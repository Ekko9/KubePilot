from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator


PASSWORD_POLICY_MESSAGE = "密码至少 8 位，且必须包含大写字母、小写字母、数字和特殊字符（空格不算特殊字符）"


def validate_strong_password(value: str) -> str:
    if (
        len(value) < 8
        or not any(character.isupper() for character in value)
        or not any(character.islower() for character in value)
        or not any(character.isdigit() for character in value)
        or not any(not character.isalnum() and not character.isspace() for character in value)
    ):
        raise ValueError(PASSWORD_POLICY_MESSAGE)
    return value


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    username: str
    display_name: str
    is_admin: bool
    role: Literal["tester", "developer", "operator", "admin"]
    role_name: str
    menu_permissions: list[str] = Field(default_factory=list)
    must_change_password: bool = False


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)

    @field_validator("new_password")
    @classmethod
    def validate_new_password_strength(cls, value: str) -> str:
        return validate_strong_password(value)

    @model_validator(mode="after")
    def validate_password_change(self):
        if self.current_password == self.new_password:
            raise ValueError("新密码不能与当前密码相同")
        return self


class AuditLogBatchDelete(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=500)

    @field_validator("ids")
    @classmethod
    def normalize_ids(cls, value: list[int]) -> list[int]:
        if any(item <= 0 for item in value):
            raise ValueError("审计日志 ID 必须为正整数")
        return sorted(set(value))


class NotificationRecordBatchDelete(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=500)

    @field_validator("ids")
    @classmethod
    def normalize_ids(cls, value: list[int]) -> list[int]:
        if any(item <= 0 for item in value):
            raise ValueError("通知记录 ID 必须为正整数")
        return sorted(set(value))


