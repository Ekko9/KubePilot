from __future__ import annotations

import asyncio
import difflib
import hashlib
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import yaml
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from kubernetes.client import ApiException
from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, Gauge, generate_latest
from sqlalchemy import delete as sa_delete, desc, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import __version__
from .alerting import alert_engine
from .config import get_settings
from .db import Base, SessionLocal, engine, ensure_mysql_database, ensure_schema_upgrades, get_db
from .k8s import EDITABLE_RESOURCE_TYPES, k8s
from .models import AlertEvent, AlertRule, AlertRuleTemplate, AlertSubscription, AuditLog, BuildConfiguration, DeploymentRecord, ImageBuild, IncidentActivity, NotificationChannel, NotificationRecord, NotificationTemplate, OnCallShift, ResourceRevision, User
from .notifications import apply_notification_template, dispatch, secret_box
from .permissions import ALL_MENU_CODES, ROLE_DEFAULT_PERMISSIONS, ROLE_LABELS, effective_menu_permissions, normalize_role, permission_catalog
from .schemas import AlertRuleCreate, AlertRuleTemplateCreate, AlertSubscriptionCreate, AuditLogBatchDelete, BuildConfigurationCreate, BuildRequest, BuildRunRequest, ChangePasswordRequest, ChannelCreate, DeployRequest, DiskMonitorConfig, IncidentUpdate, LoginRequest, MenuPermissionUpdate, NotificationRecordBatchDelete, NotificationTemplateCreate, OnCallShiftCreate, ResourceRollbackRequest, ResourceSaveRequest, RollbackRequest, ScaleRequest, TokenResponse, UserCreate, UserUpdate
from .security import create_access_token, get_authenticated_user, get_current_user, hash_password, require_admin, require_any_menu, require_menu, verify_password


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger(__name__)
settings = get_settings()
display_zone = ZoneInfo(settings.display_timezone)
base_dir = Path(__file__).resolve().parent


def static_asset_version() -> str:
    digest = hashlib.sha256()
    for path in (base_dir / "static" / "app.css", base_dir / "static" / "app.js"):
        digest.update(path.read_bytes())
    return digest.hexdigest()[:12]


STATIC_ASSET_VERSION = static_asset_version()

DEFAULT_NOTIFICATION_TEMPLATES = (
    {
        "name": "邮件标准告警（内置）",
        "channel_type": "email",
        "title_template": "[KubePilot][{severity}] {rule_name}",
        "message_template": "告警对象：{target}\n触发时间：{started_at}\n\n{message}",
    },
    {
        "name": "飞书标准告警（内置）",
        "channel_type": "feishu",
        "title_template": "[KubePilot][{severity}] {rule_name}",
        "message_template": "告警对象：{target}\n触发时间：{started_at}\n\n{message}",
    },
    {
        "name": "钉钉标准告警（内置）",
        "channel_type": "dingtalk",
        "title_template": "[KubePilot][{severity}] {rule_name}",
        "message_template": "告警对象：{target}\n触发时间：{started_at}\n\n{message}",
    },
    {
        "name": "企业微信标准告警（内置）",
        "channel_type": "wecom",
        "title_template": "[KubePilot][{severity}] {rule_name}",
        "message_template": "告警对象：{target}\n触发时间：{started_at}\n\n{message}",
    },
    {
        "name": "电话语音告警（内置）",
        "channel_type": "phone_webhook",
        "title_template": "{severity}告警：{rule_name}",
        "message_template": (
            "{resource_kind}{resource_name}触发{rule_name}，当前值{value}，"
            "阈值{operator}{threshold}，请及时处理。"
        ),
    },
)

dashboard_access = require_menu("dashboard")
dashboard_alert_access = require_any_menu("dashboard", "alert-events")
pods_access = require_menu("pods")
pod_read_access = require_any_menu("pods", "resources")
nodes_access = require_menu("nodes")
resources_access = require_menu("resources")
workloads_access = require_menu("workloads")
release_history_access = require_menu("release-history")
build_configs_access = require_menu("build-configs")
build_history_access = require_menu("build-history")
alerts_access = require_menu("alerts")
alert_events_access = require_menu("alert-events")
rule_templates_access = require_menu("rule-templates")
subscriptions_access = require_menu("alert-subscriptions")
incidents_access = require_menu("incidents")
channels_access = require_menu("settings")
notification_templates_access = require_menu("notification-templates")
notification_records_access = require_menu("notification-records")
on_call_access = require_menu("on-call")
namespace_access = require_any_menu(
    "pods", "resources", "workloads", "alerts", "build-configs", "build-history",
)


def audit(db: Session, request: Request, user: User, action: str, resource: str, detail: dict | None = None) -> None:
    db.add(AuditLog(
        username=user.username,
        display_name=actor_name(user),
        action=action,
        resource=resource,
        detail=detail or {},
    ))


def audit_identity(
    db: Session, request: Request, username: str, action: str,
    resource: str = "", detail: dict | None = None,
) -> None:
    db.add(AuditLog(
        username=username[:80] or "unknown",
        display_name=username[:120] or "unknown",
        action=action,
        resource=resource,
        detail=detail or {},
    ))


def actor_name(user: User) -> str:
    return user.display_name or user.username


def auth_response(user: User) -> dict[str, Any]:
    role = normalize_role(user.role, user.is_admin)
    return {
        "username": user.username,
        "display_name": actor_name(user),
        "email": user.email,
        "is_admin": role == "admin",
        "role": role,
        "role_name": ROLE_LABELS[role],
        "menu_permissions": effective_menu_permissions(user),
        "must_change_password": user.must_change_password,
    }


def user_response(user: User) -> dict[str, Any]:
    role = normalize_role(user.role, user.is_admin)
    return {
        "id": user.id, "username": user.username, "display_name": actor_name(user),
        "email": user.email, "phone": user.phone,
        "role": role, "role_name": ROLE_LABELS[role], "is_admin": role == "admin",
        "is_active": user.is_active, "must_change_password": user.must_change_password,
        "is_builtin_admin": user.username == settings.admin_username, "created_at": user.created_at,
    }


def validate_rule_template(db: Session, payload: AlertRuleCreate) -> AlertRuleTemplate:
    if not payload.template_id:
        raise HTTPException(status_code=400, detail="告警规则必须关联规则模板")
    template = db.get(AlertRuleTemplate, payload.template_id)
    if not template:
        raise HTTPException(status_code=400, detail="关联的规则模板不存在")
    if template.metric != payload.metric:
        raise HTTPException(status_code=400, detail="告警指标必须与关联规则模板一致")
    return template


def validate_channel_template(db: Session, payload: ChannelCreate) -> NotificationTemplate:
    template = db.get(NotificationTemplate, payload.config.get("notification_template_id"))
    if not template:
        raise HTTPException(status_code=400, detail="关联的通知模板不存在")
    if template.channel_type != payload.channel_type:
        raise HTTPException(status_code=400, detail="通知模板类型必须与渠道类型一致")
    return template


def initialize_database() -> None:
    ensure_mysql_database()
    Base.metadata.create_all(bind=engine)
    ensure_schema_upgrades()
    with SessionLocal() as db:
        changed = False
        user = db.scalar(select(User).where(User.username == settings.admin_username))
        if not user:
            db.add(User(
                username=settings.admin_username,
                display_name="系统管理员",
                password_hash=hash_password(settings.admin_password),
                email=settings.admin_email,
                is_admin=True, role="admin",
                must_change_password=False,
            ))
            changed = True
        else:
            role = normalize_role(user.role, user.is_admin)
            if not user.display_name or (user.username == settings.admin_username and user.display_name == user.username):
                user.display_name = "系统管理员" if user.username == settings.admin_username else user.username
                changed = True
            if user.role != role or user.is_admin != (role == "admin"):
                user.role = role
                user.is_admin = role == "admin"
                changed = True
        for template in DEFAULT_NOTIFICATION_TEMPLATES:
            existing = db.scalar(select(NotificationTemplate).where(NotificationTemplate.name == template["name"]))
            if not existing:
                db.add(NotificationTemplate(**template, created_by="system"))
                changed = True
            elif existing.created_by == "system":
                for key in ("channel_type", "title_template", "message_template"):
                    if getattr(existing, key) != template[key]:
                        setattr(existing, key, template[key])
                        changed = True
        if changed:
            db.commit()


def cleanup_orphan_build_jobs() -> int:
    """Remove platform-owned build Jobs whose database history is gone."""
    with SessionLocal() as db:
        existing_ids = set(db.scalars(select(ImageBuild.id)).all())
    removed = 0
    for job in k8s.list_managed_build_jobs():
        if job["build_id"] in existing_ids:
            continue
        try:
            k8s.delete_build_job(job["namespace"], job["job_name"])
            removed += 1
        except Exception:
            logger.exception(
                "failed to clean orphan build job %s/%s",
                job["namespace"], job["job_name"],
            )
    if removed:
        logger.info("removed %s orphan build Jobs", removed)
    return removed


@asynccontextmanager
async def lifespan(_: FastAPI):
    await asyncio.to_thread(initialize_database)
    try:
        await asyncio.to_thread(cleanup_orphan_build_jobs)
    except Exception:
        logger.exception("orphan build Job cleanup failed during startup")
    task = asyncio.create_task(alert_engine.loop(), name="alert-engine")
    yield
    alert_engine.running = False
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


