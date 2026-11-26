from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import re
import smtplib
from email.message import EmailMessage
from typing import Any

import httpx
from cryptography.fernet import Fernet
from sqlalchemy.orm import Session

from .config import get_settings
from .models import NotificationTemplate


settings = get_settings()


class SecretBox:
    def __init__(self) -> None:
        if settings.encryption_key:
            key = settings.encryption_key.encode()
        else:
            key = base64.urlsafe_b64encode(hashlib.sha256(settings.secret_key.encode()).digest())
        self.fernet = Fernet(key)

    def encrypt(self, value: dict[str, Any]) -> str:
        return self.fernet.encrypt(json.dumps(value, ensure_ascii=False).encode()).decode()

    def decrypt(self, value: str) -> dict[str, Any]:
        return json.loads(self.fernet.decrypt(value.encode()).decode())


secret_box = SecretBox()


def split_recipients(value: Any) -> list[str]:
    """Normalize recipient strings and lists from the channel JSON editor."""
    raw_items = value if isinstance(value, (list, tuple, set)) else [value]
    recipients: list[str] = []
    for raw in raw_items:
        for item in re.split(r"[,，;；\r\n]+", str(raw or "")):
            item = item.strip()
            if item and item not in recipients:
                recipients.append(item)
    return recipients


def render_channel_message(
    config: dict[str, Any], title: str, message: str,
    variables: dict[str, Any] | None = None,
) -> tuple[str, str]:
    values = {"title": title, "message": message, **(variables or {})}

    def render(template: str) -> str:
        result = template
        for key, value in values.items():
            result = result.replace("{" + key + "}", str(value))
        return result

    return (
        render(str(config.get("title_template", "{title}"))),
        render(str(config.get("message_template", "{message}"))),
    )


def apply_notification_template(db: Session, config: dict[str, Any]) -> dict[str, Any]:
    template_id = config.get("notification_template_id")
    if not template_id:
        return config
    try:
        template = db.get(NotificationTemplate, int(template_id))
    except (TypeError, ValueError):
        return config
    if not template:
        return config
    return {
        **config,
        "template_name": template.name,
        "title_template": template.title_template,
        "message_template": template.message_template,
    }


async def send_email(config: dict[str, Any], title: str, message: str) -> None:
    def _send() -> None:
        mail = EmailMessage()
        mail["Subject"] = title
        mail["From"] = config["from"]
        recipients = split_recipients(config.get("to", []))
        mail["To"] = ", ".join(recipients)
        mail.set_content(message)
        host = config["host"]
        port = int(config.get("port", 465 if config.get("ssl", True) else 25))
        smtp_cls = smtplib.SMTP_SSL if config.get("ssl", True) else smtplib.SMTP
        with smtp_cls(host, port, timeout=15) as smtp:
            if config.get("starttls"):
                smtp.starttls()
            if config.get("username"):
                smtp.login(config["username"], config.get("password", ""))
            smtp.send_message(mail)
    await asyncio.to_thread(_send)


async def send_webhook(channel_type: str, config: dict[str, Any], title: str, message: str) -> None:
    url = config["url"]
    text = f"{title}\n{message}"
    if channel_type == "feishu":
        payload = {"msg_type": "text", "content": {"text": text}}
    elif channel_type == "dingtalk":
        payload = {"msgtype": "text", "text": {"content": text}}
    elif channel_type == "wecom":
        payload = {"msgtype": "text", "text": {"content": text}}
    else:
        payload = {
            "title": title,
            "message": message,
            "phones": split_recipients(config.get("phones", [])),
            "template_code": config.get("template_code", ""),
        }
        payload.update(config.get("extra_payload", {}))
    headers = {"Content-Type": "application/json", **config.get("headers", {})}
    async with httpx.AsyncClient(timeout=15, verify=config.get("verify_tls", True)) as client:
        response = await client.post(url, json=payload, headers=headers)
        response.raise_for_status()


async def dispatch(
    channel_type: str, config: dict[str, Any], title: str, message: str,
    variables: dict[str, Any] | None = None,
) -> None:
    title, message = render_channel_message(config, title, message, variables)
    if channel_type == "email":
        await send_email(config, title, message)
    elif channel_type in {"feishu", "dingtalk", "wecom", "phone_webhook"}:
        await send_webhook(channel_type, config, title, message)
    else:
        raise ValueError(f"不支持的通知类型: {channel_type}")

