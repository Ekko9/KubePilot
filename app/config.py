from functools import lru_cache
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="DEVOPS_", extra="ignore")

    app_name: str = "KubePilot DevOps"
    environment: str = "production"
    secret_key: str = Field(default="change-me-in-production", min_length=16)
    encryption_key: str = ""
    access_token_minutes: int = 720

    database_url: str = ""
    db_host: str = "db.example.internal"
    db_port: int = 3306
    db_user: str = "root"
    db_password: str = ""
    db_name: str = "kubepilot_devops"

    admin_username: str = "admin"
    admin_password: str = "ChangeMe123!"
    admin_email: str = "admin@example.com"

    kubeconfig_path: str = ""
    registry_insecure: bool = True
    build_namespace: str = "devops-platform"
    service_account_name: str = "devops-platform"
    kaniko_image: str = "gcr.io/kaniko-project/executor:v1.24.0-debug"
    git_clone_image: str = "alpine/git:2.49.1"
    poll_interval_seconds: int = 30
    display_timezone: str = "Asia/Shanghai"
    disk_config_map: str = "devops-platform-monitor"
    cors_origins: str = "*"

    @property
    def cors_origin_list(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()

