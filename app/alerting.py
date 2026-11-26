from __future__ import annotations

import asyncio
import fnmatch
import hashlib
import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select

from .config import get_settings
from .db import db_session
from .k8s import k8s
from .models import AlertEvent, AlertRule, AlertSubscription, ImageBuild, IncidentActivity, NotificationChannel, NotificationRecord
from .notifications import apply_notification_template, dispatch, secret_box


logger = logging.getLogger(__name__)
settings = get_settings()
SEVERITY_LABELS = {"info": "提示", "warning": "警告", "critical": "严重"}
DISPLAY_ZONE = ZoneInfo(settings.display_timezone)


def compare(value: float, operator: str, threshold: float) -> bool:
    return {
        ">": value > threshold,
        ">=": value >= threshold,
        "<": value < threshold,
        "<=": value <= threshold,
        "==": value == threshold,
    }[operator]


def notification_due(last_notified_at: datetime | None, repeat_interval_minutes: int, now: datetime) -> bool:
    if last_notified_at is None:
        return True
    if repeat_interval_minutes == 0:
        return False
    return now - last_notified_at >= timedelta(minutes=repeat_interval_minutes)


def is_completed_job_pod(pod: dict) -> bool:
    """Completed Job pods are expected to leave Running and must not page."""
    return pod.get("owner_kind") == "Job" and pod.get("phase") == "Succeeded"


def alert_target(point: dict) -> str:
    """Nodes are cluster-scoped and must never display the internal namespace."""
    if point["kind"] == "Node":
        return point["target"]
    return f"{point['namespace']}/{point['target']}"


def format_event_time(value: datetime) -> str:
    """Persisted alert timestamps are UTC-naive; render them in the platform timezone."""
    aware = value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
    return aware.astimezone(DISPLAY_ZONE).strftime("%Y-%m-%d %H:%M:%S")


