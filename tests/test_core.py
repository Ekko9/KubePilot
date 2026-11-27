import asyncio

from app import alerting
from app.alerting import AlertEngine, alert_target, compare, format_event_time, is_completed_job_pod, notification_due
from contextlib import contextmanager
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import HTTPException, Request
from kubernetes import client
from kubernetes.client import ApiException
from pydantic import ValidationError
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.orm import Session

from app.db import Base
from app.k8s import KubernetesService, parse_cpu, parse_memory
from app.main import DEFAULT_NOTIFICATION_TEMPLATES, STATIC_ASSET_VERSION, _build_config_payload, _prune_build_history, _repair_legacy_rollback_records, alert_subscriptions, app, batch_delete_audit_logs, batch_delete_notification_records, change_password, cleanup_orphan_build_jobs, create_user, delete_alert_event, delete_alert_rule, delete_audit_log, delete_build, delete_notification_record, delete_user, incident_detail, manifest_diff_rows, notification_records, on_call_shifts, update_alert_subscription, update_incident, update_on_call_shift, validate_resource_read_access
from app.models import AlertEvent, AlertRule, AlertSubscription, AuditLog, BuildConfiguration, DeploymentRecord, ImageBuild, IncidentActivity, NotificationRecord, NotificationTemplate, OnCallShift, User
from app.notifications import apply_notification_template, render_channel_message, split_recipients
from app.schemas import AlertRuleCreate, AlertRuleTemplateCreate, AlertSubscriptionCreate, AuditLogBatchDelete, BuildConfigurationCreate, BuildRequest, ChangePasswordRequest, ChannelCreate, DeployRequest, DiskMonitorConfig, IncidentUpdate, NotificationRecordBatchDelete, OnCallShiftCreate, ResourceSaveRequest, UserCreate, UserUpdate
from app.permissions import ALL_MENU_CODES, ROLE_DEFAULT_PERMISSIONS, effective_menu_permissions
from app.security import get_current_user, hash_password, require_admin, verify_password


def test_cpu_quantities():
    assert parse_cpu("500m") == 0.5
    assert parse_cpu("100000000n") == 0.1
    assert parse_cpu("2") == 2


def test_login_template_does_not_duplicate_version_prefix():
    template = open("app/templates/index.html", encoding="utf-8").read()
    rendered = template.replace("{{ version }}", "v1.0.0").replace("{{ asset_version }}", STATIC_ASSET_VERSION)
    assert "<small>v1.0.0</small>" in rendered
    assert "vv1.0.0" not in rendered
    assert f"/static/app.js?v={STATIC_ASSET_VERSION}" in rendered
    assert f"/static/app.css?v={STATIC_ASSET_VERSION}" in rendered


def test_repository_defaults_do_not_expose_environment_addresses():
    paths = (
        "README.md",
        "app/config.py",
        "chart/devops-platform/values.yaml",
        "scripts/online_regression.py",
        "scripts/quick-install.sh",
    )
    content = "\n".join(open(path, encoding="utf-8").read() for path in paths)

    assert "172" + ".16." not in content
    assert "192" + ".168." not in content
    assert "moc.htlaehoahuih.robrah"[::-1] not in content
    assert "db.example.internal" in content
    assert "registry.example.com/team/kubepilot-devops" in content


def test_login_form_does_not_prepopulate_credentials_and_logout_clears_fields():
    template = open("app/templates/index.html", encoding="utf-8").read()
    javascript = open("app/static/app.js", encoding="utf-8").read()
    assert 'id="login-username" value="admin"' not in template
    assert "function clearLoginForm()" in javascript
    assert "document.getElementById('login-password').value = '';" in javascript


def test_audit_logs_do_not_collect_or_display_source_ip():
    template = open("app/templates/index.html", encoding="utf-8").read()
    javascript = open("app/static/app.js", encoding="utf-8").read()
    assert "source_ip" not in {column.key for column in inspect(AuditLog).columns}
    assert "来源 IP" not in template
    assert "source_ip" not in javascript


def test_on_call_uses_month_calendar_and_bounded_agenda():
    template = open("app/templates/index.html", encoding="utf-8").read()
    stylesheet = open("app/static/app.css", encoding="utf-8").read()
    javascript = open("app/static/app.js", encoding="utf-8").read()
    assert 'id="on-call-calendar"' in template
    assert 'id="on-call-user-filter"' in template
    assert 'id="on-call-agenda-title"' in template
    assert ".on-call-calendar" in stylesheet
    assert ".day-shift-more" in stylesheet
    assert ".app-shell aside" not in stylesheet
    assert ".app-shell > aside" in stylesheet
    assert ".on-call-day.selected .day-number" in stylesheet
    assert ".agenda-phone" in stylesheet


def test_pod_log_viewer_has_headlamp_style_live_controls():
    template = open("app/templates/index.html", encoding="utf-8").read()
    javascript = open("app/static/app.js", encoding="utf-8").read()
    stylesheet = open("app/static/app.css", encoding="utf-8").read()

    for control_id in (
        "pod-log-container", "pod-log-lines", "pod-log-since", "pod-log-filter",
        "pod-log-previous", "pod-log-timestamps", "pod-log-follow",
        "pod-log-autoscroll", "pod-log-wrap", "pod-log-clear",
        "pod-log-download",
    ):
        assert f'id="{control_id}"' in template
    assert "}, 2000);" in javascript
    assert "stopPodLogPolling()" in javascript
    assert "pod-log-modal').addEventListener('close'" in javascript
    assert ".pod-log-toolbar" in stylesheet
    assert ".pod-log-console" in stylesheet
    assert 'value="0">全部日志' in template
    assert "is-log-expanded" in javascript
    assert "clearBaseline" in javascript
    assert "setTimeout(function () { URL.revokeObjectURL(url); }, 1000)" in javascript
    assert "dayShifts.slice(0, 3)" in javascript
    assert "dayShifts.length - 3" in javascript


def test_log_viewer_does_not_offer_clipboard_copy():
    template = open("app/templates/index.html", encoding="utf-8").read()
    javascript = open("app/static/app.js", encoding="utf-8").read()
    functions = open("docs/FUNCTIONS.md", encoding="utf-8").read()

    assert 'id="pod-log-copy"' not in template
    assert "pod-log-copy" not in javascript
    assert "copyToClipboard" not in javascript
    assert "自动换行、复制、下载" not in functions


def test_monitor_resources_use_consistent_name():
    chart = open("chart/devops-platform/templates/disk-monitor.yaml", encoding="utf-8").read()
    deployment = open("chart/devops-platform/templates/deployment.yaml", encoding="utf-8").read()
    readme = open("README.md", encoding="utf-8").read()

    assert 'kind: DaemonSet\nmetadata:\n  name: {{ include "devops-platform.fullname" . }}-monitor' in chart
    assert 'kind: ConfigMap\nmetadata:\n  name: {{ include "devops-platform.fullname" . }}-monitor' in chart
    assert 'printf "%s-monitor"' in deployment
    assert "devops-platform-disk-monitor" not in chart + deployment + readme
    assert "get ds devops-platform-monitor" in readme
    assert "logs ds/devops-platform-monitor" in readme


def test_database_utc_times_are_rendered_in_configured_display_timezone():
    template = open("app/templates/index.html", encoding="utf-8").read()
    javascript = open("app/static/app.js", encoding="utf-8").read()
    chart = open("chart/devops-platform/templates/deployment.yaml", encoding="utf-8").read()

    assert 'data-timezone="{{ display_timezone }}"' in template
    assert "normalized.replace(' ', 'T') + 'Z'" in javascript
    assert "timeZone: displayTimezone" in javascript
    assert "formatLogTimestamps(raw)" in javascript
    assert "formatClock(item.starts_at)" in javascript
    assert "DEVOPS_DISPLAY_TIMEZONE" in chart
    assert "name: TZ" in chart


