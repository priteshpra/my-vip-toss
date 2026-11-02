"""Administrative audit logging service. Records who, what, when, and IP address."""

from typing import Optional, List, Dict, Any
from datetime import datetime
from fastapi import Request
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import AuditLog
from app.db import utc_now


def get_client_ip(request: Request) -> str:
    """Extract client IP address from proxy headers or connection host."""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        # Take the first client IP in chain
        return forwarded.split(",")[0].strip()

    real_ip = request.headers.get("X-Real-IP")
    if real_ip:
        return real_ip.strip()

    if request.client and request.client.host:
        return request.client.host

    return "127.0.0.1"


async def log_audit_event(
    session: AsyncSession,
    action: str,
    entity: str,
    entity_id: Optional[str] = None,
    user: Optional[str] = "admin",
    ip_address: Optional[str] = None,
    details: Optional[str] = None
) -> AuditLog:
    """
    Append an immutable audit entry recording administrative and system actions.
    Records who performed it, what action was taken, timestamp (UTC), and client IP.
    """
    audit = AuditLog(
        action=action,
        entity=entity,
        entity_id=str(entity_id) if entity_id is not None else None,
        user=user or "anonymous",
        ip_address=ip_address or "127.0.0.1",
        timestamp_utc=utc_now(),
        details=details
    )
    session.add(audit)
    await session.commit()
    await session.refresh(audit)
    return audit


record_audit_log = log_audit_event


async def get_audit_logs(
    session: AsyncSession,
    limit: int = 100,
    offset: int = 0,
    action: Optional[str] = None,
    user: Optional[str] = None
) -> List[AuditLog]:
    """Retrieve audit log entries in reverse chronological order."""
    query = select(AuditLog).order_by(desc(AuditLog.timestamp_utc))

    if action:
        query = query.where(AuditLog.action == action)
    if user:
        query = query.where(AuditLog.user == user)

    query = query.limit(limit).offset(offset)
    result = await session.execute(query)
    return list(result.scalars().all())

