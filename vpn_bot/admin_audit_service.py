"""Read-only helpers for admin audit log UI."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select

from vpn_bot.database import AsyncSessionLocal
from vpn_bot.models import AdminAuditLog
from vpn_bot.utils import format_datetime


@dataclass
class AuditLogRow:
    id: int
    admin_telegram_id: int
    action: str
    target_type: str | None
    target_id: str | None
    detail: dict | None
    created_at: datetime


async def fetch_recent_audit_logs(limit: int = 50) -> list[AuditLogRow]:
    async with AsyncSessionLocal() as session:
        res = await session.execute(
            select(AdminAuditLog).order_by(AdminAuditLog.created_at.desc()).limit(limit)
        )
        rows = res.scalars().all()

    out: list[AuditLogRow] = []
    for row in rows:
        detail = None
        if row.detail:
            try:
                detail = json.loads(row.detail)
            except json.JSONDecodeError:
                detail = {"raw": row.detail}
        out.append(
            AuditLogRow(
                id=row.id,
                admin_telegram_id=row.admin_telegram_id,
                action=row.action,
                target_type=row.target_type,
                target_id=row.target_id,
                detail=detail,
                created_at=row.created_at,
            )
        )
    return out


async def format_audit_log_text(limit: int = 50) -> str:
    from vpn_bot.utils import LanguageManager

    rows = await fetch_recent_audit_logs(limit=limit)
    if not rows:
        return LanguageManager.get("admin.audit.empty")

    lines = [LanguageManager.get("admin.audit.title"), ""]
    for row in rows:
        when = await format_datetime(row.created_at, include_time=True)
        target = ""
        if row.target_type and row.target_id:
            target = LanguageManager.get(
                "admin.audit.target",
                target_type=row.target_type,
                target_id=row.target_id,
            )
        lines.append(
            LanguageManager.get(
                "admin.audit.line",
                when=when,
                admin_id=row.admin_telegram_id,
                action=row.action,
                target=target,
            )
        )
    return "\n".join(lines)