def test_resource_diff_uses_aligned_idea_style_columns():
    rows = manifest_diff_rows(
        {"apiVersion": "v1", "data": {"mode": "old", "removed": "yes"}},
        {"apiVersion": "v1", "data": {"mode": "new", "added": "yes"}},
    )
    template = open("app/templates/index.html", encoding="utf-8").read()
    javascript = open("app/static/app.js", encoding="utf-8").read()
    stylesheet = open("app/static/app.css", encoding="utf-8").read()

    assert any(row["type"] == "replace" for row in rows)
    assert all({"left_number", "left_text", "right_number", "right_text"} <= row.keys() for row in rows)
    assert 'class="side-by-side-diff"' in template
    assert "diff-line-number" in javascript
    assert ".diff-row.replace" in stylesheet


def test_new_user_must_change_password_before_using_platform():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    request = Request({"type": "http", "method": "POST", "path": "/api/users", "headers": [], "client": ("127.0.0.1", 1234)})
    with Session(engine) as db:
        admin = User(
            username="admin", password_hash=hash_password("admin-password"),
            email="admin@example.com", is_admin=True, must_change_password=False,
        )
        db.add(admin)
        db.commit()
        created = create_user(UserCreate(
            username="new-user", password="Initial9!Password", email="new@example.com",
        ), request, admin, db)
        user = db.get(User, created["id"])
        assert user.must_change_password is True
        with pytest.raises(HTTPException, match="首次登录"):
            get_current_user(user)

        result = change_password(ChangePasswordRequest(
            current_password="Initial9!Password", new_password="Secure9!NewPassword",
        ), request, user, db)
        assert result["changed"] is True
        assert user.must_change_password is False
        assert verify_password("Secure9!NewPassword", user.password_hash)
        assert get_current_user(user) is user


@pytest.mark.parametrize("password", [
    "Aa1!",             # Too short
    "lowercase9!",      # Missing uppercase
    "UPPERCASE9!",      # Missing lowercase
    "MixedCase!",       # Missing number
    "MixedCase9",       # Missing special character
])
def test_strong_password_policy_applies_to_password_change_create_and_reset(password):
    with pytest.raises(ValidationError):
        ChangePasswordRequest(current_password="Current9!Password", new_password=password)
    with pytest.raises(ValidationError):
        UserCreate(username="new-user", password=password, email="new@example.com")
    with pytest.raises(ValidationError):
        UserUpdate(email="new@example.com", password=password)


def test_deleting_audit_log_records_the_admin_delete_action():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    request = Request({"type": "http", "method": "DELETE", "path": "/api/audit-logs/1", "headers": [], "client": ("127.0.0.1", 1234)})
    with Session(engine) as db:
        admin = User(
            username="admin", password_hash="hash", email="admin@example.com",
            is_admin=True, must_change_password=False,
        )
        original = AuditLog(username="operator", action="deployment.apply", resource="prod/api")
        db.add_all([admin, original])
        db.commit()
        original_id = original.id

        assert delete_audit_log(original_id, request, admin, db) == {"deleted": original_id}
        assert db.scalar(select(AuditLog).where(AuditLog.action == "deployment.apply")) is None
        replacement = db.scalar(select(AuditLog).where(AuditLog.action == "audit_log.delete"))
        assert replacement is not None
        assert replacement.username == "admin"


def test_regular_user_cannot_use_admin_audit_permissions():
    user = User(
        username="viewer", password_hash="hash", email="viewer@example.com",
        is_admin=False, is_active=True, must_change_password=False,
    )
    with pytest.raises(HTTPException, match="管理员权限"):
        require_admin(user)


def test_role_defaults_and_custom_menu_permissions():
    tester = User(
        username="tester", password_hash="hash", email="tester@example.com",
        role="tester", is_admin=False, is_active=True, must_change_password=False,
    )
    assert effective_menu_permissions(tester) == list(ROLE_DEFAULT_PERMISSIONS["tester"])
    tester.menu_permissions = ["dashboard", "pods", "invalid"]
    assert effective_menu_permissions(tester) == ["dashboard", "pods"]

    admin = User(
        username="admin", password_hash="hash", email="admin@example.com",
        role="operator", is_admin=True, is_active=True, must_change_password=False,
        menu_permissions=[],
    )
    assert effective_menu_permissions(admin) == list(ALL_MENU_CODES)
    assert require_admin(admin) is admin


def test_user_display_name_is_separate_from_login_username_and_audited():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    request = Request({"type": "http", "method": "POST", "path": "/api/users", "headers": [], "client": ("127.0.0.1", 1234)})
    with Session(engine) as db:
        admin = User(
            username="admin", display_name="系统管理员", password_hash="hash",
            email="admin@example.com", role="admin", is_admin=True, must_change_password=False,
        )
        db.add(admin)
        db.commit()
        result = create_user(UserCreate(
            username="liuzhi", display_name="刘志", password="Strong9!password",
            email="liuzhi@example.com", role="operator",
        ), request, admin, db)
        assert result["username"] == "liuzhi"
        assert result["display_name"] == "刘志"
        log = db.scalar(select(AuditLog).where(AuditLog.action == "user.create"))
        assert log.username == "admin"
        assert log.display_name == "系统管理员"


def test_permission_assignment_endpoints_require_admin():
    def dependency_names(path: str, method: str) -> set[str]:
        route = next(item for item in app.routes if getattr(item, "path", "") == path and method in getattr(item, "methods", set()))
        return {dependency.call.__name__ for dependency in route.dependant.dependencies}

    assert "require_admin" in dependency_names("/api/permissions/catalog", "GET")
    assert "require_admin" in dependency_names("/api/users/{user_id}/menu-permissions", "GET")
    assert "require_admin" in dependency_names("/api/users/{user_id}/menu-permissions", "PUT")
    assert "require_admin" in dependency_names("/api/users/{user_id}/menu-permissions", "DELETE")


def test_builtin_admin_cannot_be_deleted_by_another_admin():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    request = Request({"type": "http", "method": "DELETE", "path": "/api/users/1", "headers": [], "client": ("127.0.0.1", 1234)})
    with Session(engine) as db:
        builtin = User(
            username="admin", password_hash="hash", email="admin@example.com",
            is_admin=True, is_active=True, must_change_password=False,
        )
        operator = User(
            username="security-admin", password_hash="hash", email="security@example.com",
            is_admin=True, is_active=True, must_change_password=False,
        )
        db.add_all([builtin, operator])
        db.commit()

        with pytest.raises(HTTPException, match="不能删除"):
            delete_user(builtin.id, request, operator, db)
        assert db.get(User, builtin.id) is builtin


def test_audit_logs_support_batch_delete_and_keep_audit_trace():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    request = Request({"type": "http", "method": "DELETE", "path": "/api/audit-logs/batch", "headers": [], "client": ("127.0.0.1", 1234)})
    with Session(engine) as db:
        admin = User(
            username="admin", password_hash="hash", email="admin@example.com",
            is_admin=True, must_change_password=False,
        )
        selected = [
            AuditLog(username="operator", action="deployment.apply", resource="prod/api"),
            AuditLog(username="operator", action="deployment.rollback", resource="prod/api"),
        ]
        retained = AuditLog(username="viewer", action="auth.login", resource="viewer")
        db.add_all([admin, *selected, retained])
        db.commit()
        selected_ids = [item.id for item in selected]

        result = batch_delete_audit_logs(AuditLogBatchDelete(ids=selected_ids), request, admin, db)

        assert result["deleted"] == 2
        assert db.scalar(select(AuditLog).where(AuditLog.action == "deployment.apply")) is None
        assert db.get(AuditLog, retained.id) is not None
        trace = db.scalar(select(AuditLog).where(AuditLog.action == "audit_log.batch_delete"))
        assert trace is not None
        assert trace.detail["deleted_count"] == 2


