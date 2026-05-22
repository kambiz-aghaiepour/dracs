"""API token management for DRACS remote client authentication."""

import os
import secrets
from datetime import datetime, timezone

from dracs.db import ApiToken, get_session


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_iso(ts: str) -> datetime:
    return datetime.fromisoformat(ts)


def _default_expiry() -> int:
    return int(os.environ.get("DRACS_TOKEN_EXPIRY", "36000"))


def generate_token(
    username: str, role: str, expires_seconds: int | None = None
) -> dict:
    if expires_seconds is None:
        expires_seconds = _default_expiry()
    token_str = secrets.token_hex(32)
    now = _now_iso()
    with get_session() as session:
        token = ApiToken(
            token=token_str,
            username=username,
            role=role,
            created_at=now,
            last_used=now,
            expires_seconds=expires_seconds,
        )
        session.add(token)
        session.commit()
    return {"token": token_str, "role": role, "expires_in": expires_seconds}


def validate_token(token_str: str) -> tuple[str, str] | None:
    with get_session() as session:
        token = session.query(ApiToken).filter(ApiToken.token == token_str).first()
        if not token:
            return None
        last_used = _parse_iso(token.last_used)
        now = datetime.now(timezone.utc)
        elapsed = (now - last_used).total_seconds()
        if elapsed > token.expires_seconds:
            session.delete(token)
            session.commit()
            return None
        return (token.username, token.role)


def refresh_token(token_str: str) -> bool:
    with get_session() as session:
        token = session.query(ApiToken).filter(ApiToken.token == token_str).first()
        if not token:
            return False
        last_used = _parse_iso(token.last_used)
        now = datetime.now(timezone.utc)
        elapsed = (now - last_used).total_seconds()
        if elapsed > token.expires_seconds:
            session.delete(token)
            session.commit()
            return False
        token.last_used = _now_iso()
        session.commit()
        return True


def invalidate_token(token_str: str) -> bool:
    with get_session() as session:
        token = session.query(ApiToken).filter(ApiToken.token == token_str).first()
        if not token:
            return False
        session.delete(token)
        session.commit()
        return True


def invalidate_all_tokens(username: str) -> int:
    with get_session() as session:
        count = session.query(ApiToken).filter(ApiToken.username == username).delete()
        session.commit()
        return count


def cleanup_expired_tokens() -> int:
    with get_session() as session:
        tokens = session.query(ApiToken).all()
        now = datetime.now(timezone.utc)
        deleted = 0
        for token in tokens:
            last_used = _parse_iso(token.last_used)
            elapsed = (now - last_used).total_seconds()
            if elapsed > token.expires_seconds:
                session.delete(token)
                deleted += 1
        session.commit()
        return deleted
