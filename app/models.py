from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(120), default="")
    password_hash: Mapped[str] = mapped_column(String(255))
    email: Mapped[str] = mapped_column(String(255), default="")
    phone: Mapped[str] = mapped_column(String(40), default="")
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    role: Mapped[str] = mapped_column(String(32), default="operator")
    menu_permissions: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DeploymentRecord(Base):
    __tablename__ = "deployment_records"
    id: Mapped[int] = mapped_column(primary_key=True)
    namespace: Mapped[str] = mapped_column(String(120), index=True)
    workload: Mapped[str] = mapped_column(String(180), index=True)
    image: Mapped[str] = mapped_column(String(500))
    replicas: Mapped[int] = mapped_column(Integer, default=1)
    revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(40), default="submitted")
    strategy: Mapped[str] = mapped_column(String(40), default="RollingUpdate")
    message: Mapped[str] = mapped_column(Text, default="")
    operator: Mapped[str] = mapped_column(String(80), default="system")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class BuildConfiguration(Base):
    __tablename__ = "build_configurations"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(180), unique=True, index=True)
    description: Mapped[str] = mapped_column(String(500), default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    git_url: Mapped[str] = mapped_column(String(600))
    git_ref: Mapped[str] = mapped_column(String(180), default="main")
    context_dir: Mapped[str] = mapped_column(String(300), default=".")
    dockerfile: Mapped[str] = mapped_column(String(300), default="Dockerfile")
    destination: Mapped[str] = mapped_column(String(600))
    namespace: Mapped[str] = mapped_column(String(120), default="devops-platform")
    git_secret_name: Mapped[str] = mapped_column(String(180), default="")
    git_auth_secret_name: Mapped[str] = mapped_column(String(180), default="")
    registry_secret_name: Mapped[str] = mapped_column(String(180), default="")
    build_args: Mapped[dict[str, str]] = mapped_column(JSON, default=dict)
    build_steps: Mapped[list[dict[str, str]]] = mapped_column(JSON, default=list)
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=1800)
    allow_concurrent: Mapped[bool] = mapped_column(Boolean, default=False)
    retention_count: Mapped[int] = mapped_column(Integer, default=20)
    created_by: Mapped[str] = mapped_column(String(80), default="system")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class ImageBuild(Base):
    __tablename__ = "image_builds"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(180), index=True)
    config_id: Mapped[int | None] = mapped_column(ForeignKey("build_configurations.id"), nullable=True, index=True)
    git_url: Mapped[str] = mapped_column(String(600))
    git_ref: Mapped[str] = mapped_column(String(180), default="main")
    context_dir: Mapped[str] = mapped_column(String(300), default=".")
    dockerfile: Mapped[str] = mapped_column(String(300), default="Dockerfile")
    destination: Mapped[str] = mapped_column(String(600), index=True)
    namespace: Mapped[str] = mapped_column(String(120), default="devops-platform")
    build_args: Mapped[dict[str, str]] = mapped_column(JSON, default=dict)
    build_steps: Mapped[list[dict[str, str]]] = mapped_column(JSON, default=list)
    job_name: Mapped[str] = mapped_column(String(180), default="")
    status: Mapped[str] = mapped_column(String(40), default="pending")
    message: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str] = mapped_column(String(80), default="system")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AlertRule(Base):
    __tablename__ = "alert_rules"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(180), unique=True)
    metric: Mapped[str] = mapped_column(String(80), index=True)
    operator: Mapped[str] = mapped_column(String(8), default=">")
    threshold: Mapped[float] = mapped_column(Float)
    duration_seconds: Mapped[int] = mapped_column(Integer, default=60)
    repeat_interval_minutes: Mapped[int] = mapped_column(Integer, default=30)
    severity: Mapped[str] = mapped_column(String(20), default="warning")
    namespace: Mapped[str] = mapped_column(String(120), default="*")
    target_pattern: Mapped[str] = mapped_column(String(180), default="*")
    template_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    channel_ids: Mapped[list[int]] = mapped_column(JSON, default=list)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class NotificationChannel(Base):
    __tablename__ = "notification_channels"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(180), unique=True)
    channel_type: Mapped[str] = mapped_column(String(40), index=True)
    config_encrypted: Mapped[str] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AlertEvent(Base):
    __tablename__ = "alert_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    rule_id: Mapped[int | None] = mapped_column(ForeignKey("alert_rules.id"), nullable=True)
    fingerprint: Mapped[str] = mapped_column(String(255), index=True)
    title: Mapped[str] = mapped_column(String(300))
    message: Mapped[str] = mapped_column(Text)
    severity: Mapped[str] = mapped_column(String(20), default="warning")
    status: Mapped[str] = mapped_column(String(20), default="firing", index=True)
    target: Mapped[str] = mapped_column(String(300), default="")
    value: Mapped[float | None] = mapped_column(Float, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    assignee: Mapped[str] = mapped_column(String(80), default="")
    handling_note: Mapped[str] = mapped_column(Text, default="")
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_by: Mapped[str] = mapped_column(String(80), default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    rule: Mapped[AlertRule | None] = relationship()


class IncidentActivity(Base):
    __tablename__ = "incident_activities"
    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("alert_events.id"), index=True)
    action: Mapped[str] = mapped_column(String(40), default="update")
    from_status: Mapped[str] = mapped_column(String(20), default="")
    to_status: Mapped[str] = mapped_column(String(20), default="")
    assignee: Mapped[str] = mapped_column(String(80), default="")
    note: Mapped[str] = mapped_column(Text, default="")
    operator: Mapped[str] = mapped_column(String(80), default="system")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class AlertRuleTemplate(Base):
    __tablename__ = "alert_rule_templates"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(180), unique=True)
    description: Mapped[str] = mapped_column(String(500), default="")
    metric: Mapped[str] = mapped_column(String(80), index=True)
    operator: Mapped[str] = mapped_column(String(8), default=">")
    threshold: Mapped[float] = mapped_column(Float, default=80)
    duration_seconds: Mapped[int] = mapped_column(Integer, default=60)
    repeat_interval_minutes: Mapped[int] = mapped_column(Integer, default=30)
    severity: Mapped[str] = mapped_column(String(20), default="warning")
    namespace: Mapped[str] = mapped_column(String(120), default="*")
    target_pattern: Mapped[str] = mapped_column(String(180), default="*")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AlertSubscription(Base):
    __tablename__ = "alert_subscriptions"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(180))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    rule_id: Mapped[int | None] = mapped_column(ForeignKey("alert_rules.id"), nullable=True, index=True)
    channel_ids: Mapped[list[int]] = mapped_column(JSON, default=list)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    user: Mapped[User] = relationship()
    rule: Mapped[AlertRule | None] = relationship()