class AlertEngine:
    def __init__(self) -> None:
        self.first_seen: dict[str, datetime] = {}
        self.running = False

    def collect(self) -> list[dict]:
        points = []
        for pod in k8s.list_pods():
            base = {"namespace": pod["namespace"], "target": pod["name"], "kind": "Pod"}
            points.extend([
                {**base, "metric": "pod_cpu_percent", "value": pod["cpu_percent"]},
                {**base, "metric": "pod_memory_percent", "value": pod["memory_percent"]},
                {**base, "metric": "pod_restarts", "value": float(pod["restarts"])},
            ])
            # A completed Job pod is expected to leave Running; it is not an outage.
            # Failed Job pods remain eligible so a separate failure-oriented rule can catch them.
            if not is_completed_job_pod(pod):
                points.append({
                    **base,
                    "metric": "pod_not_running",
                    "value": 0.0 if pod["phase"] == "Running" else 1.0,
                })
        for node in k8s.list_nodes():
            base = {"namespace": "_cluster", "target": node["name"], "kind": "Node"}
            points.extend([
                {**base, "metric": "node_cpu_percent", "value": node["cpu_percent"]},
                {**base, "metric": "node_memory_percent", "value": node["memory_percent"]},
            ])
            for disk in node.get("disk_mounts", []):
                mount_path = disk.get("path")
                if mount_path and isinstance(disk.get("percent"), (int, float)):
                    points.append({
                        **base,
                        "target": f"{node['name']}:{mount_path}",
                        "metric": "node_disk_percent",
                        "value": float(disk["percent"]),
                    })
        return points

    async def evaluate(self, points: list[dict] | None = None) -> None:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        points = points if points is not None else self.collect()
        firing_fingerprints: set[str] = set()
        pending_notifications: list[tuple[NotificationChannel, str, str, int, dict, dict]] = []
        with db_session() as db:
            rules = db.scalars(select(AlertRule).where(AlertRule.enabled.is_(True))).all()
            channels = {c.id: c for c in db.scalars(select(NotificationChannel).where(NotificationChannel.enabled.is_(True))).all()}
            for rule in rules:
                for point in points:
                    if point["metric"] != rule.metric:
                        continue
                    if rule.namespace != "*" and point["namespace"] != rule.namespace:
                        continue
                    if not fnmatch.fnmatch(point["target"], rule.target_pattern):
                        continue
                    raw = f"{rule.id}:{point['namespace']}:{point['target']}:{point['metric']}"
                    fingerprint = hashlib.sha256(raw.encode()).hexdigest()
                    if not compare(point["value"], rule.operator, rule.threshold):
                        self.first_seen.pop(fingerprint, None)
                        continue
                    firing_fingerprints.add(fingerprint)
                    first = self.first_seen.setdefault(fingerprint, now)
                    if (now - first).total_seconds() < rule.duration_seconds:
                        continue
                    event = db.scalar(select(AlertEvent).where(
                        AlertEvent.fingerprint == fingerprint,
                        AlertEvent.status.in_(["firing", "acknowledged"]),
                    ))
                    title = f"[{SEVERITY_LABELS.get(rule.severity, rule.severity)}] {rule.name}"
                    message = (
                        f"资源类型: {point['kind']}\n资源名称: {point['target']}\n"
                        f"指标: {rule.metric}\n当前值: {point['value']:.2f}\n"
                        f"触发条件: {rule.operator} {rule.threshold}"
                    )
                    should_notify = False
                    if not event:
                        event = AlertEvent(
                            rule_id=rule.id, fingerprint=fingerprint, title=title, message=message,
                            severity=rule.severity, target=alert_target(point), value=point["value"],
                            started_at=first,
                        )
                        db.add(event)
                        should_notify = True
                    else:
                        event.value = point["value"]
                        event.message = message
                        should_notify = notification_due(event.last_notified_at, rule.repeat_interval_minutes, now)
                    if should_notify:
                        db.flush()
                        channel_ids = set(rule.channel_ids or [])
                        subscriptions = db.scalars(select(AlertSubscription).where(
                            AlertSubscription.enabled.is_(True),
                            (AlertSubscription.rule_id.is_(None) | (AlertSubscription.rule_id == rule.id)),
                        )).all()
                        for subscription in subscriptions:
                            channel_ids.update(subscription.channel_ids or [])
                        for channel_id in channel_ids:
                            channel = channels.get(channel_id)
                            if channel:
                                config = apply_notification_template(db, secret_box.decrypt(channel.config_encrypted))
                                variables = {
                                    "severity": SEVERITY_LABELS.get(rule.severity, rule.severity),
                                    "rule_name": rule.name,
                                    "target": alert_target(point),
                                    "namespace": "" if point["kind"] == "Node" else point["namespace"],
                                    "resource_name": point["target"],
                                    "resource_kind": point["kind"],
                                    "metric": rule.metric,
                                    "value": f"{point['value']:.2f}",
                                    "operator": rule.operator,
                                    "threshold": rule.threshold,
                                    "started_at": format_event_time(event.started_at),
                                }
                                pending_notifications.append((channel, title, message, event.id, config, variables))
            # Only events owned by an alert rule participate in automatic recovery.
            # Manually recorded incidents and clearly labelled demo incidents have no
            # rule_id and remain available until an operator updates them.
            active_events = db.scalars(select(AlertEvent).where(
                AlertEvent.rule_id.is_not(None),
                AlertEvent.status.in_(["firing", "acknowledged"]),
            )).all()
            for event in active_events:
                if event.fingerprint not in firing_fingerprints:
                    previous_status = event.status
                    event.status = "resolved"
                    event.resolved_at = now
                    event.resolved_by = "system"
                    event.updated_at = now
                    db.add(IncidentActivity(
                        event_id=event.id, action="auto_resolve", from_status=previous_status,
                        to_status="resolved", assignee=event.assignee or "",
                        note="监控指标已恢复正常", operator="system",
                    ))
        for channel, title, message, event_id, config, variables in pending_notifications:
            status = "sent"
            error = ""
            try:
                await dispatch(channel.channel_type, config, title, message, variables)
            except Exception as exc:
                status = "failed"
                error = str(exc)
                logger.exception("notification failed: %s", channel.name)
            with db_session() as db:
                db.add(NotificationRecord(
                    channel_id=channel.id, channel_name=channel.name, channel_type=channel.channel_type,
                    event_id=event_id, title=title, message=message, status=status, error=error,
                ))
                if status == "sent":
                    event = db.get(AlertEvent, event_id)
                    if event:
                        event.last_notified_at = now

    def sync_builds(self) -> None:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        with db_session() as db:
            builds = db.scalars(select(ImageBuild).where(ImageBuild.status.in_(["pending", "running"]))).all()
            for build in builds:
                if not build.job_name:
                    continue
                try:
                    status = k8s.build_status(build.namespace, build.job_name)
                    build.status = status["status"]
                    build.message = status["logs"][-20000:]
                    if build.status in {"succeeded", "failed"}:
                        build.finished_at = now
                except Exception as exc:
                    build.message = str(exc)

    async def loop(self) -> None:
        self.running = True
        while self.running:
            try:
                await asyncio.to_thread(self.sync_builds)
                points = await asyncio.to_thread(self.collect)
                await self.evaluate(points)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("alert loop failed")
            await asyncio.sleep(settings.poll_interval_seconds)


alert_engine = AlertEngine()
