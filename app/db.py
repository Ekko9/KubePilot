from contextlib import contextmanager
import re

import pymysql
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import URL
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import get_settings


settings = get_settings()


class Base(DeclarativeBase):
    pass


def ensure_mysql_database() -> None:
    if settings.database_url or not settings.db_password:
        return
    if not re.fullmatch(r"[A-Za-z0-9_]+", settings.db_name):
        raise ValueError("DEVOPS_DB_NAME 只能包含字母、数字和下划线")
    connection = pymysql.connect(
        host=settings.db_host,
        port=settings.db_port,
        user=settings.db_user,
        password=settings.db_password,
        charset="utf8mb4",
        connect_timeout=10,
        autocommit=True,
    )
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                f"CREATE DATABASE IF NOT EXISTS {settings.db_name} "
                "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )
    finally:
        connection.close()


def ensure_schema_upgrades() -> None:
    inspector = inspect(engine)
    upgrades = {
        "users": {
            "display_name": "ALTER TABLE users ADD COLUMN display_name VARCHAR(120) NOT NULL DEFAULT ''",
            "must_change_password": (
                "ALTER TABLE users ADD COLUMN must_change_password BOOLEAN NOT NULL DEFAULT FALSE"
            ),
            "phone": "ALTER TABLE users ADD COLUMN phone VARCHAR(40) NOT NULL DEFAULT ''",
            "role": "ALTER TABLE users ADD COLUMN role VARCHAR(32) NOT NULL DEFAULT 'operator'",
            "menu_permissions": "ALTER TABLE users ADD COLUMN menu_permissions JSON NULL",
        },
        "image_builds": {
            "config_id": "ALTER TABLE image_builds ADD COLUMN config_id INTEGER NULL",
            "build_args": "ALTER TABLE image_builds ADD COLUMN build_args JSON NULL",
            "build_steps": "ALTER TABLE image_builds ADD COLUMN build_steps JSON NULL",
        },
        "build_configurations": {
            "git_auth_secret_name": "ALTER TABLE build_configurations ADD COLUMN git_auth_secret_name VARCHAR(180) NOT NULL DEFAULT ''",
            "build_steps": "ALTER TABLE build_configurations ADD COLUMN build_steps JSON NULL",
        },
        "alert_rules": {
            "template_id": "ALTER TABLE alert_rules ADD COLUMN template_id INTEGER NULL",
            "repeat_interval_minutes": (
                "ALTER TABLE alert_rules ADD COLUMN repeat_interval_minutes INTEGER NOT NULL DEFAULT 30"
            ),
        },
        "alert_events": {
            "assignee": "ALTER TABLE alert_events ADD COLUMN assignee VARCHAR(80) NOT NULL DEFAULT ''",
            "handling_note": "ALTER TABLE alert_events ADD COLUMN handling_note TEXT NULL",
            "acknowledged_at": "ALTER TABLE alert_events ADD COLUMN acknowledged_at DATETIME NULL",
            "resolved_by": "ALTER TABLE alert_events ADD COLUMN resolved_by VARCHAR(80) NOT NULL DEFAULT ''",
            "updated_at": "ALTER TABLE alert_events ADD COLUMN updated_at DATETIME NULL",
        },
        "alert_rule_templates": {
            "repeat_interval_minutes": (
                "ALTER TABLE alert_rule_templates ADD COLUMN repeat_interval_minutes INTEGER NOT NULL DEFAULT 30"
            ),
        },
        "audit_logs": {
            "display_name": "ALTER TABLE audit_logs ADD COLUMN display_name VARCHAR(120) NOT NULL DEFAULT ''",
        },
    }
    with engine.begin() as connection:
        for table_name, table_upgrades in upgrades.items():
            if not inspector.has_table(table_name):
                continue
            columns = {column["name"] for column in inspect(connection).get_columns(table_name)}
            for column_name, statement in table_upgrades.items():
                if column_name not in columns:
                    connection.execute(text(statement))
        if inspector.has_table("users"):
            connection.execute(text(
                "UPDATE users SET role = CASE WHEN is_admin = TRUE THEN 'admin' "
                "WHEN role IS NULL OR role = '' THEN 'operator' ELSE role END"
            ))
            connection.execute(text(
                "UPDATE users SET display_name = username WHERE display_name IS NULL OR display_name = ''"
            ))
        if inspector.has_table("audit_logs") and inspector.has_table("users"):
            connection.execute(text(
                "UPDATE audit_logs SET display_name = COALESCE(NULLIF(("
                "SELECT users.display_name FROM users WHERE users.username = audit_logs.username"
                "), ''), username) WHERE display_name IS NULL OR display_name = ''"
            ))


def database_url() -> str | URL:
    if settings.database_url:
        return settings.database_url
    return URL.create(
        "mysql+pymysql",
        username=settings.db_user,
        password=settings.db_password,
        host=settings.db_host,
        port=settings.db_port,
        database=settings.db_name,
        query={"charset": "utf8mb4"},
    )


db_url = database_url()
connect_args = {"check_same_thread": False} if str(db_url).startswith("sqlite") else {}
engine = create_engine(db_url, pool_pre_ping=True, pool_recycle=1800, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def db_session():
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