def test_subscription_notification_and_on_call_reads_allow_regular_users_but_writes_require_admin():
    def dependency_names(path: str, method: str) -> set[str]:
        route = next(item for item in app.routes if getattr(item, "path", "") == path and method in getattr(item, "methods", set()))
        return {dependency.call.__name__ for dependency in route.dependant.dependencies}

    assert "require_menu_alert_subscriptions" in dependency_names("/api/alert-subscriptions", "GET")
    assert "require_menu_notification_records" in dependency_names("/api/notification-records", "GET")
    assert "require_menu_on_call" in dependency_names("/api/on-call-shifts", "GET")
    assert "require_admin" in dependency_names("/api/alert-subscriptions/{subscription_id}", "PUT")
    assert "require_admin" in dependency_names("/api/alert-subscriptions/{subscription_id}", "DELETE")
    assert "require_admin" in dependency_names("/api/on-call-shifts/{shift_id}", "PUT")
    assert "require_admin" in dependency_names("/api/on-call-shifts/{shift_id}", "DELETE")


def test_resource_management_reads_allow_regular_users_but_writes_require_admin():
    def dependency_names(path: str, method: str) -> set[str]:
        route = next(item for item in app.routes if getattr(item, "path", "") == path and method in getattr(item, "methods", set()))
        return {dependency.call.__name__ for dependency in route.dependant.dependencies}

    assert "require_menu_resources" in dependency_names("/api/k8s/resource-types", "GET")
    assert "require_menu_resources" in dependency_names("/api/k8s/resources", "GET")
    assert "require_menu_resources" in dependency_names("/api/k8s/resource", "GET")
    assert "require_menu_resources" in dependency_names("/api/k8s/resource/diff", "POST")
    assert "require_admin" in dependency_names("/api/k8s/resource", "PUT")
    assert "require_admin" in dependency_names("/api/k8s/resource/rollback", "POST")

    template = open("app/templates/index.html", encoding="utf-8").read()
    javascript = open("app/static/app.js", encoding="utf-8").read()
    assert '<button data-page="resources">资源管理</button>' in template
    assert "editor.readOnly = !currentIsAdmin" in javascript
    assert "resource-save-button').classList.toggle('hidden', !currentIsAdmin)" in javascript

    regular_user = User(username="viewer", password_hash="hash", is_admin=False)
    validate_resource_read_access(regular_user, "ConfigMap")
    with pytest.raises(HTTPException, match="Secret 配置仅管理员可查看"):
        validate_resource_read_access(regular_user, "Secret")


def test_admin_can_update_subscription_and_on_call_shift():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    request = Request({"type": "http", "method": "PUT", "path": "/", "headers": [], "client": ("127.0.0.1", 1234)})
    with Session(engine) as db:
        admin = User(username="admin", password_hash="hash", email="admin@example.com", is_admin=True, must_change_password=False)
        assignee = User(
            username="operator", password_hash="hash", email="operator@example.com",
            phone="13800138000", is_active=True, must_change_password=False,
        )
        subscription = AlertSubscription(name="old", user=assignee, channel_ids=[], enabled=True)
        shift = OnCallShift(
            user=assignee, starts_at=datetime(2026, 11, 21, 1),
            ends_at=datetime(2026, 11, 21, 9), note="old",
        )
        db.add_all([admin, assignee, subscription, shift])
        db.commit()

        updated_subscription = update_alert_subscription(
            subscription.id, AlertSubscriptionCreate(
                name="new", user_id=assignee.id, rule_id=None, channel_ids=[], enabled=False,
            ), request, admin, db,
        )
        updated_shift = update_on_call_shift(
            shift.id, OnCallShiftCreate(
                user_id=assignee.id, starts_at=datetime(2026, 11, 22, 1),
                ends_at=datetime(2026, 11, 22, 10), note="updated",
            ), request, admin, db,
        )

        assert updated_subscription == {"id": subscription.id, "name": "new"}
        assert subscription.enabled is False
        assert updated_shift == {"id": shift.id}
        assert shift.note == "updated"
        assert on_call_shifts(assignee, db)[0]["phone"] == "13800138000"
        assert db.scalar(select(AuditLog).where(AuditLog.action == "alert_subscription.update"))
        assert db.scalar(select(AuditLog).where(AuditLog.action == "on_call_shift.update"))


def test_notification_record_deletes_require_admin_and_support_single_and_batch_operations():
    def dependency_names(path: str, method: str) -> set[str]:
        route = next(item for item in app.routes if getattr(item, "path", "") == path and method in getattr(item, "methods", set()))
        return {dependency.call.__name__ for dependency in route.dependant.dependencies}

    assert "require_admin" in dependency_names("/api/notification-records/batch", "DELETE")
    assert "require_admin" in dependency_names("/api/notification-records/{record_id}", "DELETE")

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    request = Request({"type": "http", "method": "DELETE", "path": "/", "headers": [], "client": ("127.0.0.1", 1234)})
    with Session(engine) as db:
        admin = User(username="admin", password_hash="hash", email="admin@example.com", is_admin=True, must_change_password=False)
        records = [
            NotificationRecord(channel_name="mail", channel_type="email", title=f"notice-{index}", message="detail")
            for index in range(3)
        ]
        db.add_all([admin, *records])
        db.commit()

        result = batch_delete_notification_records(
            NotificationRecordBatchDelete(ids=[records[0].id, records[1].id]), request, admin, db,
        )
        assert result["deleted"] == 2
        assert db.get(NotificationRecord, records[2].id) is records[2]
        assert delete_notification_record(records[2].id, request, admin, db) == {"deleted": records[2].id}
        assert db.scalar(select(AuditLog).where(AuditLog.action == "notification_record.batch_delete"))
        assert db.scalar(select(AuditLog).where(AuditLog.action == "notification_record.delete"))


def test_incident_processing_supports_assignee_notes_resolution_and_timeline():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    request = Request({"type": "http", "method": "PUT", "path": "/api/incidents/1", "headers": [], "client": ("127.0.0.1", 1234)})
    with Session(engine) as db:
        operator = User(
            username="operator", password_hash="hash", email="operator@example.com",
            is_admin=False, must_change_password=False,
        )
        event = AlertEvent(
            fingerprint="incident-test", title="CPU high", message="CPU is high",
            target="prod/api-0", value=95, status="firing",
        )
        db.add_all([operator, event])
        db.commit()

        processing = update_incident(event.id, IncidentUpdate(
            status="acknowledged", assignee="operator", note="正在检查 CPU profile",
        ), request, operator, db)
        assert processing["status"] == "acknowledged"
        assert event.acknowledged_at is not None

        resolved = update_incident(event.id, IncidentUpdate(
            status="resolved", assignee="operator", note="已扩容并确认负载恢复",
        ), request, operator, db)
        assert resolved["status"] == "resolved"
        assert event.resolved_by == "operator"
        assert event.resolved_at is not None
        detail = incident_detail(event.id, operator, db)
        assert detail["handling_note"] == "已扩容并确认负载恢复"
        assert [activity.action for activity in detail["activities"]] == ["resolve", "update"]
        assert db.scalar(select(AuditLog).where(AuditLog.action == "incident.update"))