class DeployRequest(BaseModel):
    namespace: str = Field(pattern=r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$")
    name: str = Field(pattern=r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$")
    image: str


class RollbackRequest(BaseModel):
    revision: int | None = Field(default=None, ge=1)


class ScaleRequest(BaseModel):
    replicas: int = Field(ge=0, le=500)


class BuildRequest(BaseModel):
    name: str = Field(pattern=r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$")
    git_url: str
    git_ref: str = "main"
    context_dir: str = "."
    dockerfile: str = "Dockerfile"
    destination: str
    namespace: str = "devops-platform"
    git_secret_name: str = ""
    git_auth_secret_name: str = ""
    registry_secret_name: str = ""
    build_args: dict[str, str] = Field(default_factory=dict)
    timeout_seconds: int = Field(default=1800, ge=60, le=86400)


class BuildConfigurationCreate(BaseModel):
    name: str = Field(min_length=1, max_length=180, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.-]*$")
    description: str = Field(default="", max_length=500)
    enabled: bool = True
    git_url: str = Field(min_length=1, max_length=600)
    git_ref: str = Field(default="main", max_length=180)
    context_dir: str = Field(default=".", max_length=300)
    dockerfile: str = Field(default="Dockerfile", max_length=300)
    destination: str = Field(min_length=1, max_length=600)
    namespace: str = "devops-platform"
    git_secret_name: str = ""
    registry_secret_name: str = ""
    git_username: str = Field(default="", max_length=255)
    git_password: str = Field(default="", max_length=4096)
    registry_username: str = Field(default="", max_length=255)
    registry_password: str = Field(default="", max_length=4096)
    clear_git_credentials: bool = False
    clear_registry_credentials: bool = False
    build_args: dict[str, str] = Field(default_factory=dict)
    timeout_seconds: int = Field(default=1800, ge=60, le=86400)
    allow_concurrent: bool = False
    retention_count: int = Field(default=20, ge=1, le=500)

    @model_validator(mode="after")
    def validate_build_credentials(self):
        if bool(self.git_username) != bool(self.git_password):
            raise ValueError("Git 用户名和密码必须同时填写")
        if bool(self.registry_username) != bool(self.registry_password):
            raise ValueError("镜像仓库用户名和密码必须同时填写")
        if self.git_username and not self.git_url.lower().startswith(("https://", "http://")):
            raise ValueError("使用 Git 用户名和密码时，请填写仓库的 HTTP(S) 克隆地址")
        if self.git_username and self.git_secret_name:
            raise ValueError("Git 账号密码和 SSH Secret 只能选择一种认证方式")
        if self.git_username and self.clear_git_credentials:
            raise ValueError("不能同时更新并清除 Git 凭证")
        if self.registry_username and self.clear_registry_credentials:
            raise ValueError("不能同时更新并清除镜像仓库凭证")
        return self


class BuildRunRequest(BaseModel):
    git_ref: str | None = Field(default=None, max_length=180)
    destination: str | None = Field(default=None, max_length=600)
    build_args: dict[str, str] | None = None


class AlertRuleCreate(BaseModel):
    name: str
    metric: Literal[
        "pod_cpu_percent", "pod_memory_percent", "node_cpu_percent",
        "node_memory_percent", "node_disk_percent", "pod_restarts", "pod_not_running"
    ]
    operator: Literal[">", ">=", "<", "<=", "=="] = ">"
    threshold: float
    duration_seconds: int = Field(default=60, ge=0, le=86400)
    repeat_interval_minutes: int = Field(default=30, ge=0, le=10080)
    severity: Literal["info", "warning", "critical"] = "warning"
    namespace: str = "*"
    target_pattern: str = "*"
    template_id: int | None = Field(default=None, ge=1)
    channel_ids: list[int] = Field(default_factory=list)
    enabled: bool = True

    @model_validator(mode="after")
    def normalize_metric_rule(self):
        self.channel_ids = sorted(set(self.channel_ids))
        if any(channel_id <= 0 for channel_id in self.channel_ids):
            raise ValueError("通知渠道 ID 必须为正整数")
        if self.metric == "pod_not_running":
            self.operator = "=="
            self.threshold = 1
        elif self.metric in {"node_cpu_percent", "node_memory_percent", "node_disk_percent"}:
            self.namespace = "_cluster"
        elif self.metric == "pod_restarts":
            self.operator = ">="
            if self.threshold < 1 or not self.threshold.is_integer():
                raise ValueError("Pod 重启次数阈值必须是大于等于 1 的整数")
        return self


class ChannelCreate(BaseModel):
    name: str
    channel_type: Literal["email", "feishu", "dingtalk", "wecom", "phone_webhook"]
    config: dict[str, Any]
    enabled: bool = True

    @model_validator(mode="after")
    def validate_channel_config(self):
        required = {
            "email": {"host", "from", "to"},
            "feishu": {"url"},
            "dingtalk": {"url"},
            "wecom": {"url"},
            "phone_webhook": {"url", "phones"},
        }[self.channel_type]
        missing = sorted(key for key in required if not self.config.get(key))
        if missing:
            raise ValueError(f"渠道配置缺少字段: {', '.join(missing)}")
        template_id = self.config.get("notification_template_id")
        if not isinstance(template_id, int) or template_id <= 0:
            raise ValueError("通知渠道必须关联有效的通知模板")
        if "请填写" in str(self.config):
            raise ValueError("请先替换渠道配置中的占位内容")
        return self


class UserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=80, pattern=r"^[A-Za-z0-9_.-]+$")
    display_name: str = Field(default="", max_length=120)
    password: str = Field(min_length=8, max_length=128)
    email: EmailStr
    phone: str = Field(default="", max_length=40, pattern=r"^[0-9+() -]*$")
    role: Literal["tester", "developer", "operator", "admin"] = "operator"

    @field_validator("password")
    @classmethod
    def validate_password_strength(cls, value: str) -> str:
        return validate_strong_password(value)


class UserUpdate(BaseModel):
    display_name: str = Field(default="", max_length=120)
    email: EmailStr
    phone: str = Field(default="", max_length=40, pattern=r"^[0-9+() -]*$")
    password: str | None = Field(default=None, min_length=8, max_length=128)
    role: Literal["tester", "developer", "operator", "admin"] = "operator"
    is_active: bool = True

    @field_validator("password")
    @classmethod
    def validate_password_strength(cls, value: str | None) -> str | None:
        return validate_strong_password(value) if value is not None else None


class MenuPermissionUpdate(BaseModel):
    menu_permissions: list[str] = Field(default_factory=list, max_length=100)


class AlertRuleTemplateCreate(BaseModel):
    name: str = Field(min_length=1, max_length=180)
    description: str = Field(default="", max_length=500)
    metric: Literal[
        "pod_cpu_percent", "pod_memory_percent", "node_cpu_percent",
        "node_memory_percent", "node_disk_percent", "pod_restarts", "pod_not_running"
    ]
    operator: Literal[">", ">=", "<", "<=", "=="] = ">"
    threshold: float = 80
    duration_seconds: int = Field(default=60, ge=0, le=86400)
    repeat_interval_minutes: int = Field(default=30, ge=0, le=10080)
    severity: Literal["info", "warning", "critical"] = "warning"
    namespace: str = "*"
    target_pattern: str = "*"

    @model_validator(mode="after")
    def normalize_metric_template(self):
        if self.metric == "pod_not_running":
            self.operator = "=="
            self.threshold = 1
        elif self.metric in {"node_cpu_percent", "node_memory_percent", "node_disk_percent"}:
            self.namespace = "_cluster"
        elif self.metric == "pod_restarts":
            self.operator = ">="
            if self.threshold < 1 or not self.threshold.is_integer():
                raise ValueError("Pod 重启次数阈值必须是大于等于 1 的整数")
        return self


class AlertSubscriptionCreate(BaseModel):
    name: str = Field(min_length=1, max_length=180)
    user_id: int = Field(ge=1)
    rule_id: int | None = Field(default=None, ge=1)
    channel_ids: list[int] = Field(default_factory=list)
    enabled: bool = True

    @field_validator("channel_ids")
    @classmethod
    def normalize_channel_ids(cls, value: list[int]):
        if any(channel_id <= 0 for channel_id in value):
            raise ValueError("通知渠道 ID 必须为正整数")
        return sorted(set(value))


class NotificationTemplateCreate(BaseModel):
    name: str = Field(min_length=1, max_length=180)
    channel_type: Literal["email", "feishu", "dingtalk", "wecom", "phone_webhook"]
    title_template: str = Field(default="{title}", max_length=500)
    message_template: str = Field(min_length=1)


class OnCallShiftCreate(BaseModel):
    user_id: int = Field(ge=1)
    starts_at: datetime
    ends_at: datetime
    note: str = Field(default="", max_length=500)

    @model_validator(mode="after")
    def validate_shift(self):
        if self.ends_at <= self.starts_at:
            raise ValueError("值班结束时间必须晚于开始时间")
        return self


class IncidentUpdate(BaseModel):
    status: Literal["firing", "acknowledged", "resolved"]
    assignee: str = Field(default="", max_length=80)
    note: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def validate_incident_update(self):
        self.assignee = self.assignee.strip()
        self.note = self.note.strip()
        if self.status in {"acknowledged", "resolved"} and not self.assignee:
            raise ValueError("处理中或已解决的故障必须填写负责人")
        if not self.note:
            raise ValueError("请填写处理说明")
        return self


class ManifestApplyRequest(BaseModel):
    manifest: dict[str, Any]

    @field_validator("manifest")
    @classmethod
    def validate_manifest(cls, value: dict[str, Any]):
        if not value.get("apiVersion") or not value.get("kind") or not value.get("metadata", {}).get("name"):
            raise ValueError("manifest 必须包含 apiVersion、kind 和 metadata.name")
        return value


class ResourceSaveRequest(BaseModel):
    content: str = Field(min_length=1)


class ResourceRollbackRequest(BaseModel):
    version: int | None = Field(default=None, ge=1)


class DiskMonitorConfig(BaseModel):
    paths: list[str] = Field(min_length=1, max_length=20)

    @field_validator("paths")
    @classmethod
    def validate_paths(cls, value: list[str]):
        paths = []
        for path in value:
            path = path.strip().rstrip("/") or "/"
            if not path.startswith("/") or ".." in path.split("/"):
                raise ValueError("监控目录必须是合法的绝对路径")
            if path not in paths:
                paths.append(path)
        return paths