app = FastAPI(title=settings.app_name, version=__version__, lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/static", StaticFiles(directory=base_dir / "static"), name="static")
templates = Jinja2Templates(directory=base_dir / "templates")


@app.exception_handler(ApiException)
async def kubernetes_error(_: Request, exc: ApiException):
    return Response(
        content=f'{{"detail":"Kubernetes API 错误: {exc.reason or exc.body}"}}',
        status_code=exc.status or 500,
        media_type="application/json",
    )


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    response = templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "app_name": settings.app_name,
            "version": __version__,
            "asset_version": STATIC_ASSET_VERSION,
            "display_timezone": settings.display_timezone,
        },
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/healthz")
def healthz():
    return {"status": "ok", "version": __version__}


@app.get("/readyz")
def readyz(db: Session = Depends(get_db)):
    db.execute(text("SELECT 1"))
    k8s.list_namespaces()
    return {"status": "ready"}


@app.post("/api/auth/login", response_model=TokenResponse)
def login(payload: LoginRequest, request: Request, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.username == payload.username, User.is_active.is_(True)))
    if not user or not verify_password(payload.password, user.password_hash):
        audit_identity(db, request, payload.username, "auth.login.failed", payload.username, {"result": "failed"})
        db.commit()
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    audit(db, request, user, "auth.login", user.username, {"result": "success"})
    db.commit()
    return TokenResponse(
        access_token=create_access_token(user.username), **auth_response(user),
    )


@app.post("/api/auth/change-password")
def change_password(
    payload: ChangePasswordRequest, request: Request,
    user: User = Depends(get_authenticated_user), db: Session = Depends(get_db),
):
    if not verify_password(payload.current_password, user.password_hash):
        audit(db, request, user, "auth.password_change.failed", user.username, {"result": "failed"})
        db.commit()
        raise HTTPException(status_code=400, detail="当前密码不正确")
    user.password_hash = hash_password(payload.new_password)
    user.must_change_password = False
    audit(db, request, user, "auth.password_change", user.username, {"result": "success"})
    db.commit()
    return {"changed": True, **auth_response(user)}


@app.post("/api/auth/logout")
def logout(
    request: Request, user: User = Depends(get_authenticated_user),
    db: Session = Depends(get_db),
):
    audit(db, request, user, "auth.logout", user.username, {"result": "success"})
    db.commit()
    return {"logged_out": True}


@app.get("/api/me")
def me(user: User = Depends(get_authenticated_user)):
    return auth_response(user)


@app.get("/api/overview")
def overview(_: User = Depends(dashboard_access), db: Session = Depends(get_db)):
    nodes = k8s.list_nodes()
    pods = k8s.list_pods()
    deployments = k8s.list_deployments()
    return {
        "nodes": len(nodes),
        "nodes_ready": sum(1 for x in nodes if x["ready"] == "True"),
        "pods": len(pods),
        "pods_running": sum(1 for x in pods if x["phase"] == "Running"),
        "deployments": len(deployments),
        "alerts_firing": len(db.scalars(select(AlertEvent).where(AlertEvent.status == "firing")).all()),
        "builds_running": len(db.scalars(select(ImageBuild).where(ImageBuild.status.in_(["pending", "running"]))).all()),
    }


@app.get("/api/namespaces")
def namespaces(_: User = Depends(namespace_access)):
    return k8s.list_namespaces()


@app.get("/api/nodes")
def nodes(_: User = Depends(nodes_access)):
    return k8s.list_nodes()


@app.get("/api/node-disk-config")
def node_disk_config(_: User = Depends(require_admin)):
    return {"paths": k8s.get_disk_monitor_paths()}