def test_incident_processing_requires_owner_and_note():
    with pytest.raises(ValidationError, match="负责人"):
        IncidentUpdate(status="acknowledged", assignee="", note="working")
    with pytest.raises(ValidationError, match="处理说明"):
        IncidentUpdate(status="resolved", assignee="operator", note="")


def test_recipient_lists_accept_common_separators_and_remove_duplicates():
    assert split_recipients("one@example.com,two@example.com；three@example.com\nfour@example.com") == [
        "one@example.com", "two@example.com", "three@example.com", "four@example.com",
    ]
    assert split_recipients(["13800000000;13900000000", "13800000000"]) == [
        "13800000000", "13900000000",
    ]


def test_memory_quantities():
    assert parse_memory("1Gi") == 1024**3
    assert parse_memory("256Mi") == 256 * 1024**2


def test_completed_job_pods_are_excluded_from_not_running_alerts():
    assert is_completed_job_pod({"owner_kind": "Job", "phase": "Succeeded"}) is True
    assert is_completed_job_pod({"owner_kind": "Job", "phase": "Running"}) is False
    assert is_completed_job_pod({"owner_kind": "Job", "phase": "Failed"}) is False
    assert is_completed_job_pod({"owner_kind": "Deployment", "phase": "Succeeded"}) is False


def test_node_alerts_are_cluster_scoped_and_times_are_displayed_in_local_timezone():
    assert alert_target({"kind": "Node", "namespace": "_cluster", "target": "worker-01"}) == "worker-01"
    assert alert_target({"kind": "Pod", "namespace": "prod", "target": "api-0"}) == "prod/api-0"
    assert format_event_time(datetime(2026, 11, 18, 6, 47, 49)) == "2026-11-18 14:47:49"


