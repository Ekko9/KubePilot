#!/usr/bin/env python3
"""Non-destructive online API regression suite for KubePilot.

The caller supplies credentials through environment variables and prepares an
isolated Kubernetes namespace plus a ConfigMap and Deployment fixture.
All database objects created here use a unique ``regression-`` prefix and are
removed in ``finally`` blocks.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone


BASE_URL = os.environ.get("KUBEPILOT_URL", "http://127.0.0.1:30088").rstrip("/")
USERNAME = os.environ.get("KUBEPILOT_USERNAME", "admin")
PASSWORD = os.environ["KUBEPILOT_PASSWORD"]
TEST_NAMESPACE = os.environ.get("KUBEPILOT_TEST_NAMESPACE", "kubepilot-regression")
TEST_DEPLOYMENT = os.environ.get("KUBEPILOT_TEST_DEPLOYMENT", "release-smoke")
TEST_CONFIGMAP = os.environ.get("KUBEPILOT_TEST_CONFIGMAP", "resource-smoke")
CURRENT_IMAGE = os.environ.get(
    "KUBEPILOT_CURRENT_IMAGE",
    "registry.example.com/team/kubepilot-devops:v1.0.0",
)
SUFFIX = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
TOKEN = ""
RESULTS: list[dict] = []
CREATED: dict[str, list[int]] = {
    "subscriptions": [], "rules": [], "rule_templates": [], "channels": [],
    "notification_templates": [], "shifts": [], "users": [], "build_configs": [],
    "builds": [],
}


def request(method: str, path: str, body=None, expected=(200, 201)):
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if TOKEN:
        headers["Authorization"] = f"Bearer {TOKEN}"
    req = urllib.request.Request(BASE_URL + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            raw = response.read()
            payload = json.loads(raw) if raw else None
            if response.status not in expected:
                raise AssertionError(f"{method} {path}: HTTP {response.status}")
            return payload
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        raise AssertionError(f"{method} {path}: HTTP {exc.code} {detail}") from exc


def check(name: str, callback):
    started = time.monotonic()
    try:
        detail = callback()
        RESULTS.append({"name": name, "status": "passed", "seconds": round(time.monotonic() - started, 2), "detail": detail})
        print(f"PASS {name}: {detail}", flush=True)
    except Exception as exc:
        RESULTS.append({"name": name, "status": "failed", "seconds": round(time.monotonic() - started, 2), "detail": str(exc)})
        print(f"FAIL {name}: {exc}", flush=True)


def cleanup(method: str, path: str):
    try:
        request(method, path)
    except Exception as exc:
        print(f"CLEANUP-WARN {method} {path}: {exc}", flush=True)


def wait_release(execution_id: int, timeout=180):
    deadline = time.monotonic() + timeout
    latest = None
    while time.monotonic() < deadline:
        latest = request("GET", f"/api/deployment-history/{execution_id}/status")
        if latest["status"] in {"succeeded", "failed"}:
            return latest
        time.sleep(2)
    raise AssertionError(f"release #{execution_id} timed out: {latest}")


def wait_build(build_id: int, timeout=420):
    deadline = time.monotonic() + timeout
    latest = None
    while time.monotonic() < deadline:
        latest = request("GET", f"/api/builds/{build_id}")
        if latest["status"] in {"succeeded", "failed"}:
            return latest
        time.sleep(3)
    raise AssertionError(f"build #{build_id} timed out: {latest}")


def main() -> int:
    global TOKEN
    login = request("POST", "/api/auth/login", {"username": USERNAME, "password": PASSWORD})
    TOKEN = login["access_token"]
    print(f"Authenticated as {login['username']} (admin={login['is_admin']})", flush=True)

    check("health and readiness", lambda: (
        request("GET", "/healthz"), request("GET", "/readyz")
    ))
    check("current user", lambda: request("GET", "/api/me"))
    check("overview", lambda: request("GET", "/api/overview"))
    check("namespaces", lambda: {"count": len(request("GET", "/api/namespaces"))})
    check("nodes, cpu, memory and disks", lambda: {
        "count": len(request("GET", "/api/nodes")),
        "disk_paths": request("GET", "/api/node-disk-config")["paths"],
    })
    check("pods and resource metrics", lambda: {"count": len(request("GET", "/api/pods"))})
    check("deployments", lambda: {"count": len(request("GET", "/api/deployments"))})
    check("release history", lambda: {"count": len(request("GET", "/api/deployment-history?limit=500"))})
    check("roles", lambda: request("GET", "/api/roles"))
    check("audit logs", lambda: {"count": len(request("GET", "/api/audit-logs?limit=20"))})
    check("notification records", lambda: {"count": len(request("GET", "/api/notification-records?limit=20"))})
    check("alert events and incidents", lambda: {
        "all": len(request("GET", "/api/alerts?limit=50")),
        "firing": len(request("GET", "/api/alerts?status=firing&limit=50")),
    })
    check("prometheus metrics", lambda: urllib.request.urlopen(BASE_URL + "/metrics", timeout=60).status)

    def release_flow():
        before = next(
            item for item in request("GET", f"/api/deployments?namespace={TEST_NAMESPACE}")
            if item["name"] == TEST_DEPLOYMENT
        )
        run = request("POST", "/api/deployments", {
            "namespace": TEST_NAMESPACE, "name": TEST_DEPLOYMENT, "image": CURRENT_IMAGE,
        })
        completed = wait_release(run["execution_id"])
        assert completed["status"] == "succeeded", completed
        assert completed["events"], "rollout must expose Kubernetes events"
        assert "container_logs" in completed, "rollout status must expose the container log stream"
        revisions = request("GET", f"/api/deployments/{TEST_NAMESPACE}/{TEST_DEPLOYMENT}/revisions")
        target = next((item for item in revisions["items"] if item["image"] == before["image"]), None)
        rollback_execution = None
        if target:
            rollback = request(
                "POST", f"/api/deployments/{TEST_NAMESPACE}/{TEST_DEPLOYMENT}/rollback",
                {"revision": target["revision"]},
            )
            rolled_back = wait_release(rollback["execution_id"])
            assert rolled_back["status"] == "succeeded", rolled_back
            rollback_execution = rollback["execution_id"]
        request("POST", f"/api/deployments/{TEST_NAMESPACE}/{TEST_DEPLOYMENT}/scale", {"replicas": 2})
        request("POST", f"/api/deployments/{TEST_NAMESPACE}/{TEST_DEPLOYMENT}/scale", {"replicas": 1})
        request("POST", f"/api/deployments/{TEST_NAMESPACE}/{TEST_DEPLOYMENT}/restart", {})
        pods = []
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            pods = [
                item for item in request("GET", "/api/pods")
                if item["namespace"] == TEST_NAMESPACE and item["phase"] == "Running"
                and str(item.get("ready", "")).startswith("1/")
            ]
            if pods:
                break
            time.sleep(2)
        assert pods, "release smoke running pod missing"
        logs = request("GET", f"/api/pods/{TEST_NAMESPACE}/{pods[0]['name']}/logs?tail_lines=20")
        assert "logs" in logs
        return {"upgrade_execution": run["execution_id"], "rollback_execution": rollback_execution}

    check("rolling upgrade, live status, rollback, scale, restart and pod logs", release_flow)

    def resource_flow():
        types = request("GET", "/api/k8s/resource-types")
        assert any(item["kind"] == "ConfigMap" for item in types)
        query = urllib.parse.urlencode({"namespace": TEST_NAMESPACE, "api_version": "v1", "kind": "ConfigMap"})
        resources = request("GET", "/api/k8s/resources?" + query)
        assert any(item["name"] == TEST_CONFIGMAP for item in resources)
        base = {"namespace": TEST_NAMESPACE, "api_version": "v1", "kind": "ConfigMap", "name": TEST_CONFIGMAP}
        detail_query = urllib.parse.urlencode(base)
        request("GET", "/api/k8s/resource?" + detail_query)
        for value in ("first", "second"):
            content = (
                "apiVersion: v1\nkind: ConfigMap\nmetadata:\n"
                f"  name: {TEST_CONFIGMAP}\n  namespace: {TEST_NAMESPACE}\n"
                f"data:\n  regression: {value}\n"
            )
            request("PUT", "/api/k8s/resource?" + detail_query, {"content": content})
        diff = request("POST", "/api/k8s/resource/diff?" + detail_query, {"content": content})
        assert "regression" in diff["diff"]
        rolled = request("POST", "/api/k8s/resource/rollback?" + detail_query, {"version": None})
        assert rolled["history"]
        return {"types": len(types), "history": len(rolled["history"])}

    check("resource list, view, edit, diff and rollback", resource_flow)

    user_id = None
    template_id = None
    rule_template_id = None
    channel_ids: list[int] = []
    rule_id = None
    subscription_id = None
    shift_id = None
    try:
        user = request("POST", "/api/users", {
            "username": f"regression-{SUFFIX}", "password": "Regression123!",
            "display_name": "回归测试用户",
            "email": f"regression-{SUFFIX}@example.com", "role": "operator",
        })
        user_id = user["id"]
        CREATED["users"].append(user_id)
        updated_user = request("PUT", f"/api/users/{user_id}", {
            "email": f"updated-{SUFFIX}@example.com", "password": None,
            "is_admin": False, "is_active": True,
        })
        check("user create, edit and list", lambda: {
            "id": user_id, "email": updated_user["email"],
            "listed": any(item["id"] == user_id for item in request("GET", "/api/users")),
        })

        notification_template = request("POST", "/api/notification-templates", {
            "name": f"regression-email-{SUFFIX}", "channel_type": "email",
            "title_template": "[Regression] {title}", "message_template": "{message}",
        })
        template_id = notification_template["id"]
        CREATED["notification_templates"].append(template_id)
        channel_specs = {
            "email": {"host": "smtp.invalid", "from": "regression@example.com", "to": "one@example.com,two@example.com"},
            "feishu": {"url": "https://example.invalid/feishu"},
            "dingtalk": {"url": "https://example.invalid/dingtalk"},
            "wecom": {"url": "https://example.invalid/wecom"},
            "phone_webhook": {"url": "https://example.invalid/phone", "phones": "13800000000,13900000000"},
        }
        template_ids = {"email": template_id}
        for channel_type in ("feishu", "dingtalk", "wecom", "phone_webhook"):
            item = request("POST", "/api/notification-templates", {
                "name": f"regression-{channel_type}-{SUFFIX}", "channel_type": channel_type,
                "title_template": "[Regression] {title}", "message_template": "{message}",
            })
            template_ids[channel_type] = item["id"]
            CREATED["notification_templates"].append(item["id"])
        for channel_type, config in channel_specs.items():
            config = {**config, "notification_template_id": template_ids[channel_type]}
            channel = request("POST", "/api/channels", {
                "name": f"regression-{channel_type}-{SUFFIX}", "channel_type": channel_type,
                "config": config, "enabled": False,
            })
            channel_ids.append(channel["id"])
            CREATED["channels"].append(channel["id"])
            returned = request("GET", f"/api/channels/{channel['id']}/config")
            request("PUT", f"/api/channels/{channel['id']}", {
                "name": returned["name"], "channel_type": returned["channel_type"],
                "config": returned["config"], "enabled": False,
            })
        check("notification templates and all five channel types", lambda: {
            "templates": len(template_ids), "channels": len(channel_ids),
        })

        rule_template = request("POST", "/api/alert-rule-templates", {
            "name": f"regression-rule-template-{SUFFIX}", "description": "online regression",
            "metric": "node_cpu_percent", "operator": ">", "threshold": 99,
            "duration_seconds": 60, "repeat_interval_minutes": 30,
            "severity": "warning", "namespace": "_cluster", "target_pattern": "regression-none",
        })
        rule_template_id = rule_template["id"]
        CREATED["rule_templates"].append(rule_template_id)
        rule_payload = {
            "name": f"regression-rule-{SUFFIX}", "metric": "node_cpu_percent",
            "operator": ">", "threshold": 99, "duration_seconds": 60,
            "repeat_interval_minutes": 30, "severity": "warning",
            "namespace": "_cluster", "target_pattern": "regression-none",
            "template_id": rule_template_id, "channel_ids": [channel_ids[0]], "enabled": False,
        }
        rule = request("POST", "/api/alert-rules", rule_payload)
        rule_id = rule["id"]
        CREATED["rules"].append(rule_id)
        rule_payload["threshold"] = 98
        request("PUT", f"/api/alert-rules/{rule_id}", rule_payload)
        subscription = request("POST", "/api/alert-subscriptions", {
            "name": f"regression-subscription-{SUFFIX}", "user_id": user_id,
            "rule_id": rule_id, "channel_ids": [channel_ids[0]], "enabled": True,
        })
        subscription_id = subscription["id"]
        CREATED["subscriptions"].append(subscription_id)
        check("rule template, alert rule edit and subscription", lambda: {
            "rule_template": rule_template_id, "rule": rule_id, "subscription": subscription_id,
        })

        starts_at = datetime.now(timezone.utc) + timedelta(days=1)
        shift = request("POST", "/api/on-call-shifts", {
            "user_id": user_id, "starts_at": starts_at.isoformat(),
            "ends_at": (starts_at + timedelta(hours=8)).isoformat(), "note": "online regression",
        })
        shift_id = shift["id"]
        CREATED["shifts"].append(shift_id)
        check("on-call create and list", lambda: {
            "id": shift_id,
            "listed": any(item["id"] == shift_id for item in request("GET", "/api/on-call-shifts")),
        })
    finally:
        if shift_id:
            cleanup("DELETE", f"/api/on-call-shifts/{shift_id}")
        if subscription_id:
            cleanup("DELETE", f"/api/alert-subscriptions/{subscription_id}")
        if rule_id:
            cleanup("DELETE", f"/api/alert-rules/{rule_id}")
        if rule_template_id:
            cleanup("DELETE", f"/api/alert-rule-templates/{rule_template_id}")
        for channel_id in reversed(channel_ids):
            cleanup("DELETE", f"/api/channels/{channel_id}")
        for item_id in reversed(CREATED["notification_templates"]):
            cleanup("DELETE", f"/api/notification-templates/{item_id}")
        if user_id:
            cleanup("DELETE", f"/api/users/{user_id}")

    def build_config_flow():
        config = request("POST", "/api/build-configs", {
            "name": f"regression-build-{SUFFIX}", "description": "online regression",
            "enabled": False, "git_url": "https://example.invalid/repository.git",
            "git_ref": "main", "context_dir": ".", "dockerfile": "Dockerfile",
            "destination": f"harbor.invalid/regression/app:{SUFFIX}",
            "namespace": TEST_NAMESPACE, "git_secret_name": "", "registry_secret_name": "",
            "git_username": "", "git_password": "", "registry_username": "", "registry_password": "",
            "clear_git_credentials": False, "clear_registry_credentials": False,
            "build_args": {"REGRESSION": "true"}, "timeout_seconds": 60,
            "allow_concurrent": False, "retention_count": 2,
        })
        CREATED["build_configs"].append(config["id"])
        payload = {
            "name": config["name"], "description": "online regression updated", "enabled": False,
            "git_url": config["git_url"], "git_ref": "main", "context_dir": ".",
            "dockerfile": "Dockerfile", "destination": config["destination"],
            "namespace": TEST_NAMESPACE, "git_secret_name": "", "registry_secret_name": "",
            "git_username": "", "git_password": "", "registry_username": "", "registry_password": "",
            "clear_git_credentials": False, "clear_registry_credentials": False,
            "build_args": {"REGRESSION": "updated"}, "timeout_seconds": 60,
            "allow_concurrent": False, "retention_count": 2,
        }
        updated = request("PUT", f"/api/build-configs/{config['id']}", payload)
        assert updated["description"].endswith("updated")
        cleanup("DELETE", f"/api/build-configs/{config['id']}")
        return {"id": config["id"], "listed": len(request("GET", "/api/build-configs"))}

    check("build configuration create, edit, list and delete", build_config_flow)
    def build_history_flow():
        history = request("GET", "/api/builds?limit=500")
        if not history:
            return {"count": 0, "latest": None, "detail_checked": False}
        latest = request("GET", f"/api/builds/{history[0]['id']}")
        return {"count": len(history), "latest": latest["status"], "detail_checked": True}

    check("build history list and optional log endpoint", build_history_flow)

    passed = sum(item["status"] == "passed" for item in RESULTS)
    failed = len(RESULTS) - passed
    print("REGRESSION_SUMMARY=" + json.dumps({
        "passed": passed, "failed": failed, "results": RESULTS,
    }, ensure_ascii=False), flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