@app.put("/api/node-disk-config")
def update_node_disk_config(payload: DiskMonitorConfig, request: Request, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    paths = k8s.set_disk_monitor_paths(payload.paths)
    audit(db, request, user, "node_disk_config.update", ",".join(paths), {"paths": paths})
    db.commit()
    return {"paths": paths}


@app.get("/api/pods")
def pods(namespace: str = "", _: User = Depends(pods_access)):
    return k8s.list_pods(namespace)


@app.get("/api/pods/{namespace}/{name}/logs")
def pod_logs(
    namespace: str, name: str, container: str = "", tail_lines: int = 500,
    since_seconds: int = 0, previous: bool = False, timestamps: bool = True,
    _: User = Depends(pod_read_access),
):
    try:
        result = k8s.pod_logs(
            namespace, name, container, tail_lines,
            since_seconds, previous, timestamps,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "namespace": namespace,
        "pod": name,
        **result,
    }


@app.post("/api/pods/{namespace}/{name}/restart")
def restart_pod(
    namespace: str, name: str, request: Request,
    user: User = Depends(pods_access), db: Session = Depends(get_db),
):
    try:
        result = k8s.restart_pod(namespace, name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    audit(db, request, user, "pod.restart", f"{namespace}/{name}", result)
    db.commit()
    return result


@app.get("/api/deployments")
def deployments(namespace: str = "", _: User = Depends(workloads_access)):
    return k8s.list_deployments(namespace)


@app.get("/api/deployments/{namespace}/{name}/revisions")
def deployment_revisions(namespace: str, name: str, _: User = Depends(workloads_access)):
    return k8s.deployment_revisions(namespace, name)


@app.post("/api/deployments")
def deploy(payload: DeployRequest, request: Request, user: User = Depends(workloads_access), db: Session = Depends(get_db)):
    try:
        result = k8s.deploy(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    record = DeploymentRecord(
        namespace=payload.namespace, workload=payload.name, image=payload.image,
        replicas=result["replicas"], status="running", operator=actor_name(user),
    )
    db.add(record)
    db.flush()
    audit(db, request, user, "deployment.apply", f"{payload.namespace}/{payload.name}", payload.model_dump())
    db.commit()
    return {**result, "execution_id": record.id, "status": record.status}


@app.post("/api/deployments/{namespace}/{name}/rollback")
def rollback(namespace: str, name: str, payload: RollbackRequest, request: Request, user: User = Depends(workloads_access), db: Session = Depends(get_db)):
    try:
        result = k8s.rollback(namespace, name, payload.revision)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    record = DeploymentRecord(
        namespace=namespace, workload=name, image=result["target_image"],
        replicas=result["replicas"], revision=result["target_revision"],
        status="running", strategy="Rollback", operator=actor_name(user),
    )
    db.add(record)
    db.flush()
    audit(db, request, user, "deployment.rollback", f"{namespace}/{name}", result)
    db.commit()
    return {**result, "execution_id": record.id, "status": record.status}


@app.post("/api/deployments/{namespace}/{name}/scale")
def scale(namespace: str, name: str, payload: ScaleRequest, request: Request, user: User = Depends(workloads_access), db: Session = Depends(get_db)):
    result = k8s.scale(namespace, name, payload.replicas)
    audit(db, request, user, "deployment.scale", f"{namespace}/{name}", result)
    db.commit()
    return result


@app.post("/api/deployments/{namespace}/{name}/restart")
def restart(namespace: str, name: str, request: Request, user: User = Depends(workloads_access), db: Session = Depends(get_db)):
    result = k8s.restart(namespace, name)
    audit(db, request, user, "deployment.restart", f"{namespace}/{name}", result)
    db.commit()
    return result


@app.get("/api/deployment-history")
def deployment_history(limit: int = 100, _: User = Depends(release_history_access), db: Session = Depends(get_db)):
    records = list(db.scalars(
        select(DeploymentRecord).order_by(desc(DeploymentRecord.id)).limit(min(limit, 500))
    ).all())
    _repair_legacy_rollback_records(records, db)
    for record in records[:20]:
        if record.status != "running":
            continue
        try:
            _refresh_deployment_execution(record, db)
        except ApiException:
            continue
    return records


def _repair_legacy_rollback_records(records: list[DeploymentRecord], db: Session) -> None:
    legacy = [record for record in records if (record.image or "").lower() == "rollback"]
    if not legacy:
        return
    audits = list(db.scalars(
        select(AuditLog)
        .where(AuditLog.action == "deployment.rollback")
        .order_by(desc(AuditLog.id))
    ).all())
    revision_cache: dict[tuple[str, str], list[dict[str, Any]]] = {}
    changed = False
    for record in legacy:
        resource = f"{record.namespace}/{record.workload}"
        matching_audits = [audit for audit in audits if audit.resource == resource]
        audit = min(
            matching_audits,
            key=lambda item: abs((item.created_at - record.created_at).total_seconds()),
            default=None,
        )
        if not audit or abs((audit.created_at - record.created_at).total_seconds()) > 300:
            continue
        record.strategy = "Rollback"
        detail = audit.detail or {}
        target_revision = int(detail.get("target_revision") or record.revision or 0)
        from_revision = int(detail.get("from_revision") or 0)
        key = (record.namespace, record.workload)
        if key not in revision_cache:
            try:
                revision_cache[key] = k8s.deployment_revisions(*key)["items"]
            except ApiException:
                revision_cache[key] = []
        revisions = revision_cache[key]
        target = next(
            (item for item in revisions if item["revision"] == target_revision),
            None,
        )
        if not target and from_revision:
            # Kubernetes rollback creates a new revision containing the target
            # template. If the original target ReplicaSet has been pruned, the
            # first revision after from_revision is the recoverable equivalent.
            newer = [item for item in revisions if item["revision"] > from_revision]
            target = min(newer, key=lambda item: item["revision"], default=None)
        if target and target.get("image"):
            record.image = target["image"]
            changed = True
    if changed:
        db.commit()


def _refresh_deployment_execution(record: DeploymentRecord, db: Session) -> dict[str, Any]:
    result = k8s.deployment_rollout_status(
        record.namespace, record.workload, record.image,
    )
    record.status = result["status"]
    record.revision = result["revision"] or record.revision
    summary = (
        f"副本 {result['ready_replicas']}/{result['desired_replicas']}，"
        f"已更新 {result['updated_replicas']}，可用 {result['available_replicas']}"
    )
    snapshot_lines = [summary]
    for condition in result["conditions"]:
        snapshot_lines.append(
            f"[条件] {condition['type']}={condition['status']} "
            f"{condition['reason']} {condition['message']}".strip()
        )
    for event in result["events"]:
        snapshot_lines.append(
            f"[事件] {event['created_at']} {event['type']} {event['reason']} "
            f"{event['object']}: {event['message']}"
        )
    if result["container_logs"]:
        snapshot_lines.extend(["", "[主容器日志]", result["container_logs"]])
    record.message = "\n".join(snapshot_lines)[-60000:]
    db.commit()
    return {
        **result,
        "execution_id": record.id,
        "strategy": record.strategy,
        "operator": record.operator,
        "created_at": record.created_at,
        "message": record.message,
    }


def _archived_deployment_execution(record: DeploymentRecord) -> dict[str, Any]:
    return {
        "execution_id": record.id,
        "status": record.status,
        "namespace": record.namespace,
        "name": record.workload,
        "image": record.image,
        "expected_image": record.image,
        "revision": record.revision or 0,
        "desired_replicas": record.replicas,
        "updated_replicas": record.replicas if record.status == "succeeded" else 0,
        "ready_replicas": record.replicas if record.status == "succeeded" else 0,
        "available_replicas": record.replicas if record.status == "succeeded" else 0,
        "unavailable_replicas": 0,
        "conditions": [],
        "pods": [],
        "events": [],
        "container_logs": record.message or "该历史记录没有保存日志快照。",
        "strategy": record.strategy,
        "operator": record.operator,
        "created_at": record.created_at,
        "message": record.message,
    }


@app.get("/api/deployment-history/{execution_id}/status")
def deployment_execution_status(
    execution_id: int,
    _: User = Depends(release_history_access),
    db: Session = Depends(get_db),
):
    record = db.get(DeploymentRecord, execution_id)
    if not record:
        raise HTTPException(status_code=404, detail="发布记录不存在")
    if record.status != "running":
        return _archived_deployment_execution(record)
    return _refresh_deployment_execution(record, db)


def _start_build(payload: BuildRequest, request: Request, user: User, db: Session, config_id: int | None = None) -> ImageBuild:
    build = ImageBuild(
        name=payload.name, git_url=payload.git_url, git_ref=payload.git_ref,
        context_dir=payload.context_dir, dockerfile=payload.dockerfile,
        destination=payload.destination, namespace=payload.namespace, build_args=payload.build_args,
        build_steps=[step.model_dump() for step in payload.build_steps],
        config_id=config_id, created_by=actor_name(user),
    )
    db.add(build)
    db.flush()
    try:
        payload.destination = (payload.destination
                               .replace("${BUILD_NUMBER}", str(build.id))
                               .replace("${GIT_REF}", payload.git_ref)
                               .replace("${BRANCH_NAME}", payload.git_ref))
        build.destination = payload.destination
        build.job_name = k8s.create_build_job(payload, build.id)
        build.status = "pending"
    except Exception as exc:
        build.status = "failed"
        build.message = str(exc)
        db.commit()
        raise
    audit(db, request, user, "image.build", payload.destination, {"job_name": build.job_name, "git_url": payload.git_url, "git_ref": payload.git_ref})
    db.commit()
    db.refresh(build)
    return build


@app.post("/api/builds")
def create_build(payload: BuildRequest, request: Request, user: User = Depends(build_configs_access), db: Session = Depends(get_db)):
    return _start_build(payload, request, user, db)


@app.get("/api/builds")
def builds(limit: int = 100, _: User = Depends(build_history_access), db: Session = Depends(get_db)):
    return db.scalars(select(ImageBuild).order_by(desc(ImageBuild.id)).limit(min(limit, 500))).all()


@app.delete("/api/builds/{build_id}")
def delete_build(build_id: int, request: Request, user: User = Depends(build_history_access), db: Session = Depends(get_db)):
    build = db.get(ImageBuild, build_id)
    if not build:
        raise HTTPException(status_code=404, detail="构建记录不存在")
    _delete_build_artifacts(build)
    audit(db, request, user, "image.build.delete", str(build_id), {"job_name": build.job_name})
    db.delete(build)
    db.commit()
    return {"deleted": build_id}


def _delete_build_artifacts(build: ImageBuild) -> None:
    """Delete the Job for every build state; Job deletion cascades to Pods."""
    if build.job_name:
        k8s.delete_build_job(build.namespace, build.job_name)


def _prune_build_history(db: Session, config_id: int, retention_count: int) -> int:
    builds = list(db.scalars(
        select(ImageBuild)
        .where(ImageBuild.config_id == config_id)
        .order_by(desc(ImageBuild.id))
    ).all())
    removed = 0
    for old in builds[retention_count:]:
        if old.status not in {"succeeded", "failed"}:
            continue
        try:
            _delete_build_artifacts(old)
        except Exception:
            logger.exception(
                "failed to prune build artifacts for build %s; keeping database history",
                old.id,
            )
            continue
        db.delete(old)
        removed += 1
    return removed


@app.get("/api/builds/{build_id}")
def build_detail(build_id: int, _: User = Depends(build_history_access), db: Session = Depends(get_db)):
    build = db.get(ImageBuild, build_id)
    if not build:
        raise HTTPException(status_code=404, detail="构建记录不存在")
    if build.job_name and build.status in {"pending", "running"}:
        status = k8s.build_status(build.namespace, build.job_name)
        build.status = status["status"]
        build.message = status["logs"][-20000:]
        if build.status in {"succeeded", "failed"}:
            build.finished_at = datetime.now(timezone.utc).replace(tzinfo=None)
        db.commit()
    return build


def _build_config_payload(item: BuildConfiguration) -> dict:
    payload = {key: getattr(item, key) for key in (
        "id", "name", "description", "enabled", "git_url", "git_ref", "context_dir", "dockerfile",
        "destination", "namespace", "build_args", "build_steps", "timeout_seconds",
        "allow_concurrent", "retention_count", "created_by", "created_at", "updated_at",
    )}
    payload["has_git_credentials"] = bool(item.git_auth_secret_name or item.git_secret_name)
    payload["has_registry_credentials"] = bool(item.registry_secret_name)
    return payload


def _apply_build_credentials(item: BuildConfiguration, payload: BuildConfigurationCreate) -> None:
    registry_server = payload.destination.split("/", 1)[0]
    if any((payload.git_username, payload.registry_username)):
        k8s.ensure_namespace(payload.namespace)
    git_secret, registry_secret = k8s.configure_build_credentials(
        payload.namespace,
        item.id,
        registry_server=registry_server,
        git_username=payload.git_username,
        git_password=payload.git_password,
        registry_username=payload.registry_username,
        registry_password=payload.registry_password,
        clear_git=payload.clear_git_credentials,
        clear_registry=payload.clear_registry_credentials,
    )
    if git_secret:
        item.git_auth_secret_name = git_secret
        item.git_secret_name = ""
    elif payload.clear_git_credentials:
        item.git_auth_secret_name = ""
    if registry_secret:
        item.registry_secret_name = registry_secret
    elif payload.clear_registry_credentials:
        item.registry_secret_name = ""


def _build_config_values(payload: BuildConfigurationCreate) -> dict:
    return payload.model_dump(exclude={
        "git_username", "git_password", "registry_username", "registry_password",
        "clear_git_credentials", "clear_registry_credentials",
    })


@app.get("/api/build-configs")
def build_configs(_: User = Depends(build_configs_access), db: Session = Depends(get_db)):
    return [_build_config_payload(item) for item in db.scalars(select(BuildConfiguration).order_by(desc(BuildConfiguration.id))).all()]


@app.post("/api/build-configs")
def create_build_config(payload: BuildConfigurationCreate, request: Request, user: User = Depends(build_configs_access), db: Session = Depends(get_db)):
    item = BuildConfiguration(**_build_config_values(payload), created_by=actor_name(user))
    db.add(item)
    try:
        db.flush()
        _apply_build_credentials(item, payload)
        audit(db, request, user, "build_config.create", payload.name, _build_config_values(payload))
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="构建配置名称已存在") from exc
    except Exception:
        db.rollback()
        for secret_name in k8s.build_secret_names(item.id):
            try:
                k8s.delete_build_secret(payload.namespace, secret_name)
            except Exception:
                logger.exception("failed to clean build credential secret")
        raise
    db.refresh(item)
    return _build_config_payload(item)


@app.put("/api/build-configs/{config_id}")
def update_build_config(config_id: int, payload: BuildConfigurationCreate, request: Request, user: User = Depends(build_configs_access), db: Session = Depends(get_db)):
    item = db.get(BuildConfiguration, config_id)
    if not item:
        raise HTTPException(status_code=404, detail="构建配置不存在")
    old_namespace = item.namespace
    old_generated_secrets = set(k8s.build_secret_names(config_id)) & {
        item.git_auth_secret_name, item.registry_secret_name,
    }
    if payload.namespace != item.namespace and (
        ((item.git_secret_name or item.git_auth_secret_name) and not payload.git_username and not payload.clear_git_credentials) or
        (item.registry_secret_name and not payload.registry_username and not payload.clear_registry_credentials)
    ):
        raise HTTPException(status_code=400, detail="更改构建命名空间时，请重新填写已配置的凭证")
    for key, value in _build_config_values(payload).items():
        if key in {"git_secret_name", "registry_secret_name"} and not value:
            continue
        setattr(item, key, value)
    try:
        _apply_build_credentials(item, payload)
        audit(db, request, user, "build_config.update", item.name, _build_config_values(payload))
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="构建配置名称已存在") from exc
    except Exception:
        db.rollback()
        raise
    if old_namespace != item.namespace:
        for secret_name in old_generated_secrets:
            try:
                k8s.delete_build_secret(old_namespace, secret_name)
            except Exception:
                logger.exception("failed to remove old namespace build credential")
    db.refresh(item)
    return _build_config_payload(item)


@app.delete("/api/build-configs/{config_id}")
def delete_build_config(config_id: int, request: Request, user: User = Depends(build_configs_access), db: Session = Depends(get_db)):
    item = db.get(BuildConfiguration, config_id)
    if not item:
        raise HTTPException(status_code=404, detail="构建配置不存在")
    running = db.scalar(select(ImageBuild.id).where(ImageBuild.config_id == config_id, ImageBuild.status.in_(["pending", "running"])).limit(1))
    if running:
        raise HTTPException(status_code=409, detail="该配置仍有构建任务运行中")
    for generated_name in k8s.build_secret_names(config_id):
        if generated_name in {item.git_auth_secret_name, item.registry_secret_name}:
            k8s.delete_build_secret(item.namespace, generated_name)
    audit(db, request, user, "build_config.delete", item.name)
    db.execute(update(ImageBuild).where(ImageBuild.config_id == config_id).values(config_id=None))
    db.delete(item)
    db.commit()
    return {"deleted": config_id}


@app.post("/api/build-configs/{config_id}/run")
def run_build_config(config_id: int, payload: BuildRunRequest, request: Request, user: User = Depends(build_configs_access), db: Session = Depends(get_db)):
    item = db.get(BuildConfiguration, config_id)
    if not item or not item.enabled:
        raise HTTPException(status_code=404, detail="构建配置不存在或已停用")
    if not item.allow_concurrent:
        running = db.scalar(select(ImageBuild.id).where(ImageBuild.config_id == config_id, ImageBuild.status.in_(["pending", "running"])).limit(1))
        if running:
            raise HTTPException(status_code=409, detail="该构建配置已有任务运行中")
    build_payload = BuildRequest(
        name=item.name,
        git_url=item.git_url,
        git_ref=payload.git_ref or item.git_ref,
        context_dir=item.context_dir,
        dockerfile=item.dockerfile,
        destination=payload.destination or item.destination,
        namespace=item.namespace,
        git_secret_name=item.git_secret_name,
        git_auth_secret_name=item.git_auth_secret_name,
        registry_secret_name=item.registry_secret_name,
        build_args=payload.build_args if payload.build_args is not None else (item.build_args or {}),
        build_steps=payload.build_steps if payload.build_steps is not None else (item.build_steps or []),
        timeout_seconds=item.timeout_seconds,
    )
    build = _start_build(build_payload, request, user, db, config_id=config_id)
    _prune_build_history(db, config_id, item.retention_count)
    db.commit()
    return build


@app.get("/api/alert-rules")
def alert_rules(_: User = Depends(alerts_access), db: Session = Depends(get_db)):
    templates_by_id = {item.id: item.name for item in db.scalars(select(AlertRuleTemplate)).all()}
    rows = db.scalars(select(AlertRule).order_by(desc(AlertRule.id))).all()
    return [{
        "id": row.id, "name": row.name, "metric": row.metric, "operator": row.operator,
        "threshold": row.threshold, "duration_seconds": row.duration_seconds,
        "repeat_interval_minutes": row.repeat_interval_minutes,
        "severity": row.severity, "namespace": row.namespace,
        "target_pattern": row.target_pattern, "template_id": row.template_id,
        "template_name": templates_by_id.get(row.template_id, "未关联模板"),
        "channel_ids": row.channel_ids or [], "enabled": row.enabled,
        "created_at": row.created_at,
    } for row in rows]


@app.post("/api/alert-rules")
def create_alert_rule(payload: AlertRuleCreate, request: Request, user: User = Depends(alerts_access), db: Session = Depends(get_db)):
    validate_rule_template(db, payload)
    if payload.channel_ids:
        existing_ids = set(db.scalars(
            select(NotificationChannel.id).where(NotificationChannel.id.in_(payload.channel_ids))
        ).all())
        missing_ids = sorted(set(payload.channel_ids) - existing_ids)
        if missing_ids:
            raise HTTPException(status_code=400, detail=f"通知渠道不存在: {', '.join(map(str, missing_ids))}")
    rule = AlertRule(**payload.model_dump())
    db.add(rule)
    try:
        audit(db, request, user, "alert_rule.create", payload.name, payload.model_dump())
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="告警规则名称已存在") from exc
    db.refresh(rule)
    return rule