def test_manual_incidents_are_not_auto_resolved(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        manual = AlertEvent(
            rule_id=None, fingerprint="demo:incident:manual", title="[演示] 手工故障",
            message="manual", severity="warning", status="firing", target="prod/api",
        )
        db.add(manual)
        db.commit()
        manual_id = manual.id

    @contextmanager
    def test_session():
        with Session(engine) as db:
            yield db
            db.commit()

    monkeypatch.setattr(alerting, "db_session", test_session)
    asyncio.run(AlertEngine().evaluate([]))

    with Session(engine) as db:
        assert db.get(AlertEvent, manual_id).status == "firing"
        assert db.scalar(select(IncidentActivity).where(IncidentActivity.event_id == manual_id)) is None


def test_build_credentials_require_complete_pairs_and_http_or_https_git_url():
    base = {
        "name": "web-release",
        "git_url": "https://git.example.com/team/project.git",
        "destination": "harbor.example.com/team/project:${BUILD_NUMBER}",
    }
    config = BuildConfigurationCreate(**base, git_username="builder", git_password="token")
    assert config.git_username == "builder"
    http_config = BuildConfigurationCreate(**{**base, "git_url": "http://git.example.com/team/project.git"}, git_username="builder", git_password="token")
    assert http_config.git_url.startswith("http://")
    with pytest.raises(ValidationError, match="必须同时填写"):
        BuildConfigurationCreate(**base, git_username="builder")
    with pytest.raises(ValidationError, match=r"HTTP\(S\)"):
        BuildConfigurationCreate(**{**base, "git_url": "git@example.com:team/project.git"}, git_username="builder", git_password="token")


def test_build_steps_validate_repository_paths_and_limits():
    base = {
        "name": "web-release",
        "git_url": "https://git.example.com/team/project.git",
        "destination": "registry.example.com/team/project:1",
    }
    config = BuildConfigurationCreate(**base, build_steps=[{
        "name": "frontend", "image": "registry.example.com/build/node:20",
        "working_dir": "frontend", "command": "npm ci && npm run build",
        "env": {"NPM_CONFIG_REGISTRY": "http://npm.example.internal/"},
    }])
    assert config.build_steps[0].working_dir == "frontend"
    with pytest.raises(ValidationError, match="代码仓库内"):
        BuildConfigurationCreate(**base, build_steps=[{
            "name": "escape", "image": "node:20", "working_dir": "../outside",
            "command": "npm run build",
        }])


def test_build_config_response_does_not_return_credentials_or_secret_names():
    config = BuildConfiguration(
        id=1, name="web-release", git_url="https://git.example.com/team/project.git",
        destination="harbor.example.com/team/project:1", git_auth_secret_name="git-secret",
        registry_secret_name="registry-secret",
    )

    payload = _build_config_payload(config)

    assert payload["has_git_credentials"] is True
    assert payload["has_registry_credentials"] is True
    assert "git_auth_secret_name" not in payload
    assert "registry_secret_name" not in payload
    assert "git_password" not in payload
    assert "registry_password" not in payload


def test_build_secret_stores_encoded_values_in_kubernetes():
    service = KubernetesService()
    service._loaded = True
    service.core = Mock()
    service.core.read_namespaced_secret.side_effect = ApiException(status=404)

    result = service.set_build_secret("devops-platform", "build-config-7-git", {
        "username": "builder", "password": "not-for-database",
    })

    secret = service.core.create_namespaced_secret.call_args.args[1]
    assert result == "build-config-7-git"
    assert secret.data["username"] == "YnVpbGRlcg=="
    assert secret.data["password"] == "bm90LWZvci1kYXRhYmFzZQ=="
    assert secret.type == "Opaque"


def test_build_secret_recreates_legacy_secret_when_type_changes():
    service = KubernetesService()
    service._loaded = True
    service.core = Mock()
    service.core.read_namespaced_secret.return_value = SimpleNamespace(
        metadata=SimpleNamespace(
            labels={"kubepilot.io/build-credential": "true"},
            resource_version="12",
        ),
        type="Opaque",
    )

    service.set_build_secret(
        "devops-platform", "build-config-7-registry",
        {"config.json": "{}", ".dockerconfigjson": "{}"},
        secret_type="kubernetes.io/dockerconfigjson",
    )

    service.core.delete_namespaced_secret.assert_called_once_with(
        "build-config-7-registry", "devops-platform",
    )
    created = service.core.create_namespaced_secret.call_args.args[1]
    assert created.type == "kubernetes.io/dockerconfigjson"
    service.core.replace_namespaced_secret.assert_not_called()


def test_harbor_credentials_create_kaniko_docker_config():
    service = KubernetesService()
    secrets = {}
    secret_types = []

    def capture_secret(namespace, name, values, secret_type="Opaque"):
        secrets.update(values)
        secret_types.append(secret_type)
        return name

    service.set_build_secret = capture_secret

    git_secret, registry_secret = service.configure_build_credentials(
        "devops-platform", 12, registry_server="harbor.example.com",
        registry_username="robot", registry_password="token",
    )

    import base64
    import json

    config = json.loads(secrets["config.json"])
    assert git_secret is None
    assert registry_secret == "build-config-12-registry"
    assert config["auths"]["harbor.example.com"]["auth"] == base64.b64encode(b"robot:token").decode()
    assert secrets[".dockerconfigjson"] == secrets["config.json"]
    assert secret_types == ["kubernetes.io/dockerconfigjson"]


def test_build_job_uses_secret_refs_without_embedding_git_password(monkeypatch):
    service = KubernetesService()
    service._loaded = True
    service.ensure_namespace = lambda _: None
    service.batch = Mock()
    service.core = Mock()
    request = BuildRequest(
        name="web-release", git_url="https://git.example.com/team/repo.git",
        git_ref="main", destination="harbor.example.com/team/repo:1",
        git_auth_secret_name="build-config-5-git", registry_secret_name="build-config-5-registry",
        build_steps=[{
            "name": "frontend", "image": "node:20-alpine", "working_dir": "frontend",
            "command": "npm ci && npm run build", "env": {"CI": "true"},
        }, {
            "name": "backend", "image": "gradle:8-jdk17", "working_dir": ".",
            "command": "./gradlew :backend:bootJar", "env": {},
        }],
    )

    service.create_build_job(request, 5)

    job = service.batch.create_namespaced_job.call_args.args[1]
    spec = job.spec.template.spec
    clone = spec.init_containers[0]
    assert "git clone" in clone.command[2]
    assert "GIT_PASSWORD" in clone.command[2]
    assert "secret-token" not in clone.command[2]
    assert {item.name for item in clone.env} == {"GIT_USERNAME", "GIT_PASSWORD"}
    assert clone.env[1].value_from.secret_key_ref.name == "build-config-5-git"
    assert any(volume.name == "registry-auth" for volume in spec.volumes)
    assert [container.name for container in spec.init_containers] == [
        "git-clone", "compile-1-frontend", "compile-2-backend",
    ]
    frontend = spec.init_containers[1]
    assert frontend.working_dir == "/workspace/src/frontend"
    assert frontend.command == ["/bin/sh", "-ec", "npm ci && npm run build"]
    assert frontend.env[0].name == "CI" and frontend.env[0].value == "true"
    assert spec.init_containers[2].working_dir == "/workspace/src"
    assert spec.image_pull_secrets[0].name == "build-config-5-registry"


def test_build_status_aggregates_clone_compile_and_kaniko_logs():
    service = KubernetesService()
    service._loaded = True
    service.batch = Mock()
    service.core = Mock()
    service.batch.read_namespaced_job.return_value = SimpleNamespace(
        status=SimpleNamespace(succeeded=None, failed=1, active=None),
    )
    pod = SimpleNamespace(
        metadata=SimpleNamespace(name="build-web-1-pod"),
        spec=SimpleNamespace(init_containers=[
            SimpleNamespace(name="git-clone"),
            SimpleNamespace(name="compile-1-frontend"),
            SimpleNamespace(name="compile-2-backend"),
        ]),
    )
    service.core.list_namespaced_pod.return_value = SimpleNamespace(items=[pod])
    logs = {
        "git-clone": "cloned",
        "compile-1-frontend": "frontend built",
        "compile-2-backend": "backend built",
        "kaniko": "image pushed",
    }
    service.core.read_namespaced_pod_log.side_effect = lambda *args, **kwargs: logs[kwargs["container"]]

    result = service.build_status("devops-platform", "build-web-1")

    assert result["status"] == "failed"
    assert result["logs"].index("git-clone") < result["logs"].index("compile-1-frontend")
    assert result["logs"].index("compile-1-frontend") < result["logs"].index("compile-2-backend")
    assert result["logs"].index("compile-2-backend") < result["logs"].index("kaniko")


def test_build_history_delete_always_removes_kubernetes_job(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    request = Request({"type": "http", "method": "DELETE", "path": "/", "headers": [], "client": ("127.0.0.1", 1234)})
    removed = []
    monkeypatch.setattr(
        "app.main.k8s.delete_build_job",
        lambda namespace, name: removed.append((namespace, name)),
    )
    with Session(engine) as db:
        user = User(username="admin", password_hash="hash", email="admin@example.com", is_admin=True)
        build = ImageBuild(
            name="done", git_url="https://git/repo.git", destination="registry/app:1",
            namespace="builds", job_name="build-done-1", status="succeeded",
        )
        db.add_all([user, build])
        db.commit()
        build_id = build.id

        assert delete_build(build_id, request, user, db) == {"deleted": build_id}
        assert removed == [("builds", "build-done-1")]
        assert db.get(ImageBuild, build_id) is None


def test_build_retention_prunes_job_and_database_record(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    removed = []
    monkeypatch.setattr(
        "app.main.k8s.delete_build_job",
        lambda namespace, name: removed.append((namespace, name)),
    )
    with Session(engine) as db:
        config = BuildConfiguration(
            name="web", git_url="https://git/repo.git", destination="registry/app:1",
        )
        db.add(config)
        db.flush()
        builds = []
        for number in range(3):
            build = ImageBuild(
                name="web", config_id=config.id, git_url="https://git/repo.git",
                destination=f"registry/app:{number}", namespace="builds",
                job_name=f"build-web-{number}", status="succeeded",
            )
            db.add(build)
            builds.append(build)
        db.commit()

        assert _prune_build_history(db, config.id, 2) == 1
        db.commit()
        assert removed == [("builds", "build-web-0")]
        assert db.get(ImageBuild, builds[0].id) is None


def test_orphan_build_job_cleanup_only_removes_missing_database_ids(monkeypatch):
    test_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(test_engine)
    monkeypatch.setattr("app.main.SessionLocal", lambda: Session(test_engine))
    removed = []
    monkeypatch.setattr("app.main.k8s.list_managed_build_jobs", lambda: [
        {"build_id": 1, "namespace": "builds", "job_name": "kept"},
        {"build_id": 99, "namespace": "builds", "job_name": "orphan"},
    ])
    monkeypatch.setattr(
        "app.main.k8s.delete_build_job",
        lambda namespace, name: removed.append((namespace, name)),
    )
    with Session(test_engine) as db:
        db.add(ImageBuild(
            id=1, name="kept", git_url="https://git/repo.git",
            destination="registry/app:1", namespace="builds", job_name="kept",
        ))
        db.commit()

    assert cleanup_orphan_build_jobs() == 1
    assert removed == [("builds", "orphan")]


def test_comparison_operators():
    assert compare(90, ">", 80)
    assert compare(80, ">=", 80)
    assert compare(1, "==", 1)
    assert not compare(70, ">", 80)


def test_alert_notification_repeat_interval():
    now = datetime(2026, 11, 18, 12, 0, 0)
    last_sent = now - timedelta(minutes=30)

    assert notification_due(None, 30, now)
    assert notification_due(last_sent, 30, now)
    assert not notification_due(last_sent + timedelta(seconds=1), 30, now)
    assert not notification_due(last_sent, 0, now)


def test_alert_repeat_interval_schema_upgrade(monkeypatch):
    test_engine = create_engine("sqlite://")
    with test_engine.begin() as connection:
        connection.execute(text("CREATE TABLE alert_rules (id INTEGER PRIMARY KEY, template_id INTEGER NULL)"))
        connection.execute(text("CREATE TABLE alert_rule_templates (id INTEGER PRIMARY KEY)"))
        connection.execute(text("CREATE TABLE build_configurations (id INTEGER PRIMARY KEY)"))
    monkeypatch.setattr("app.db.engine", test_engine)

    from app.db import ensure_schema_upgrades

    ensure_schema_upgrades()

    assert "repeat_interval_minutes" in {column["name"] for column in inspect(test_engine).get_columns("alert_rules")}
    assert "repeat_interval_minutes" in {column["name"] for column in inspect(test_engine).get_columns("alert_rule_templates")}
    assert "git_auth_secret_name" in {column["name"] for column in inspect(test_engine).get_columns("build_configurations")}
    test_engine.dispose()


def test_alert_repeat_interval_validation():
    rule = AlertRuleCreate(
        name="one-time alert", metric="pod_cpu_percent", threshold=80,
        repeat_interval_minutes=0,
    )
    assert rule.repeat_interval_minutes == 0
    with pytest.raises(ValidationError):
        AlertRuleCreate(
            name="bad interval", metric="pod_cpu_percent", threshold=80,
            repeat_interval_minutes=10081,
        )


def test_special_alert_metrics_are_normalized():
    restarts = AlertRuleCreate(
        name="frequent restarts", metric="pod_restarts", operator=">",
        threshold=3, channel_ids=[2, 1, 2],
    )
    not_running = AlertRuleCreate(
        name="pod down", metric="pod_not_running", operator=">", threshold=99,
    )

    assert restarts.operator == ">="
    assert restarts.threshold == 3
    assert restarts.channel_ids == [1, 2]
    assert not_running.operator == "=="
    assert not_running.threshold == 1


def test_node_alert_rules_do_not_use_a_namespace():
    rule = AlertRuleCreate(
        name="node memory", metric="node_memory_percent", operator=">",
        threshold=85, namespace="production", target_pattern="worker-*",
    )

    assert rule.namespace == "_cluster"
    assert rule.target_pattern == "worker-*"


def test_node_disk_alert_and_mount_path_config():
    rule = AlertRuleCreate(
        name="disk full", metric="node_disk_percent", operator=">",
        threshold=85, namespace="production", target_pattern="worker-1:/data",
    )
    config = DiskMonitorConfig(paths=["/", "/data/", "/data"])

    assert rule.namespace == "_cluster"
    assert rule.target_pattern == "worker-1:/data"
    assert config.paths == ["/", "/data"]
    with pytest.raises(ValidationError, match="绝对路径"):
        DiskMonitorConfig(paths=["data"])


def test_user_payloads_support_personnel_management():
    created = UserCreate(
        username="ops.user-01", password="Strong9!password", email="ops@example.com",
        phone="+86 13800138000", role="tester",
    )
    updated = UserUpdate(
        email="new-ops@example.com", phone="021-12345678", password=None,
        role="admin", is_active=False,
    )
    reset = UserUpdate(email="ops@example.com", password="Strong9!password")

    assert created.username == "ops.user-01"
    assert created.phone == "+86 13800138000"
    assert updated.phone == "021-12345678"
    assert updated.password is None
    assert reset.password == "Strong9!password"
    assert created.role == "tester"
    assert updated.role == "admin"
    assert updated.is_active is False
    with pytest.raises(ValidationError, match="phone"):
        UserCreate(
            username="bad-phone", password="Strong9!password",
            email="bad@example.com", phone="not-a-phone",
        )


def test_notification_channel_message_template():
    title, message = render_channel_message(
        {
            "title_template": "KubePilot | {title}",
            "message_template": "告警详情:\n{message}\n标题: {title}",
        },
        "CPU 告警",
        "当前值 90%",
    )

    assert title == "KubePilot | CPU 告警"
    assert message == "告警详情:\n当前值 90%\n标题: CPU 告警"


def test_built_in_notification_templates_cover_all_channels():
    channel_types = {template["channel_type"] for template in DEFAULT_NOTIFICATION_TEMPLATES}
    names = [template["name"] for template in DEFAULT_NOTIFICATION_TEMPLATES]

    assert channel_types == {"email", "feishu", "dingtalk", "wecom", "phone_webhook"}
    assert len(names) == len(set(names)) == 5
    assert all("{severity}" in template["title_template"] for template in DEFAULT_NOTIFICATION_TEMPLATES)


def test_notification_template_supports_structured_alert_variables():
    title, message = render_channel_message(
        {
            "title_template": "[{severity}] {rule_name}",
            "message_template": "{target} 当前值 {value}，阈值 {operator} {threshold}",
        },
        "原始标题",
        "原始消息",
        {
            "severity": "严重", "rule_name": "节点 CPU 高负载",
            "target": "_cluster/worker-1", "value": "95.20",
            "operator": ">", "threshold": 80,
        },
    )

    assert title == "[严重] 节点 CPU 高负载"
    assert message == "_cluster/worker-1 当前值 95.20，阈值 > 80"


def test_notification_channel_requires_a_template_association():
    with pytest.raises(ValidationError, match="必须关联有效的通知模板"):
        ChannelCreate(
            name="飞书", channel_type="feishu",
            config={"url": "https://example.invalid/hook"},
        )

    channel = ChannelCreate(
        name="飞书", channel_type="feishu",
        config={"url": "https://example.invalid/hook", "notification_template_id": 3},
    )
    assert channel.config["notification_template_id"] == 3


def test_notification_template_overrides_channel_message_template():
    db = Mock()
    db.get.return_value = NotificationTemplate(
        id=7,
        name="飞书值班模板",
        channel_type="feishu",
        title_template="[值班] {title}",
        message_template="请及时处理：{message}",
    )

    result = apply_notification_template(db, {
        "url": "https://example.invalid/hook",
        "notification_template_id": 7,
        "title_template": "旧标题 {title}",
    })

    assert result["template_name"] == "飞书值班模板"
    assert result["title_template"] == "[值班] {title}"
    assert result["message_template"] == "请及时处理：{message}"


def test_rule_template_normalizes_special_metrics():
    template = AlertRuleTemplateCreate(
        name="重启告警模板", metric="pod_restarts", operator=">", threshold=2,
        namespace="production",
    )

    assert template.operator == ">="
    assert template.threshold == 2
    assert template.namespace == "production"


def test_on_call_shift_requires_end_after_start():
    starts_at = datetime(2026, 11, 18, 9, 0)
    valid = OnCallShiftCreate(
        user_id=1, starts_at=starts_at, ends_at=starts_at + timedelta(hours=8),
    )
    assert valid.ends_at.hour == 17

    with pytest.raises(ValidationError, match="值班结束时间必须晚于开始时间"):
        OnCallShiftCreate(user_id=1, starts_at=starts_at, ends_at=starts_at)


def test_deployment_options_reflect_running_container_and_service():
    deployment = client.V1Deployment(
        metadata=client.V1ObjectMeta(
            name="orders",
            namespace="production",
            annotations={"deployment.kubernetes.io/revision": "7"},
        ),
        spec=client.V1DeploymentSpec(
            replicas=3,
            selector=client.V1LabelSelector(match_labels={"app": "orders"}),
            template=client.V1PodTemplateSpec(
                spec=client.V1PodSpec(
                    containers=[
                        client.V1Container(
                            name="orders",
                            image="registry/production/orders:v7",
                            ports=[client.V1ContainerPort(container_port=8080)],
                            resources=client.V1ResourceRequirements(
                                requests={"cpu": "250m", "memory": "256Mi"},
                                limits={"cpu": "2", "memory": "2Gi"},
                            ),
                        )
                    ]
                )
            ),
        ),
        status=client.V1DeploymentStatus(
            replicas=3,
            ready_replicas=3,
            updated_replicas=3,
            available_replicas=3,
        ),
    )
    service = client.V1Service(
        spec=client.V1ServiceSpec(
            ports=[client.V1ServicePort(port=80, target_port=8080)]
        )
    )
    k8s = KubernetesService()
    k8s._loaded = True
    k8s.apps = Mock()
    k8s.core = Mock()
    k8s.apps.list_namespaced_deployment.return_value.items = [deployment]
    service.metadata = client.V1ObjectMeta(name="orders", namespace="production")
    k8s.core.list_namespaced_service.return_value.items = [service]

    result = k8s.list_deployments("production")

    assert result == [{
        "namespace": "production",
        "name": "orders",
        "replicas": 3,
        "ready_replicas": 3,
        "updated_replicas": 3,
        "available_replicas": 3,
        "image": "registry/production/orders:v7",
        "container_port": 8080,
        "ports": [8080],
        "cpu_request": "250m",
        "memory_request": "256Mi",
        "cpu_limit": "2",
        "memory_limit": "2Gi",
        "revision": 7,
    }]


def test_deploy_only_updates_main_container_image():
    application = client.V1Container(
        name="orders",
        image="registry/orders:v1",
        env=[client.V1EnvVar(name="DATABASE_URL", value="kept")],
        ports=[client.V1ContainerPort(container_port=8080)],
        resources=client.V1ResourceRequirements(
            requests={"cpu": "250m", "memory": "256Mi"},
            limits={"cpu": "2", "memory": "2Gi"},
        ),
        readiness_probe=client.V1Probe(
            tcp_socket=client.V1TCPSocketAction(port=8080)
        ),
    )
    sidecar = client.V1Container(name="metrics", image="metrics:v1")
    deployment = client.V1Deployment(
        metadata=client.V1ObjectMeta(
            name="orders", namespace="production", labels={"team": "shop"}
        ),
        spec=client.V1DeploymentSpec(
            replicas=2,
            selector=client.V1LabelSelector(match_labels={"app": "orders"}),
            template=client.V1PodTemplateSpec(
                metadata=client.V1ObjectMeta(labels={"app": "orders"}),
                spec=client.V1PodSpec(
                    containers=[application, sidecar],
                    volumes=[client.V1Volume(name="config")],
                ),
            ),
        ),
    )
    service = client.V1Service(
        metadata=client.V1ObjectMeta(name="orders", namespace="production"),
        spec=client.V1ServiceSpec(
            type="NodePort",
            selector={"app": "orders"},
            ports=[client.V1ServicePort(port=80, target_port=8080, node_port=30080)],
        ),
    )
    k8s = KubernetesService()
    k8s._loaded = True
    k8s.apps = Mock()
    k8s.core = Mock()
    k8s.apps.read_namespaced_deployment.return_value = deployment
    k8s.apps.replace_namespaced_deployment.side_effect = lambda _name, _namespace, body: body

    result = k8s.deploy(DeployRequest(
        namespace="production",
        name="orders",
        image="registry/orders:v2",
    ))

    updated = k8s.apps.replace_namespaced_deployment.call_args.args[2]
    assert result["action"] == "updated"
    assert result["replicas"] == 2
    assert updated.spec.replicas == 2
    assert updated.spec.template.spec.containers[0].image == "registry/orders:v2"
    assert updated.spec.template.spec.containers[0].env[0].value == "kept"
    assert updated.spec.template.spec.containers[0].ports[0].container_port == 8080
    assert updated.spec.template.spec.containers[0].readiness_probe.tcp_socket.port == 8080
    assert updated.spec.template.spec.containers[0].resources.requests == {
        "cpu": "250m", "memory": "256Mi",
    }
    assert updated.spec.template.spec.containers[0].resources.limits == {
        "cpu": "2", "memory": "2Gi",
    }
    assert updated.spec.template.spec.containers[1].name == "metrics"
    assert updated.spec.template.spec.volumes[0].name == "config"
    k8s.core.assert_not_called()


def test_list_deployments_does_not_invent_port_when_workload_has_none():
    deployment = client.V1Deployment(
        metadata=client.V1ObjectMeta(name="worker", namespace="production"),
        spec=client.V1DeploymentSpec(
            replicas=1,
            selector=client.V1LabelSelector(match_labels={"app": "worker"}),
            template=client.V1PodTemplateSpec(
                spec=client.V1PodSpec(
                    containers=[client.V1Container(name="worker", image="registry/worker:v1")]
                )
            ),
        ),
        status=client.V1DeploymentStatus(),
    )
    k8s = KubernetesService()
    k8s._loaded = True
    k8s.apps = Mock()
    k8s.core = Mock()
    k8s.apps.list_namespaced_deployment.return_value.items = [deployment]
    k8s.core.list_namespaced_service.return_value.items = []

    assert k8s.list_deployments("production")[0]["container_port"] is None


def test_pod_logs_support_container_metadata_and_headlamp_filters():
    pod = SimpleNamespace(
        spec=SimpleNamespace(
            containers=[SimpleNamespace(name="app"), SimpleNamespace(name="sidecar")],
            init_containers=[SimpleNamespace(name="init-db")],
            ephemeral_containers=[SimpleNamespace(name="debugger")],
        ),
        status=SimpleNamespace(
            container_statuses=[SimpleNamespace(name="app", restart_count=2)],
            init_container_statuses=[SimpleNamespace(name="init-db", restart_count=1)],
            ephemeral_container_statuses=[],
        ),
    )
    k8s = KubernetesService()
    k8s._loaded = True
    k8s.core = Mock()
    k8s.core.read_namespaced_pod.return_value = pod
    k8s.core.read_namespaced_pod_log.return_value = "2026-11-25T01:02:03Z ready\n"

    result = k8s.pod_logs(
        "production", "orders-1", "init-db", tail_lines=1000,
        since_seconds=900, previous=True, timestamps=False,
    )

    assert result["selected_container"] == "init-db"
    assert result["logs"].endswith("ready\n")
    assert result["containers"] == [
        {"name": "app", "type": "container", "restart_count": 2},
        {"name": "sidecar", "type": "container", "restart_count": 0},
        {"name": "init-db", "type": "init", "restart_count": 1},
        {"name": "debugger", "type": "ephemeral", "restart_count": 0},
    ]
    k8s.core.read_namespaced_pod_log.assert_called_once_with(
        "orders-1", "production", container="init-db", tail_lines=1000,
        since_seconds=900, previous=True, timestamps=False,
    )

    with pytest.raises(ValueError, match="上一实例日志"):
        k8s.pod_logs("production", "orders-1", "sidecar", previous=True)


def test_restart_pod_requires_controller_and_uses_graceful_delete():
    owned_pod = SimpleNamespace(
        metadata=SimpleNamespace(
            deletion_timestamp=None,
            owner_references=[SimpleNamespace(
                controller=True, kind="ReplicaSet", name="orders-7dbf9",
            )],
        ),
        status=SimpleNamespace(phase="Running"),
    )
    k8s = KubernetesService()
    k8s._loaded = True
    k8s.core = Mock()
    k8s.core.read_namespaced_pod.return_value = owned_pod

    result = k8s.restart_pod("production", "orders-1")

    assert result["controller_kind"] == "ReplicaSet"
    k8s.core.delete_namespaced_pod.assert_called_once()
    args = k8s.core.delete_namespaced_pod.call_args
    assert args.args[:2] == ("orders-1", "production")
    assert args.kwargs["body"].grace_period_seconds == 30

    owned_pod.metadata.owner_references = []
    with pytest.raises(ValueError, match="没有控制器接管"):
        k8s.restart_pod("production", "standalone")


def test_deployment_revisions_lists_image_and_creation_time_for_rollback():
    deployment = client.V1Deployment(
        metadata=client.V1ObjectMeta(
            name="orders", namespace="production",
            annotations={"deployment.kubernetes.io/revision": "4"},
        ),
        spec=client.V1DeploymentSpec(
            replicas=2,
            selector=client.V1LabelSelector(match_labels={"app": "orders"}),
            template=client.V1PodTemplateSpec(
                spec=client.V1PodSpec(containers=[client.V1Container(name="orders", image="orders:v4")])
            ),
        ),
    )
    old = client.V1ReplicaSet(
        metadata=client.V1ObjectMeta(
            name="orders-old", namespace="production",
            annotations={"deployment.kubernetes.io/revision": "3"},
            creation_timestamp=datetime(2026, 11, 19, 8, 30),
        ),
        spec=client.V1ReplicaSetSpec(
            replicas=0,
            selector=client.V1LabelSelector(match_labels={"app": "orders"}),
            template=client.V1PodTemplateSpec(
                spec=client.V1PodSpec(containers=[client.V1Container(name="orders", image="orders:v3")])
            ),
        ),
        status=client.V1ReplicaSetStatus(replicas=0, ready_replicas=0),
    )
    k8s = KubernetesService()
    k8s._loaded = True
    k8s.apps = Mock()
    k8s.apps.read_namespaced_deployment.return_value = deployment
    k8s.apps.list_namespaced_replica_set.return_value.items = [old]

    result = k8s.deployment_revisions("production", "orders")

    assert result["current_revision"] == 4
    assert result["items"][0]["revision"] == 3
    assert result["items"][0]["image"] == "orders:v3"
    assert result["items"][0]["created_at"].startswith("2026-11-19T08:30:00")


def test_deployment_rollout_status_reports_completed_progress():
    deployment = client.V1Deployment(
        metadata=client.V1ObjectMeta(
            name="orders", namespace="production", uid="deployment-uid", generation=8,
            annotations={"deployment.kubernetes.io/revision": "5"},
        ),
        spec=client.V1DeploymentSpec(
            replicas=2,
            selector=client.V1LabelSelector(match_labels={"app": "orders"}),
            template=client.V1PodTemplateSpec(
                spec=client.V1PodSpec(containers=[client.V1Container(name="orders", image="orders:v5")])
            ),
        ),
        status=client.V1DeploymentStatus(
            replicas=2, updated_replicas=2, ready_replicas=2,
            available_replicas=2, unavailable_replicas=0, observed_generation=8,
        ),
    )
    k8s = KubernetesService()
    k8s._loaded = True
    k8s.apps = Mock()
    k8s.core = Mock()
    k8s.apps.read_namespaced_deployment.return_value = deployment
    k8s.core.list_namespaced_pod.return_value.items = []
    k8s.core.list_namespaced_event.return_value.items = []

    result = k8s.deployment_rollout_status("production", "orders", "orders:v5")

    assert result["status"] == "succeeded"
    assert result["revision"] == 5
    assert result["ready_replicas"] == 2
    assert result["container_logs"] == ""


def test_generic_resource_update_preserves_identity_and_concurrency_version():
    current = {
        "apiVersion": "v1", "kind": "ConfigMap",
        "metadata": {
            "name": "demo", "namespace": "test", "resourceVersion": "12",
            "uid": "server-value", "managedFields": [{"manager": "kubectl"}],
        },
        "data": {"mode": "old"}, "status": {"ignored": True},
    }
    updated = {
        "apiVersion": "v1", "kind": "ConfigMap",
        "metadata": {"name": "demo", "namespace": "test", "resourceVersion": "13"},
        "data": {"mode": "new"},
    }
    resource = Mock(verbs=["get", "list", "update"])
    current_result = Mock()
    current_result.to_dict.return_value = current
    updated_result = Mock()
    updated_result.to_dict.return_value = updated
    k8s = KubernetesService()
    k8s._loaded = True
    k8s.dynamic = Mock()
    k8s.dynamic.resources.get.return_value = resource
    k8s.dynamic.get.return_value = current_result
    k8s.dynamic.replace.return_value = updated_result

    before, after = k8s.replace_namespaced_resource(
        "test", "v1", "ConfigMap", "demo",
        {"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": "demo"}, "data": {"mode": "new"}},
    )

    body = k8s.dynamic.replace.call_args.kwargs["body"]
    assert body["metadata"]["namespace"] == "test"
    assert body["metadata"]["resourceVersion"] == "12"
    assert "managedFields" not in before["metadata"]
    assert "status" not in before
    assert after["data"]["mode"] == "new"


def test_resource_type_discovery_ignores_dynamic_list_resources():
    resource = SimpleNamespace(
        namespaced=True, verbs=["get", "list", "update"], group="",
        group_version="v1", kind="ConfigMap", name="configmaps", preferred=True,
    )
    resource_list = SimpleNamespace(
        kind="ConfigMapList", verbs=["get", "list"], group="",
        group_version="v1", name="configmaps",
    )
    k8s = KubernetesService()
    k8s._loaded = True
    k8s.dynamic = Mock()
    k8s.dynamic.resources.search.return_value = [resource_list, resource]

    types = k8s.list_namespaced_resource_types()

    assert types == [{
        "api_version": "v1", "kind": "ConfigMap", "plural": "configmaps",
        "editable": True,
    }]


def test_resource_type_discovery_only_returns_supported_types():
    configmap = SimpleNamespace(
        namespaced=True, verbs=["get", "list", "update"], group="",
        group_version="v1", kind="ConfigMap", name="configmaps", preferred=True,
    )
    custom_resource = SimpleNamespace(
        namespaced=True, verbs=["get", "list", "update"], group="example.io",
        group_version="example.io/v1", kind="Widget", name="widgets", preferred=True,
    )
    k8s = KubernetesService()
    k8s._loaded = True
    k8s.dynamic = Mock()
    k8s.dynamic.resources.search.return_value = [custom_resource, configmap]

    assert [item["kind"] for item in k8s.list_namespaced_resource_types()] == ["ConfigMap"]


def test_alert_rule_and_event_deletion_preserve_related_history():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    request = Request({"type": "http", "method": "DELETE", "path": "/", "headers": [], "client": ("127.0.0.1", 1234)})
    with Session(engine) as db:
        user = User(username="admin", password_hash="hash", email="admin@example.com", is_admin=True)
        rule = AlertRule(name="memory", metric="node_memory_percent", threshold=80)
        db.add_all([user, rule])
        db.flush()
        event = AlertEvent(
            rule_id=rule.id, fingerprint="memory:node-1", title="memory high",
            message="threshold exceeded", target="node-1",
        )
        subscription = AlertSubscription(name="ops", user_id=user.id, rule_id=rule.id, channel_ids=[])
        db.add_all([event, subscription])
        db.commit()
        rule_id, event_id = rule.id, event.id

        assert delete_alert_rule(rule_id, request, user, db) == {"deleted": rule_id}
        assert db.get(AlertRule, rule_id) is None
        assert db.get(AlertEvent, event_id).rule_id is None
        assert db.get(AlertSubscription, subscription.id).rule_id is None

        assert delete_alert_event(event_id, request, user, db) == {"deleted": event_id}
        assert db.get(AlertEvent, event_id) is None
        assert len(db.scalars(select(AuditLog)).all()) == 2


def test_legacy_rollback_history_recovers_image_from_generated_revision(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    created_at = datetime(2026, 11, 19, 7, 15, 21)
    with Session(engine) as db:
        record = DeploymentRecord(
            namespace="devops-platform", workload="devops-platform",
            image="rollback", revision=40, status="submitted",
            operator="admin", created_at=created_at,
        )
        audit = AuditLog(
            username="admin", action="deployment.rollback",
            resource="devops-platform/devops-platform",
            detail={"from_revision": 41, "target_revision": 40},
            created_at=created_at,
        )
        db.add_all([record, audit])
        db.commit()
        monkeypatch.setattr(
            "app.main.k8s.deployment_revisions",
            lambda _namespace, _name: {"items": [
                {"revision": 42, "image": "harbor/devops:v1.0.1"},
                {"revision": 41, "image": "harbor/devops:v1.0.0"},
            ]},
        )

        _repair_legacy_rollback_records([record], db)

        assert record.image == "harbor/devops:v1.0.1"
        assert record.strategy == "Rollback"


def test_resource_editor_requires_non_empty_yaml():
    request = ResourceSaveRequest(content="apiVersion: v1\nkind: ConfigMap\n")
    assert "ConfigMap" in request.content
    with pytest.raises(ValidationError):
        ResourceSaveRequest(content="")

