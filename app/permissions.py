from __future__ import annotations

from typing import Any


ROLE_LABELS = {
    "tester": "测试",
    "developer": "研发",
    "operator": "运维",
    "admin": "管理员",
}

MENU_GROUPS = (
    {"code": "overview", "name": "平台总览", "items": (
        ("dashboard", "平台总览"),
    )},
    {"code": "resource-monitoring", "name": "资源监控", "items": (
        ("pods", "Pod 监控"), ("nodes", "服务器监控"),
    )},
    {"code": "resource-management", "name": "资源管理", "items": (
        ("resources", "资源管理"),
    )},
    {"code": "image-build", "name": "镜像构建", "items": (
        ("build-configs", "构建配置"), ("build-history", "执行历史"),
    )},
    {"code": "release-management", "name": "发布管理", "items": (
        ("workloads", "更新发布"), ("release-history", "发布历史"),
    )},
    {"code": "alert-management", "name": "告警管理", "items": (
        ("alerts", "告警规则"), ("alert-events", "告警事件"),
        ("rule-templates", "规则模板"), ("alert-subscriptions", "告警订阅"),
    )},
    {"code": "incident-management", "name": "故障管理", "items": (
        ("incidents", "故障中心"),
    )},
    {"code": "notification-management", "name": "通知管理", "items": (
        ("settings", "通知对象"), ("notification-templates", "通知模板"),
        ("notification-records", "通知记录"),
    )},
    {"code": "duty-management", "name": "值班管理", "items": (
        ("on-call", "值班日程"),
    )},
    {"code": "system-management", "name": "系统管理", "items": (
        ("users", "用户管理"), ("roles", "角色管理"),
        ("permissions", "权限管理"), ("audit-logs", "日志审计"),
    )},
)

ALL_MENU_CODES = tuple(
    code for group in MENU_GROUPS for code, _ in group["items"]
)

ROLE_DEFAULT_PERMISSIONS = {
    "tester": (
        "dashboard", "pods", "resources", "release-history",
        "alert-events", "incidents", "notification-records", "on-call",
    ),
    "developer": (
        "dashboard", "pods", "nodes", "resources", "build-configs",
        "build-history", "workloads", "release-history", "alert-events",
        "incidents", "notification-records", "on-call",
    ),
    "operator": tuple(code for code in ALL_MENU_CODES if code not in {
        "users", "roles", "permissions", "audit-logs",
    }),
    "admin": ALL_MENU_CODES,
}


def normalize_role(role: str | None, is_admin: bool = False) -> str:
    if is_admin or role == "admin":
        return "admin"
    return role if role in ROLE_LABELS else "operator"


def effective_menu_permissions(user: Any) -> list[str]:
    role = normalize_role(getattr(user, "role", None), bool(getattr(user, "is_admin", False)))
    if role == "admin":
        return list(ALL_MENU_CODES)
    custom = getattr(user, "menu_permissions", None)
    source = custom if isinstance(custom, list) else ROLE_DEFAULT_PERMISSIONS[role]
    allowed = set(ALL_MENU_CODES)
    return [code for code in ALL_MENU_CODES if code in source and code in allowed]


def permission_catalog() -> list[dict[str, Any]]:
    return [
        {
            "code": group["code"],
            "name": group["name"],
            "items": [{"code": code, "name": name} for code, name in group["items"]],
        }
        for group in MENU_GROUPS
    ]