@app.put("/api/alert-rules/{rule_id}")
def update_alert_rule(rule_id: int, payload: AlertRuleCreate, request: Request, user: User = Depends(alerts_access), db: Session = Depends(get_db)):
    rule = db.get(AlertRule, rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail="规则不存在")
    validate_rule_template(db, payload)
    if payload.channel_ids:
        existing_ids = set(db.scalars(
            select(NotificationChannel.id).where(NotificationChannel.id.in_(payload.channel_ids))
        ).all())
        missing_ids = sorted(set(payload.channel_ids) - existing_ids)
        if missing_ids:
            raise HTTPException(status_code=400, detail=f"通知渠道不存在: {', '.join(map(str, missing_ids))}")
    for key, value in payload.model_dump().items():
        setattr(rule, key, value)
    try:
        audit(db, request, user, "alert_rule.update", payload.name, payload.model_dump())
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="告警规则名称已存在") from exc
    db.refresh(rule)
    return rule


@app.delete("/api/alert-rules/{rule_id}")
def delete_alert_rule(rule_id: int, request: Request, user: User = Depends(alerts_access), db: Session = Depends(get_db)):
    rule = db.get(AlertRule, rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail="规则不存在")
    audit(db, request, user, "alert_rule.delete", rule.name)
    db.execute(update(AlertEvent).where(AlertEvent.rule_id == rule_id).values(rule_id=None))
    db.execute(update(AlertSubscription).where(AlertSubscription.rule_id == rule_id).values(rule_id=None))
    db.delete(rule)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="告警规则仍被其他数据引用，暂时无法删除") from exc
    return {"deleted": rule_id}


@app.get("/api/alert-rule-templates")
def alert_rule_templates(_: User = Depends(rule_templates_access), db: Session = Depends(get_db)):
    return db.scalars(select(AlertRuleTemplate).order_by(desc(AlertRuleTemplate.id))).all()


@app.post("/api/alert-rule-templates")
def create_alert_rule_template(payload: AlertRuleTemplateCreate, request: Request, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    template = AlertRuleTemplate(**payload.model_dump())
    db.add(template)
    audit(db, request, user, "alert_rule_template.create", payload.name)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="规则模板名称已存在") from exc
    db.refresh(template)
    return template


