"""Lightweight admin audit logging.

Usage::

    from vpn_bot.admin_audit import audit_log

    await audit_log(
        admin_id=update.effective_user.id,
        action="ban_user",
        target_type="user",
        target_id=str(target_telegram_id),
        detail={"reason": "spam"},
    )

All arguments except ``admin_id`` and ``action`` are optional.
Failures are logged but never propagate — audit must not break normal flow.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from vpn_bot.database import AsyncSessionLocal
from vpn_bot.models import AdminAuditLog

logger = logging.getLogger("vpn_bot.admin_audit")


async def audit_log(
    admin_id: int,
    action: str,
    *,
    target_type: str | None = None,
    target_id: str | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    """Write one audit record.  Swallows all exceptions to protect the caller."""
    try:
        detail_str = json.dumps(detail, ensure_ascii=False) if detail else None
        async with AsyncSessionLocal() as session:
            entry = AdminAuditLog(
                admin_telegram_id=admin_id,
                action=action,
                target_type=target_type,
                target_id=str(target_id) if target_id is not None else None,
                detail=detail_str,
            )
            session.add(entry)
            await session.commit()
    except Exception as exc:
        logger.error("audit_log failed (action=%s admin=%s): %s", action, admin_id, exc)