class NotificationTemplate(Base):
    __tablename__ = "notification_templates"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(180), unique=True)
    channel_type: Mapped[str] = mapped_column(String(40), index=True)
    title_template: Mapped[str] = mapped_column(String(500), default="{title}")
    message_template: Mapped[str] = mapped_column(Text, default="{message}")
    created_by: Mapped[str] = mapped_column(String(80), default="system")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class NotificationRecord(Base):
    __tablename__ = "notification_records"
    id: Mapped[int] = mapped_column(primary_key=True)
    channel_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    channel_name: Mapped[str] = mapped_column(String(180), default="")
    channel_type: Mapped[str] = mapped_column(String(40), default="")
    event_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(500))
    message: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="sent", index=True)
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class OnCallShift(Base):
    __tablename__ = "on_call_shifts"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    note: Mapped[str] = mapped_column(String(500), default="")
    created_by: Mapped[str] = mapped_column(String(80), default="system")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    user: Mapped[User] = relationship()


class ResourceRevision(Base):
    __tablename__ = "resource_revisions"
    id: Mapped[int] = mapped_column(primary_key=True)
    namespace: Mapped[str] = mapped_column(String(120), index=True)
    api_version: Mapped[str] = mapped_column(String(120), index=True)
    kind: Mapped[str] = mapped_column(String(120), index=True)
    name: Mapped[str] = mapped_column(String(253), index=True)
    version: Mapped[int] = mapped_column(Integer)
    manifest: Mapped[dict[str, Any]] = mapped_column(JSON)
    action: Mapped[str] = mapped_column(String(40), default="update")
    created_by: Mapped[str] = mapped_column(String(80), default="system")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(80), index=True)
    display_name: Mapped[str] = mapped_column(String(120), default="", index=True)
    action: Mapped[str] = mapped_column(String(120), index=True)
    resource: Mapped[str] = mapped_column(String(300), default="")
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)