@app.delete("/api/alert-rule-templates/{template_id}")
def delete_alert_rule_template(template_id: int, request: Request, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    template = db.get(AlertRuleTemplate, template_id)
    if not template:
        raise HTTPException(status_code=404, detail="规则模板不存在")
    usage = db.scalar(select(AlertRule).where(AlertRule.template_id == template_id).limit(1))
    if usage:
        raise HTTPException(status_code=409, detail="规则模板正在被告警规则使用，请先修改关联规则")
    audit(db, request, user, "alert_rule_template.delete", template.name)
    db.delete(template)
    db.commit()
    return {"deleted": template_id}


@app.get("/api/alert-subscriptions")
def alert_subscriptions(_: User = Depends(subscriptions_access), db: Session = Depends(get_db)):
    rows = db.scalars(select(AlertSubscription).order_by(desc(AlertSubscription.id))).all()
    return [{
        "id": row.id, "name": row.name, "user_id": row.user_id,
        "username": row.user.username if row.user else "",
        "display_name": actor_name(row.user) if row.user else "",
        "rule_id": row.rule_id, "rule_name": row.rule.name if row.rule else "全部告警",
        "channel_ids": row.channel_ids or [], "enabled": row.enabled,
    } for row in rows]


def validate_alert_subscription(db: Session, payload: AlertSubscriptionCreate) -> None:
    if not db.get(User, payload.user_id):
        raise HTTPException(status_code=400, detail="订阅人员不存在")
    if payload.rule_id and not db.get(AlertRule, payload.rule_id):
        raise HTTPException(status_code=400, detail="告警规则不存在")
    valid_channels = set(db.scalars(select(NotificationChannel.id).where(NotificationChannel.id.in_(payload.channel_ids or [-1]))).all())
    if set(payload.channel_ids) - valid_channels:
        raise HTTPException(status_code=400, detail="包含不存在的通知渠道")


@app.post("/api/alert-subscriptions")
def create_alert_subscription(payload: AlertSubscriptionCreate, request: Request, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    validate_alert_subscription(db, payload)
    subscription = AlertSubscription(**payload.model_dump())
    db.add(subscription)
    audit(db, request, user, "alert_subscription.create", payload.name)
    db.commit()
    db.refresh(subscription)
    return {"id": subscription.id, "name": subscription.name}


@app.put("/api/alert-subscriptions/{subscription_id}")
def update_alert_subscription(
    subscription_id: int, payload: AlertSubscriptionCreate, request: Request,
    user: User = Depends(require_admin), db: Session = Depends(get_db),
):
    subscription = db.get(AlertSubscription, subscription_id)
    if not subscription:
        raise HTTPException(status_code=404, detail="订阅不存在")
    validate_alert_subscription(db, payload)
    for key, value in payload.model_dump().items():
        setattr(subscription, key, value)
    audit(db, request, user, "alert_subscription.update", payload.name, {
        "user_id": payload.user_id, "rule_id": payload.rule_id,
        "channel_ids": payload.channel_ids, "enabled": payload.enabled,
    })
    db.commit()
    return {"id": subscription.id, "name": subscription.name}


@app.delete("/api/alert-subscriptions/{subscription_id}")
def delete_alert_subscription(subscription_id: int, request: Request, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    subscription = db.get(AlertSubscription, subscription_id)
    if not subscription:
        raise HTTPException(status_code=404, detail="订阅不存在")
    audit(db, request, user, "alert_subscription.delete", subscription.name)
    db.delete(subscription)
    db.commit()
    return {"deleted": subscription_id}


@app.post("/api/incidents/{event_id}/acknowledge")
def acknowledge_incident(event_id: int, request: Request, user: User = Depends(incidents_access), db: Session = Depends(get_db)):
    event = db.get(AlertEvent, event_id)
    if not event:
        raise HTTPException(status_code=404, detail="故障事件不存在")
    if event.status == "resolved":
        raise HTTPException(status_code=409, detail="已恢复事件无需确认")
    previous_status = event.status
    event.status = "acknowledged"
    event.assignee = event.assignee or actor_name(user)
    event.handling_note = event.handling_note or "已确认并开始处理"
    event.acknowledged_at = event.acknowledged_at or datetime.now(timezone.utc).replace(tzinfo=None)
    event.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
    db.add(IncidentActivity(
        event_id=event.id, action="acknowledge", from_status=previous_status,
        to_status=event.status, assignee=event.assignee,
        note=event.handling_note, operator=actor_name(user),
    ))
    audit(db, request, user, "incident.acknowledge", str(event_id), {
        "target": event.target, "assignee": event.assignee,
    })
    db.commit()
    return {"id": event_id, "status": event.status}


@app.get("/api/incidents/{event_id}")
def incident_detail(
    event_id: int, _: User = Depends(incidents_access), db: Session = Depends(get_db),
):
    event = db.get(AlertEvent, event_id)
    if not event:
        raise HTTPException(status_code=404, detail="故障事件不存在")
    activities = db.scalars(
        select(IncidentActivity).where(IncidentActivity.event_id == event_id)
        .order_by(desc(IncidentActivity.id))
    ).all()
    return {
        "id": event.id, "title": event.title, "message": event.message,
        "severity": event.severity, "status": event.status, "target": event.target,
        "value": event.value, "started_at": event.started_at,
        "resolved_at": event.resolved_at, "assignee": event.assignee or "",
        "handling_note": event.handling_note or "", "acknowledged_at": event.acknowledged_at,
        "resolved_by": event.resolved_by or "", "updated_at": event.updated_at,
        "activities": activities,
    }


@app.put("/api/incidents/{event_id}")
def update_incident(
    event_id: int, payload: IncidentUpdate, request: Request,
    user: User = Depends(incidents_access), db: Session = Depends(get_db),
):
    event = db.get(AlertEvent, event_id)
    if not event:
        raise HTTPException(status_code=404, detail="故障事件不存在")
    previous_status = event.status
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    event.status = payload.status
    event.assignee = payload.assignee
    event.handling_note = payload.note
    event.updated_at = now
    if payload.status == "acknowledged":
        event.acknowledged_at = event.acknowledged_at or now
        event.resolved_at = None
        event.resolved_by = ""
    elif payload.status == "resolved":
        event.acknowledged_at = event.acknowledged_at or now
        event.resolved_at = now
        event.resolved_by = actor_name(user)
    else:
        event.acknowledged_at = None
        event.resolved_at = None
        event.resolved_by = ""
    action = "resolve" if payload.status == "resolved" else ("reopen" if payload.status == "firing" else "update")
    db.add(IncidentActivity(
        event_id=event.id, action=action, from_status=previous_status,
        to_status=payload.status, assignee=payload.assignee,
        note=payload.note, operator=actor_name(user),
    ))
    audit(db, request, user, "incident.update", str(event_id), {
        "from_status": previous_status, "to_status": payload.status,
        "assignee": payload.assignee, "note": payload.note,
    })
    db.commit()
    return {"id": event.id, "status": event.status, "assignee": event.assignee}


@app.get("/api/notification-templates")
def notification_templates(_: User = Depends(notification_templates_access), db: Session = Depends(get_db)):
    return db.scalars(select(NotificationTemplate).order_by(desc(NotificationTemplate.id))).all()


@app.post("/api/notification-templates")
def create_notification_template(payload: NotificationTemplateCreate, request: Request, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    template = NotificationTemplate(**payload.model_dump(), created_by=actor_name(user))
    db.add(template)
    audit(db, request, user, "notification_template.create", payload.name)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="通知模板名称已存在") from exc
    db.refresh(template)
    return template


@app.delete("/api/notification-templates/{template_id}")
def delete_notification_template(template_id: int, request: Request, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    template = db.get(NotificationTemplate, template_id)
    if not template:
        raise HTTPException(status_code=404, detail="通知模板不存在")
    if template.created_by == "system":
        raise HTTPException(status_code=409, detail="内置通知模板不可删除，可新建自定义模板替代")
    for channel in db.scalars(select(NotificationChannel)).all():
        config = secret_box.decrypt(channel.config_encrypted)
        if config.get("notification_template_id") == template_id:
            raise HTTPException(status_code=409, detail="通知模板正在被通知渠道使用，请先修改关联渠道")
    audit(db, request, user, "notification_template.delete", template.name)
    db.delete(template)
    db.commit()
    return {"deleted": template_id}


@app.get("/api/notification-records")
def notification_records(limit: int = 500, _: User = Depends(notification_records_access), db: Session = Depends(get_db)):
    return db.scalars(select(NotificationRecord).order_by(desc(NotificationRecord.id)).limit(min(max(limit, 1), 1000))).all()


@app.delete("/api/notification-records/batch")
def batch_delete_notification_records(
    payload: NotificationRecordBatchDelete, request: Request,
    user: User = Depends(require_admin), db: Session = Depends(get_db),
):
    existing_ids = list(db.scalars(
        select(NotificationRecord.id).where(NotificationRecord.id.in_(payload.ids))
    ).all())
    if not existing_ids:
        raise HTTPException(status_code=404, detail="未找到可删除的通知记录")
    db.execute(sa_delete(NotificationRecord).where(NotificationRecord.id.in_(existing_ids)))
    audit(db, request, user, "notification_record.batch_delete", "selected", {
        "deleted_count": len(existing_ids), "deleted_ids": existing_ids,
    })
    db.commit()
    return {"deleted": len(existing_ids), "ids": existing_ids}


@app.delete("/api/notification-records/{record_id}")
def delete_notification_record(
    record_id: int, request: Request,
    user: User = Depends(require_admin), db: Session = Depends(get_db),
):
    record = db.get(NotificationRecord, record_id)
    if not record:
        raise HTTPException(status_code=404, detail="通知记录不存在")
    detail = {
        "channel_name": record.channel_name, "channel_type": record.channel_type,
        "status": record.status, "title": record.title,
    }
    db.delete(record)
    db.flush()
    audit(db, request, user, "notification_record.delete", str(record_id), detail)
    db.commit()
    return {"deleted": record_id}


@app.get("/api/on-call-shifts")
def on_call_shifts(_: User = Depends(on_call_access), db: Session = Depends(get_db)):
    rows = db.scalars(select(OnCallShift).order_by(OnCallShift.starts_at.desc())).all()
    return [{
        "id": row.id, "user_id": row.user_id,
        "username": row.user.username if row.user else "",
        "display_name": actor_name(row.user) if row.user else "",
        "phone": row.user.phone if row.user else "",
        "starts_at": row.starts_at.replace(tzinfo=display_zone).isoformat() if row.starts_at.tzinfo is None else row.starts_at.astimezone(display_zone).isoformat(),
        "ends_at": row.ends_at.replace(tzinfo=display_zone).isoformat() if row.ends_at.tzinfo is None else row.ends_at.astimezone(display_zone).isoformat(),
        "note": row.note,
        "created_by": row.created_by,
    } for row in rows]


@app.post("/api/on-call-shifts")
def create_on_call_shift(payload: OnCallShiftCreate, request: Request, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    assignee = db.get(User, payload.user_id)
    if not assignee or not assignee.is_active:
        raise HTTPException(status_code=400, detail="值班人员不存在或已停用")
    shift = OnCallShift(**payload.model_dump(), created_by=actor_name(user))
    db.add(shift)
    audit(db, request, user, "on_call_shift.create", assignee.username)
    db.commit()
    db.refresh(shift)
    return {"id": shift.id}


@app.put("/api/on-call-shifts/{shift_id}")
def update_on_call_shift(
    shift_id: int, payload: OnCallShiftCreate, request: Request,
    user: User = Depends(require_admin), db: Session = Depends(get_db),
):
    shift = db.get(OnCallShift, shift_id)
    if not shift:
        raise HTTPException(status_code=404, detail="值班排班不存在")
    assignee = db.get(User, payload.user_id)
    if not assignee or not assignee.is_active:
        raise HTTPException(status_code=400, detail="值班人员不存在或已停用")
    for key, value in payload.model_dump().items():
        setattr(shift, key, value)
    audit(db, request, user, "on_call_shift.update", str(shift_id), {
        "user_id": payload.user_id, "starts_at": payload.starts_at.isoformat(),
        "ends_at": payload.ends_at.isoformat(),
    })
    db.commit()
    return {"id": shift.id}


@app.delete("/api/on-call-shifts/{shift_id}")
def delete_on_call_shift(shift_id: int, request: Request, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    shift = db.get(OnCallShift, shift_id)
    if not shift:
        raise HTTPException(status_code=404, detail="值班排班不存在")
    audit(db, request, user, "on_call_shift.delete", str(shift_id))
    db.delete(shift)
    db.commit()
    return {"deleted": shift_id}


@app.get("/api/roles")
def roles(_: User = Depends(require_admin)):
    descriptions = {
        "tester": "面向测试验证，可查看监控、资源、发布历史、告警与值班信息",
        "developer": "面向研发交付，可使用镜像构建、更新发布和资源管理",
        "operator": "面向运维管理，拥有系统管理以外的全部业务菜单",
        "admin": "平台管理员，拥有全部菜单及用户、权限和审计管理能力",
    }
    return [{
        "name": ROLE_LABELS[code], "code": code, "description": descriptions[code],
        "default_permissions": list(ROLE_DEFAULT_PERMISSIONS[code]),
    } for code in ("tester", "developer", "operator", "admin")]


@app.get("/api/channels")
def channels(_: User = Depends(channels_access), db: Session = Depends(get_db)):
    items = db.scalars(select(NotificationChannel).order_by(desc(NotificationChannel.id))).all()
    rules = db.scalars(select(AlertRule)).all()
    usage = {
        channel.id: sum(channel.id in (rule.channel_ids or []) for rule in rules)
        for channel in items
    }
    result = []
    for channel in items:
        config = secret_box.decrypt(channel.config_encrypted)
        rendered_config = apply_notification_template(db, config)
        result.append({
            "id": channel.id, "name": channel.name, "channel_type": channel.channel_type,
            "template_name": rendered_config.get("template_name", "默认消息模板"),
            "enabled": channel.enabled, "rule_count": usage[channel.id],
            "created_at": channel.created_at,
        })
    return result


@app.post("/api/channels")
def create_channel(payload: ChannelCreate, request: Request, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    validate_channel_template(db, payload)
    channel = NotificationChannel(
        name=payload.name, channel_type=payload.channel_type,
        config_encrypted=secret_box.encrypt(payload.config), enabled=payload.enabled,
    )
    db.add(channel)
    try:
        audit(db, request, user, "channel.create", payload.name, {"type": payload.channel_type})
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="通知渠道名称已存在") from exc
    db.refresh(channel)
    return {"id": channel.id, "name": channel.name, "channel_type": channel.channel_type, "enabled": channel.enabled}


@app.get("/api/channels/{channel_id}/config")
def channel_config(channel_id: int, _: User = Depends(require_admin), db: Session = Depends(get_db)):
    channel = db.get(NotificationChannel, channel_id)
    if not channel:
        raise HTTPException(status_code=404, detail="通知渠道不存在")
    return {
        "id": channel.id,
        "name": channel.name,
        "channel_type": channel.channel_type,
        "config": secret_box.decrypt(channel.config_encrypted),
        "enabled": channel.enabled,
    }


@app.put("/api/channels/{channel_id}")
def update_channel(channel_id: int, payload: ChannelCreate, request: Request, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    channel = db.get(NotificationChannel, channel_id)
    if not channel:
        raise HTTPException(status_code=404, detail="通知渠道不存在")
    validate_channel_template(db, payload)
    channel.name = payload.name
    channel.channel_type = payload.channel_type
    channel.config_encrypted = secret_box.encrypt(payload.config)
    channel.enabled = payload.enabled
    try:
        audit(db, request, user, "channel.update", payload.name, {"type": payload.channel_type})
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="通知渠道名称已存在") from exc
    db.refresh(channel)
    return {"id": channel.id, "name": channel.name, "channel_type": channel.channel_type, "enabled": channel.enabled}


@app.post("/api/channels/{channel_id}/test")
async def test_channel(
    channel_id: int, request: Request,
    user: User = Depends(require_admin), db: Session = Depends(get_db),
):
    channel = db.get(NotificationChannel, channel_id)
    if not channel:
        raise HTTPException(status_code=404, detail="通知渠道不存在")
    title = "KubePilot 通知测试"
    message = f"由用户 {actor_name(user)} 发起，通知渠道工作正常。"
    config = apply_notification_template(db, secret_box.decrypt(channel.config_encrypted))
    status, error = "sent", ""
    try:
        await dispatch(channel.channel_type, config, title, message, {
            "severity": "测试", "rule_name": "通知渠道测试", "target": "system/test",
            "namespace": "system", "resource_name": "test", "resource_kind": "System",
            "metric": "test", "value": "1", "operator": "==", "threshold": "1",
            "started_at": datetime.now(timezone.utc).astimezone(display_zone).strftime("%Y-%m-%d %H:%M:%S"),
        })
    except Exception as exc:
        status, error = "failed", str(exc)
        db.add(NotificationRecord(
            channel_id=channel.id, channel_name=channel.name, channel_type=channel.channel_type,
            title=title, message=message, status=status, error=error,
        ))
        audit(db, request, user, "channel.test", channel.name, {"result": "failed"})
        db.commit()
        raise
    db.add(NotificationRecord(
        channel_id=channel.id, channel_name=channel.name, channel_type=channel.channel_type,
        title=title, message=message, status=status, error=error,
    ))
    audit(db, request, user, "channel.test", channel.name, {"result": "success"})
    db.commit()
    return {"status": "sent"}


@app.delete("/api/channels/{channel_id}")
def delete_channel(channel_id: int, request: Request, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    channel = db.get(NotificationChannel, channel_id)
    if not channel:
        raise HTTPException(status_code=404, detail="通知渠道不存在")
    for rule in db.scalars(select(AlertRule)).all():
        if channel_id in (rule.channel_ids or []):
            rule.channel_ids = [item for item in rule.channel_ids if item != channel_id]
    audit(db, request, user, "channel.delete", channel.name)
    db.delete(channel)
    db.commit()
    return {"deleted": channel_id}


@app.get("/api/alerts")
def alerts(status: str = "", limit: int = 200, _: User = Depends(dashboard_alert_access), db: Session = Depends(get_db)):
    query = select(AlertEvent)
    if status:
        query = query.where(AlertEvent.status == status)
    return db.scalars(query.order_by(desc(AlertEvent.id)).limit(min(limit, 1000))).all()


@app.delete("/api/alerts/{event_id}")
def delete_alert_event(event_id: int, request: Request, user: User = Depends(alert_events_access), db: Session = Depends(get_db)):
    event = db.get(AlertEvent, event_id)
    if not event:
        raise HTTPException(status_code=404, detail="告警事件不存在")
    audit(db, request, user, "alert_event.delete", str(event_id), {
        "title": event.title, "target": event.target, "status": event.status,
    })
    db.execute(sa_delete(IncidentActivity).where(IncidentActivity.event_id == event_id))
    db.delete(event)
    db.commit()
    return {"deleted": event_id}


@app.get("/api/audit-logs")
def audit_logs(
    username: str = "", action: str = "", limit: int = 500,
    _: User = Depends(require_admin), db: Session = Depends(get_db),
):
    query = select(AuditLog)
    if username:
        query = query.where(
            AuditLog.username.contains(username) | AuditLog.display_name.contains(username)
        )
    if action:
        query = query.where(AuditLog.action.contains(action))
    return db.scalars(query.order_by(desc(AuditLog.id)).limit(min(limit, 2000))).all()


@app.delete("/api/audit-logs")
def clear_audit_logs(
    request: Request, admin: User = Depends(require_admin), db: Session = Depends(get_db),
):
    result = db.execute(sa_delete(AuditLog))
    deleted = result.rowcount or 0
    audit(db, request, admin, "audit_log.clear", "all", {"deleted_count": deleted})
    db.commit()
    return {"deleted": deleted}


@app.delete("/api/audit-logs/batch")
def batch_delete_audit_logs(
    payload: AuditLogBatchDelete, request: Request,
    admin: User = Depends(require_admin), db: Session = Depends(get_db),
):
    existing_ids = list(db.scalars(select(AuditLog.id).where(AuditLog.id.in_(payload.ids))).all())
    if not existing_ids:
        raise HTTPException(status_code=404, detail="未找到可删除的审计日志")
    db.execute(sa_delete(AuditLog).where(AuditLog.id.in_(existing_ids)))
    audit(db, request, admin, "audit_log.batch_delete", "selected", {
        "deleted_count": len(existing_ids), "deleted_ids": existing_ids,
    })
    db.commit()
    return {"deleted": len(existing_ids), "ids": existing_ids}


@app.delete("/api/audit-logs/{audit_id}")
def delete_audit_log(
    audit_id: int, request: Request,
    admin: User = Depends(require_admin), db: Session = Depends(get_db),
):
    item = db.get(AuditLog, audit_id)
    if not item:
        raise HTTPException(status_code=404, detail="审计日志不存在")
    removed = {"username": item.username, "action": item.action, "resource": item.resource}
    db.delete(item)
    db.flush()
    audit(db, request, admin, "audit_log.delete", str(audit_id), removed)
    db.commit()
    return {"deleted": audit_id}


def resource_revision_query(namespace: str, api_version: str, kind: str, name: str):
    return select(ResourceRevision).where(
        ResourceRevision.namespace == namespace,
        ResourceRevision.api_version == api_version,
        ResourceRevision.kind == kind,
        ResourceRevision.name == name,
    )


def validate_resource_type(api_version: str, kind: str) -> None:
    if (api_version, kind) not in EDITABLE_RESOURCE_TYPES:
        raise HTTPException(status_code=400, detail="该资源类型不在平台允许管理的范围内")


def validate_resource_read_access(user: User, kind: str) -> None:
    if kind == "Secret" and not user.is_admin:
        raise HTTPException(status_code=403, detail="Secret 配置仅管理员可查看")


def manifest_diff(previous: dict, current: dict, previous_label: str, current_label: str) -> str:
    before = yaml.safe_dump(previous, allow_unicode=True, sort_keys=False).splitlines(keepends=True)
    after = yaml.safe_dump(current, allow_unicode=True, sort_keys=False).splitlines(keepends=True)
    return "".join(difflib.unified_diff(before, after, fromfile=previous_label, tofile=current_label))


def manifest_diff_rows(previous: dict, current: dict) -> list[dict[str, Any]]:
    before = yaml.safe_dump(previous, allow_unicode=True, sort_keys=False).splitlines()
    after = yaml.safe_dump(current, allow_unicode=True, sort_keys=False).splitlines()
    rows = []
    matcher = difflib.SequenceMatcher(None, before, after, autojunk=False)
    for tag, left_start, left_end, right_start, right_end in matcher.get_opcodes():
        left_lines = before[left_start:left_end]
        right_lines = after[right_start:right_end]
        length = max(len(left_lines), len(right_lines))
        for offset in range(length):
            left_exists = offset < len(left_lines)
            right_exists = offset < len(right_lines)
            row_type = tag
            if tag == "replace" and not left_exists:
                row_type = "insert"
            elif tag == "replace" and not right_exists:
                row_type = "delete"
            rows.append({
                "type": row_type,
                "left_number": left_start + offset + 1 if left_exists else None,
                "left_text": left_lines[offset] if left_exists else "",
                "right_number": right_start + offset + 1 if right_exists else None,
                "right_text": right_lines[offset] if right_exists else "",
            })
    return rows


def resource_detail_response(
    db: Session, namespace: str, api_version: str, kind: str, name: str,
    allow_write: bool = True,
) -> dict:
    current = k8s.get_namespaced_resource(namespace, api_version, kind, name)
    revisions = db.scalars(
        resource_revision_query(namespace, api_version, kind, name)
        .order_by(desc(ResourceRevision.version)).limit(20)
    ).all()
    previous = None
    if revisions:
        previous = revisions[1] if revisions[0].manifest == current["manifest"] and len(revisions) > 1 else revisions[0]
    return {
        **current,
        "editable": bool(current.get("editable")) and allow_write,
        "history": [{
            "version": row.version, "action": row.action,
            "created_by": row.created_by, "created_at": row.created_at,
        } for row in revisions],
        "can_compare": previous is not None,
        "can_rollback": previous is not None and allow_write,
        "previous_version": previous.version if previous else None,
    }


@app.get("/api/k8s/resource-types")
def k8s_resource_types(user: User = Depends(resources_access)):
    resource_types = k8s.list_namespaced_resource_types()
    if user.is_admin:
        return resource_types
    return [item for item in resource_types if item["kind"] != "Secret"]


@app.get("/api/k8s/resources")
def k8s_resources(
    namespace: str, api_version: str, kind: str,
    user: User = Depends(resources_access),
):
    validate_resource_type(api_version, kind)
    validate_resource_read_access(user, kind)
    if namespace not in k8s.list_namespaces():
        raise HTTPException(status_code=404, detail="命名空间不存在")
    return k8s.list_namespaced_resources(namespace, api_version, kind)


@app.get("/api/k8s/resource")
def k8s_resource_detail(
    namespace: str, api_version: str, kind: str, name: str,
    user: User = Depends(resources_access), db: Session = Depends(get_db),
):
    validate_resource_type(api_version, kind)
    validate_resource_read_access(user, kind)
    return resource_detail_response(
        db, namespace, api_version, kind, name, allow_write=user.is_admin,
    )


@app.put("/api/k8s/resource")
def save_k8s_resource(
    namespace: str, api_version: str, kind: str, name: str,
    payload: ResourceSaveRequest, request: Request,
    user: User = Depends(require_admin), db: Session = Depends(get_db),
):
    validate_resource_type(api_version, kind)
    try:
        manifest = yaml.safe_load(payload.content)
    except yaml.YAMLError as exc:
        raise HTTPException(status_code=400, detail=f"YAML 格式错误: {exc}") from exc
    if not isinstance(manifest, dict):
        raise HTTPException(status_code=400, detail="资源配置必须是 YAML 对象")
    try:
        before, updated = k8s.replace_namespaced_resource(namespace, api_version, kind, name, manifest)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    revisions = db.scalars(
        resource_revision_query(namespace, api_version, kind, name)
        .order_by(desc(ResourceRevision.version))
    ).all()
    next_version = revisions[0].version + 1 if revisions else 1
    if not revisions or revisions[0].manifest != before:
        db.add(ResourceRevision(
            namespace=namespace, api_version=api_version, kind=kind, name=name,
            version=next_version, manifest=before, action="baseline", created_by=actor_name(user),
        ))
        next_version += 1
    db.add(ResourceRevision(
        namespace=namespace, api_version=api_version, kind=kind, name=name,
        version=next_version, manifest=updated, action="update", created_by=actor_name(user),
    ))
    audit(db, request, user, "k8s_resource.update", f"{namespace}/{kind}/{name}", {
        "api_version": api_version, "version": next_version,
    })
    db.commit()
    return resource_detail_response(db, namespace, api_version, kind, name)


@app.post("/api/k8s/resource/diff")
def diff_k8s_resource(
    namespace: str, api_version: str, kind: str, name: str,
    payload: ResourceSaveRequest,
    user: User = Depends(resources_access), db: Session = Depends(get_db),
):
    validate_resource_type(api_version, kind)
    validate_resource_read_access(user, kind)
    live = k8s.get_namespaced_resource(namespace, api_version, kind, name)["manifest"]
    try:
        current = yaml.safe_load(payload.content)
    except yaml.YAMLError as exc:
        raise HTTPException(status_code=400, detail=f"YAML 格式错误: {exc}") from exc
    if not isinstance(current, dict):
        raise HTTPException(status_code=400, detail="资源配置必须是 YAML 对象")
    revisions = db.scalars(
        resource_revision_query(namespace, api_version, kind, name)
        .order_by(desc(ResourceRevision.version)).limit(20)
    ).all()
    previous = None
    if revisions:
        previous = revisions[1] if revisions[0].manifest == live and len(revisions) > 1 else revisions[0]
    if not previous:
        raise HTTPException(status_code=404, detail="暂无上一个版本可比较")
    return {
        "previous_version": previous.version,
        "diff": manifest_diff(previous.manifest, current, f"版本 {previous.version}", "当前版本"),
        "rows": manifest_diff_rows(previous.manifest, current),
        "previous_label": f"历史版本 v{previous.version}",
        "current_label": "当前编辑内容",
    }


@app.post("/api/k8s/resource/rollback")
def rollback_k8s_resource(
    namespace: str, api_version: str, kind: str, name: str,
    payload: ResourceRollbackRequest, request: Request,
    user: User = Depends(require_admin), db: Session = Depends(get_db),
):
    validate_resource_type(api_version, kind)
    current = k8s.get_namespaced_resource(namespace, api_version, kind, name)["manifest"]
    revisions = db.scalars(
        resource_revision_query(namespace, api_version, kind, name)
        .order_by(desc(ResourceRevision.version)).limit(100)
    ).all()
    if payload.version:
        target = next((row for row in revisions if row.version == payload.version), None)
    else:
        target = revisions[1] if revisions and revisions[0].manifest == current and len(revisions) > 1 else (revisions[0] if revisions else None)
    if not target:
        raise HTTPException(status_code=404, detail="未找到可回退的历史版本")
    try:
        _, updated = k8s.replace_namespaced_resource(namespace, api_version, kind, name, target.manifest)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    next_version = (revisions[0].version if revisions else 0) + 1
    db.add(ResourceRevision(
        namespace=namespace, api_version=api_version, kind=kind, name=name,
        version=next_version, manifest=updated, action="rollback", created_by=actor_name(user),
    ))
    audit(db, request, user, "k8s_resource.rollback", f"{namespace}/{kind}/{name}", {
        "api_version": api_version, "target_version": target.version, "version": next_version,
    })
    db.commit()
    return resource_detail_response(db, namespace, api_version, kind, name)


@app.post("/api/users")
def create_user(payload: UserCreate, request: Request, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    role = normalize_role(payload.role)
    display_name = payload.display_name.strip() or payload.username
    user = User(
        username=payload.username, password_hash=hash_password(payload.password),
        display_name=display_name, email=str(payload.email), phone=payload.phone.strip(),
        role=role, is_admin=role == "admin", must_change_password=True,
    )
    db.add(user)
    try:
        audit(db, request, admin, "user.create", display_name, {
            "username": payload.username, "role": role,
        })
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="用户名已存在") from exc
    return user_response(user)


@app.get("/api/users")
def users(_: User = Depends(require_admin), db: Session = Depends(get_db)):
    items = db.scalars(select(User).order_by(desc(User.id))).all()
    return [user_response(item) for item in items]


def ensure_admin_change_is_safe(db: Session, target: User, current: User, *, role: str, keep_active: bool) -> None:
    keep_admin = role == "admin"
    if target.username == settings.admin_username and (not keep_admin or not keep_active):
        raise HTTPException(status_code=400, detail="内置 admin 管理员不能停用或取消管理员权限")
    if target.id == current.id and (not keep_admin or not keep_active):
        raise HTTPException(status_code=400, detail="不能取消当前账号的管理员权限或停用当前账号")
    if target.is_admin and target.is_active and (not keep_admin or not keep_active):
        other_admin = db.scalar(select(User.id).where(
            User.id != target.id, User.is_admin.is_(True), User.is_active.is_(True)
        ).limit(1))
        if not other_admin:
            raise HTTPException(status_code=400, detail="必须至少保留一个启用的管理员")


@app.put("/api/users/{user_id}")
def update_user(user_id: int, payload: UserUpdate, request: Request, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="人员不存在")
    role = normalize_role(payload.role)
    role_changed = normalize_role(user.role, user.is_admin) != role
    ensure_admin_change_is_safe(db, user, admin, role=role, keep_active=payload.is_active)
    phone_changed = user.phone != payload.phone.strip()
    display_name = payload.display_name.strip() or user.display_name or user.username
    display_name_changed = user.display_name != display_name
    user.display_name = display_name
    user.email = str(payload.email)
    user.phone = payload.phone.strip()
    user.role = role
    user.is_admin = role == "admin"
    if role_changed:
        user.menu_permissions = None
    user.is_active = payload.is_active
    if payload.password:
        user.password_hash = hash_password(payload.password)
        user.must_change_password = user.id != admin.id
    audit(db, request, admin, "user.update", actor_name(user), {
        "username": user.username,
        "role": role, "is_active": payload.is_active,
        "phone_changed": phone_changed, "display_name_changed": display_name_changed,
        "password_changed": bool(payload.password),
    })
    db.commit()
    db.refresh(user)
    return user_response(user)


@app.delete("/api/users/{user_id}")
def delete_user(user_id: int, request: Request, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="人员不存在")
    if user.username == settings.admin_username:
        raise HTTPException(status_code=400, detail="内置 admin 管理员不能删除")
    if user.id == admin.id:
        raise HTTPException(status_code=400, detail="不能删除当前登录账号")
    ensure_admin_change_is_safe(db, user, admin, role="operator", keep_active=False)
    audit(db, request, admin, "user.delete", actor_name(user), {"username": user.username})
    db.delete(user)
    db.commit()
    return {"deleted": user_id}


@app.get("/api/permissions/catalog")
def permissions_catalog(_: User = Depends(require_admin)):
    return {
        "groups": permission_catalog(),
        "roles": [{
            "code": code, "name": ROLE_LABELS[code],
            "default_permissions": list(ROLE_DEFAULT_PERMISSIONS[code]),
        } for code in ("tester", "developer", "operator", "admin")],
    }


@app.get("/api/users/{user_id}/menu-permissions")
def user_menu_permissions(user_id: int, _: User = Depends(require_admin), db: Session = Depends(get_db)):
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="人员不存在")
    role = normalize_role(user.role, user.is_admin)
    return {
        "user_id": user.id, "username": user.username,
        "display_name": actor_name(user), "role": role,
        "role_name": ROLE_LABELS[role], "is_admin": role == "admin",
        "is_custom": isinstance(user.menu_permissions, list) and role != "admin",
        "menu_permissions": effective_menu_permissions(user),
        "role_default_permissions": list(ROLE_DEFAULT_PERMISSIONS[role]),
    }


@app.put("/api/users/{user_id}/menu-permissions")
def update_user_menu_permissions(
    user_id: int, payload: MenuPermissionUpdate, request: Request,
    admin: User = Depends(require_admin), db: Session = Depends(get_db),
):
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="人员不存在")
    role = normalize_role(user.role, user.is_admin)
    if role == "admin":
        raise HTTPException(status_code=400, detail="管理员固定拥有全部菜单权限，无需分配")
    invalid = sorted(set(payload.menu_permissions) - set(ALL_MENU_CODES))
    if invalid:
        raise HTTPException(status_code=400, detail=f"包含无效菜单权限: {', '.join(invalid)}")
    selected = set(payload.menu_permissions)
    user.menu_permissions = [code for code in ALL_MENU_CODES if code in selected]
    audit(db, request, admin, "user.permissions.update", actor_name(user), {
        "username": user.username,
        "role": role, "menu_permissions": user.menu_permissions,
    })
    db.commit()
    return user_menu_permissions(user_id, admin, db)


@app.delete("/api/users/{user_id}/menu-permissions")
def reset_user_menu_permissions(
    user_id: int, request: Request, admin: User = Depends(require_admin), db: Session = Depends(get_db),
):
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="人员不存在")
    role = normalize_role(user.role, user.is_admin)
    if role == "admin":
        raise HTTPException(status_code=400, detail="管理员固定拥有全部菜单权限")
    user.menu_permissions = None
    audit(db, request, admin, "user.permissions.reset", actor_name(user), {
        "username": user.username, "role": role,
    })
    db.commit()
    return user_menu_permissions(user_id, admin, db)


@app.get("/metrics")
def metrics():
    registry = CollectorRegistry()
    node_cpu = Gauge("kubepilot_node_cpu_percent", "Node CPU usage percent", ["node"], registry=registry)
    node_mem = Gauge("kubepilot_node_memory_percent", "Node memory usage percent", ["node"], registry=registry)
    node_disk = Gauge("kubepilot_node_disk_percent", "Node disk usage percent", ["node", "path"], registry=registry)
    pod_cpu = Gauge("kubepilot_pod_cpu_percent", "Pod CPU usage percent", ["namespace", "pod"], registry=registry)
    pod_mem = Gauge("kubepilot_pod_memory_percent", "Pod memory usage percent", ["namespace", "pod"], registry=registry)
    try:
        for node in k8s.list_nodes():
            node_cpu.labels(node=node["name"]).set(node["cpu_percent"])
            node_mem.labels(node=node["name"]).set(node["memory_percent"])
            for disk in node.get("disk_mounts", []):
                node_disk.labels(node=node["name"], path=disk["path"]).set(disk["percent"])
        for pod in k8s.list_pods():
            pod_cpu.labels(namespace=pod["namespace"], pod=pod["name"]).set(pod["cpu_percent"])
            pod_mem.labels(namespace=pod["namespace"], pod=pod["name"]).set(pod["memory_percent"])
    except Exception:
        logger.exception("metrics collection failed")
    return Response(generate_latest(registry), media_type=CONTENT_TYPE_LATEST)

