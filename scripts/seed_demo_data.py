"""Create an idempotent, clearly labelled data set for visual acceptance.

The script only deletes/recreates records carrying the ``demo:`` fingerprint or
``[演示]`` marker. Real platform data is never modified.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select

from app.db import SessionLocal
from app.models import (
    AlertEvent,
    IncidentActivity,
    NotificationRecord,
    OnCallShift,
    User,
)


DEMO_MARKER = "[演示]"


def month_day(year: int, month: int, day: int, hour: int = 0) -> datetime:
    """Return a valid date in the current month, folding large days forward."""
    return datetime(year, month, 1) + timedelta(days=day - 1, hours=hour)


def seed() -> dict[str, int]:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    year, month = now.year, now.month
    with SessionLocal() as db:
        users = db.scalars(
            select(User).where(User.is_active.is_(True)).order_by(User.is_admin.desc(), User.id)
        ).all()
        if not users:
            raise RuntimeError("没有可用人员，无法创建演示排班")
        assignees = users[: min(len(users), 6)]

        demo_event_ids = list(db.scalars(
            select(AlertEvent.id).where(AlertEvent.fingerprint.like("demo:%"))
        ).all())
        if demo_event_ids:
            db.execute(delete(IncidentActivity).where(IncidentActivity.event_id.in_(demo_event_ids)))
            db.execute(delete(NotificationRecord).where(NotificationRecord.event_id.in_(demo_event_ids)))
            db.execute(delete(AlertEvent).where(AlertEvent.id.in_(demo_event_ids)))
        db.execute(delete(OnCallShift).where(OnCallShift.note.like(f"{DEMO_MARKER}%")))
        db.execute(delete(NotificationRecord).where(NotificationRecord.title.like(f"{DEMO_MARKER}%")))

        shifts: list[OnCallShift] = []
        shift_specs = [
            (2, 9, 18, 0, "白班主值"), (2, 18, 24, 1, "夜班备值"),
            (3, 9, 18, 1, "白班主值"), (3, 9, 18, 0, "发布保障"),
            (6, 8, 20, 0, "周末值守"), (6, 8, 20, 1, "业务备班"),
            (10, 9, 18, 0, "平台主值"), (10, 9, 18, 1, "应用支持"),
            (10, 12, 21, 2, "数据库支持"), (10, 18, 24, 3, "夜间备值"),
            (14, 9, 18, 1, "白班主值"), (15, 9, 18, 0, "白班主值"),
            (18, 18, 32, 1, "跨日夜班"), (21, 8, 20, 0, "周末值守"),
            (24, 9, 18, 1, "变更窗口保障"), (27, 9, 18, 0, "月末主值"),
        ]
        for day, start_hour, end_hour, user_offset, label in shift_specs:
            start = month_day(year, month, day, start_hour)
            end = month_day(year, month, day, end_hour)
            if end <= start:
                end += timedelta(days=1)
            user = assignees[user_offset % len(assignees)]
            shifts.append(OnCallShift(
                user_id=user.id, starts_at=start, ends_at=end,
                note=f"{DEMO_MARKER} {label}", created_by="demo-seeder",
            ))
        db.add_all(shifts)

        incident_specs = [
            ("critical", "firing", "生产 API 错误率升高", "prod/api-gateway", 12.7, "", "等待应用负责人确认"),
            ("warning", "firing", "节点磁盘使用率超过阈值", "worker-02:/data", 91.4, "", "持续观察磁盘增长趋势"),
            ("critical", "acknowledged", "订单服务 Pod 频繁重启", "prod/order-api-7d8c9", 8, "zhangyang", "已定位启动探针配置，处理中"),
            ("warning", "acknowledged", "消息队列消费延迟", "middleware/mq-consumer", 68, "admin", "已扩容消费者并观察积压下降"),
            ("warning", "resolved", "节点内存使用率过高", "worker-04", 87.2, "admin", "迁移工作负载后恢复正常"),
            ("info", "resolved", "证书到期巡检提醒", "ingress/prod-tls", 14, "zhangyang", "证书已完成更新"),
        ]
        events: list[AlertEvent] = []
        for index, (severity, status, title, target, value, assignee, note) in enumerate(incident_specs, 1):
            started = now - timedelta(hours=index * 5)
            event = AlertEvent(
                rule_id=None,
                fingerprint=f"demo:incident:{index}",
                title=f"{DEMO_MARKER} {title}",
                message=f"{DEMO_MARKER} 用于界面验收的模拟故障；目标 {target}，当前值 {value}。",
                severity=severity,
                status=status,
                target=target,
                value=float(value),
                started_at=started,
                assignee=assignee,
                handling_note=note,
                acknowledged_at=started + timedelta(minutes=18) if status != "firing" else None,
                resolved_at=started + timedelta(hours=2) if status == "resolved" else None,
                resolved_by=assignee if status == "resolved" else "",
                updated_at=now - timedelta(minutes=index * 7),
            )
            db.add(event)
            db.flush()
            events.append(event)
            db.add(IncidentActivity(
                event_id=event.id, action="create", from_status="", to_status="firing",
                assignee="", note="监控触发演示事件", operator="demo-seeder",
                created_at=started,
            ))
            if status != "firing":
                db.add(IncidentActivity(
                    event_id=event.id,
                    action="resolve" if status == "resolved" else "acknowledge",
                    from_status="firing", to_status=status, assignee=assignee,
                    note=note, operator=assignee or "admin",
                    created_at=event.resolved_at or event.acknowledged_at or started,
                ))

        record_specs = [
            (0, "邮件", "email", "sent", ""),
            (1, "飞书机器人", "feishu", "sent", ""),
            (2, "钉钉机器人", "dingtalk", "failed", "演示：Webhook 返回 401，请检查签名"),
            (3, "企业微信", "wecom", "sent", ""),
            (4, "电话网关", "phone", "failed", "演示：被叫号码暂时无法接通"),
            (5, "邮件", "email", "sent", ""),
        ]
        records = []
        for offset, channel_name, channel_type, status, error in record_specs:
            event = events[offset]
            records.append(NotificationRecord(
                channel_id=None, channel_name=f"{DEMO_MARKER} {channel_name}",
                channel_type=channel_type, event_id=event.id,
                title=event.title, message=event.message, status=status, error=error,
                created_at=event.started_at + timedelta(minutes=2),
            ))
        db.add_all(records)
        db.commit()
        return {
            "on_call_shifts": len(shifts),
            "incidents": len(events),
            "incident_activities": len(events) + sum(1 for item in events if item.status != "firing"),
            "notification_records": len(records),
        }


if __name__ == "__main__":
    result = seed()
    print("演示数据已写入：" + ", ".join(f"{key}={value}" for key, value in result.items()))
