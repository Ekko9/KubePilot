#!/usr/bin/env python3
"""Verify first-login password reset and audit permissions against a live platform."""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone


BASE_URL = os.environ.get("KUBEPILOT_URL", "http://127.0.0.1:30088").rstrip("/")
ADMIN_USERNAME = os.environ.get("KUBEPILOT_USERNAME", "admin")
ADMIN_PASSWORD = os.environ["KUBEPILOT_PASSWORD"]


def call(method: str, path: str, body=None, token: str = "", expected: int = 200):
    data = None if body is None else json.dumps(body).encode()
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(BASE_URL + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            status = response.status
            raw = response.read()
    except urllib.error.HTTPError as exc:
        status = exc.code
        raw = exc.read()
    if status != expected:
        raise AssertionError(f"{method} {path}: expected {expected}, got {status}: {raw.decode(errors='replace')}")
    return json.loads(raw) if raw else None


def main() -> None:
    admin = call("POST", "/api/auth/login", {
        "username": ADMIN_USERNAME, "password": ADMIN_PASSWORD,
    })
    admin_token = admin["access_token"]
    builtin_admin = next(item for item in call("GET", "/api/users", token=admin_token) if item["username"] == ADMIN_USERNAME)
    assert builtin_admin["is_builtin_admin"] is True
    call("DELETE", f"/api/users/{builtin_admin['id']}", token=admin_token, expected=400)
    suffix = str(int(time.time()))
    username = f"verify-auth-{suffix}"
    initial_password = "Initial-Test-123!"
    changed_password = "Changed-Test-456!"
    notification_record_ids = [
        int(value) for value in os.environ.get("KUBEPILOT_TEST_NOTIFICATION_RECORD_IDS", "").split(",") if value
    ]
    incident_id = int(os.environ["KUBEPILOT_TEST_INCIDENT_ID"]) if os.environ.get("KUBEPILOT_TEST_INCIDENT_ID") else None
    user_id = None
    subscription_id = None
    shift_id = None
    try:
        call("POST", "/api/users", {
            "username": f"weak-{username}", "password": "weakpass",
            "display_name": "弱密码测试", "email": f"weak-{username}@example.com",
            "role": "operator",
        }, admin_token, expected=422)
        created = call("POST", "/api/users", {
            "username": username, "password": initial_password,
            "display_name": "在线验收用户", "email": f"{username}@example.com",
            "role": "operator",
        }, admin_token)
        user_id = created["id"]
        assert created["must_change_password"] is True
        call("PUT", f"/api/users/{user_id}", {
            "display_name": "在线验收用户", "email": f"{username}@example.com",
            "password": "weakpass", "role": "operator", "is_active": True,
        }, admin_token, expected=422)

        first_login = call("POST", "/api/auth/login", {
            "username": username, "password": initial_password,
        })
        assert first_login["must_change_password"] is True
        user_token = first_login["access_token"]
        call("GET", "/api/namespaces", token=user_token, expected=403)

        call("POST", "/api/auth/change-password", {
            "current_password": initial_password, "new_password": "weakpass",
        }, user_token, expected=422)
        call("POST", "/api/auth/change-password", {
            "current_password": initial_password, "new_password": changed_password,
        }, user_token)
        call("GET", "/api/namespaces", token=user_token)
        subscription = call("POST", "/api/alert-subscriptions", {
            "name": f"verify-subscription-{suffix}", "user_id": user_id,
            "rule_id": None, "channel_ids": [], "enabled": True,
        }, admin_token)
        subscription_id = subscription["id"]
        call("PUT", f"/api/alert-subscriptions/{subscription_id}", {
            "name": f"verify-subscription-updated-{suffix}", "user_id": user_id,
            "rule_id": None, "channel_ids": [], "enabled": False,
        }, admin_token)
        starts_at = datetime.now(timezone.utc) + timedelta(days=1)
        shift = call("POST", "/api/on-call-shifts", {
            "user_id": user_id, "starts_at": starts_at.isoformat(),
            "ends_at": (starts_at + timedelta(hours=8)).isoformat(), "note": "verify",
        }, admin_token)
        shift_id = shift["id"]
        call("PUT", f"/api/on-call-shifts/{shift_id}", {
            "user_id": user_id, "starts_at": starts_at.isoformat(),
            "ends_at": (starts_at + timedelta(hours=9)).isoformat(), "note": "verify-updated",
        }, admin_token)
        call("GET", "/api/alert-subscriptions", token=user_token)
        call("GET", "/api/notification-records", token=user_token)
        call("GET", "/api/on-call-shifts", token=user_token)
        call("DELETE", "/api/alert-subscriptions/999999999", token=user_token, expected=403)
        call("DELETE", "/api/on-call-shifts/999999999", token=user_token, expected=403)
        call("DELETE", "/api/notification-records/999999999", token=user_token, expected=403)
        incident_workflow = "not-run"
        if incident_id is not None:
            call("PUT", f"/api/incidents/{incident_id}", {
                "status": "acknowledged", "assignee": username, "note": "在线验收：开始排查",
            }, user_token)
            call("PUT", f"/api/incidents/{incident_id}", {
                "status": "resolved", "assignee": username, "note": "在线验收：问题已解决",
            }, user_token)
            detail = call("GET", f"/api/incidents/{incident_id}", token=user_token)
            assert detail["status"] == "resolved" and detail["resolved_by"] == username
            assert len(detail["activities"]) >= 2
            incident_workflow = "processing-and-resolution-passed"
        call("GET", "/api/audit-logs", token=user_token, expected=403)
        call("POST", "/api/auth/logout", token=user_token)

        notification_delete_status = "permission-only"
        if notification_record_ids:
            call("DELETE", f"/api/notification-records/{notification_record_ids[0]}", token=admin_token)
            if len(notification_record_ids) > 1:
                result = call("DELETE", "/api/notification-records/batch", {
                    "ids": notification_record_ids[1:],
                }, admin_token)
                assert result["deleted"] == len(notification_record_ids) - 1
            notification_delete_status = "single-and-batch-passed"

        logs = call("GET", f"/api/audit-logs?username={username}&limit=50", token=admin_token)
        actions = {item["action"] for item in logs}
        required = {"auth.login", "auth.password_change", "auth.logout"}
        assert required.issubset(actions), (required, actions)
        creation_logs = call("GET", "/api/audit-logs?action=user.create&limit=2000", token=admin_token)
        assert any(item["resource"] == "在线验收用户" for item in creation_logs), username
        batch_target = next(item for item in logs if item["action"] == "auth.login")
        batch_result = call("DELETE", "/api/audit-logs/batch", {"ids": [batch_target["id"]]}, admin_token)
        assert batch_result["deleted"] == 1
        print(json.dumps({
            "status": "passed", "username": username,
            "verified_actions": sorted(required | {"user.create"}),
            "strong_password_policy": "create-reset-and-first-login-passed",
            "ordinary_audit_access": "forbidden", "builtin_admin_delete": "forbidden",
            "batch_delete": "passed", "ordinary_subscription_notification_on_call": "read_only",
            "admin_subscription_on_call_update": "passed",
            "admin_notification_record_delete": notification_delete_status,
            "incident_workflow": incident_workflow,
        }, ensure_ascii=False))
    finally:
        if shift_id is not None:
            call("DELETE", f"/api/on-call-shifts/{shift_id}", token=admin_token)
        if subscription_id is not None:
            call("DELETE", f"/api/alert-subscriptions/{subscription_id}", token=admin_token)
        if user_id is not None:
            call("DELETE", f"/api/users/{user_id}", token=admin_token)


if __name__ == "__main__":
    main()
