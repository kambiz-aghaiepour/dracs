"""User management and authentication for DRACS."""

import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from werkzeug.security import check_password_hash, generate_password_hash

from dracs.db import Site, User, UserSiteRole, get_session
from dracs.exceptions import ValidationError

_USERNAME_RE = re.compile(r"^[a-zA-Z0-9_-]{3,32}$")
_VALID_ROLES = {"admin", "user"}


def _superadmin_username() -> str:
    return os.environ.get("WEBADMIN_USER", "admin")


def validate_username(username: str) -> bool:
    return bool(_USERNAME_RE.match(username))


def create_user(
    username: str,
    password: str,
    role: str,
    created_by: str | None = None,
) -> User:
    if not validate_username(username):
        raise ValidationError(
            f"Invalid username: '{username}'. "
            "Must be 3-32 characters, alphanumeric, hyphens, or underscores."
        )
    if role not in _VALID_ROLES:
        raise ValidationError(f"Invalid role: '{role}'. Must be 'admin' or 'user'.")
    if not password:
        raise ValidationError("Password cannot be empty.")
    if username == _superadmin_username():
        raise ValidationError(
            f"Cannot create user '{username}': reserved for superadmin."
        )

    with get_session() as session:
        existing = session.query(User).filter(User.username == username).first()
        if existing:
            raise ValidationError(f"User '{username}' already exists.")

        user = User(
            username=username,
            password_hash=generate_password_hash(password),
            role=role,
            created_at=datetime.now(timezone.utc).isoformat(),
            created_by=created_by,
        )
        session.add(user)
        session.flush()

        default_site = (
            session.query(Site).filter(Site.is_primary == True).first()
        )  # noqa: E712
        if default_site:
            mapping = UserSiteRole(user_id=user.id, site_id=default_site.id, role=role)
            session.add(mapping)

        session.commit()
        session.refresh(user)
        return user


def authenticate(username: str, password: str) -> tuple[str, str] | None:
    with get_session() as session:
        user = session.query(User).filter(User.username == username).first()
        if user and check_password_hash(user.password_hash, password):
            return (user.username, user.role)

    superadmin_user = _superadmin_username()
    superadmin_pass = os.environ.get("WEBADMIN_PASSWORD", "admin")
    if username == superadmin_user and password == superadmin_pass:
        return (superadmin_user, "admin")

    return None


def delete_user(username: str) -> bool:
    if username == _superadmin_username():
        raise ValidationError("Cannot delete the superadmin account.")

    with get_session() as session:
        user = session.query(User).filter(User.username == username).first()
        if not user:
            return False
        session.delete(user)
        session.commit()
        return True


def list_users() -> list[dict]:
    with get_session() as session:
        users = session.query(User).order_by(User.username).all()
        return [
            {
                "id": u.id,
                "username": u.username,
                "role": u.role,
                "created_at": u.created_at,
                "created_by": u.created_by,
            }
            for u in users
        ]


def update_user_password(username: str, new_password: str) -> bool:
    if username == _superadmin_username():
        raise ValidationError(
            "Cannot modify superadmin via API. Edit the config file directly."
        )
    if not new_password:
        raise ValidationError("Password cannot be empty.")

    with get_session() as session:
        user = session.query(User).filter(User.username == username).first()
        if not user:
            return False
        user.password_hash = generate_password_hash(new_password)
        session.commit()
        return True


def update_user_role(username: str, new_role: str) -> bool:
    if username == _superadmin_username():
        raise ValidationError(
            "Cannot modify superadmin via API. Edit the config file directly."
        )
    if new_role not in _VALID_ROLES:
        raise ValidationError(f"Invalid role: '{new_role}'. Must be 'admin' or 'user'.")

    with get_session() as session:
        user = session.query(User).filter(User.username == username).first()
        if not user:
            return False
        user.role = new_role

        default_site = (
            session.query(Site).filter(Site.is_primary == True).first()
        )  # noqa: E712
        if default_site:
            mapping = (
                session.query(UserSiteRole)
                .filter_by(user_id=user.id, site_id=default_site.id)
                .first()
            )
            if mapping:
                mapping.role = new_role

        session.commit()
        return True


def get_user(username: str) -> User | None:
    with get_session() as session:
        return session.query(User).filter(User.username == username).first()


def set_user_site_role(username: str, site_id: int, role: str) -> None:
    if role not in _VALID_ROLES:
        raise ValidationError(f"Invalid role: '{role}'. Must be 'admin' or 'user'.")

    with get_session() as session:
        user = session.query(User).filter(User.username == username).first()
        if user is None:
            raise ValidationError(f"User '{username}' not found.")
        site = session.get(Site, site_id)
        if site is None:
            raise ValidationError(f"Site ID {site_id} not found.")

        existing = (
            session.query(UserSiteRole)
            .filter_by(user_id=user.id, site_id=site_id)
            .first()
        )
        if existing:
            existing.role = role
        else:
            mapping = UserSiteRole(user_id=user.id, site_id=site_id, role=role)
            session.add(mapping)
        session.commit()


def remove_user_site_role(username: str, site_id: int) -> bool:
    with get_session() as session:
        user = session.query(User).filter(User.username == username).first()
        if user is None:
            return False

        deleted = (
            session.query(UserSiteRole)
            .filter_by(user_id=user.id, site_id=site_id)
            .delete()
        )
        session.commit()
        return deleted > 0


def get_user_site_roles(username: str) -> list[dict]:
    with get_session() as session:
        user = session.query(User).filter(User.username == username).first()
        if user is None:
            return []

        results = (
            session.query(UserSiteRole, Site)
            .join(Site, UserSiteRole.site_id == Site.id)
            .filter(UserSiteRole.user_id == user.id)
            .all()
        )
        return [
            {
                "site_id": role.site_id,
                "site_name": site.name,
                "role": role.role,
            }
            for role, site in results
        ]


def get_user_role_for_site(username: str, site_id: int) -> str | None:
    with get_session() as session:
        user = session.query(User).filter(User.username == username).first()
        if user is None:
            return None

        mapping = (
            session.query(UserSiteRole)
            .filter_by(user_id=user.id, site_id=site_id)
            .first()
        )
        if mapping is None:
            return None
        return mapping.role


def update_superadmin_password(new_password: str) -> None:
    if not new_password:
        raise ValidationError("Password cannot be empty.")

    from dracs.config import SYSTEM_CONFIG

    config_path = Path(os.environ.get("DRACS_CONF", str(SYSTEM_CONFIG)))
    if not config_path.exists():
        raise ValidationError(f"Config file not found: {config_path}")

    lines = config_path.read_text().splitlines(keepends=True)
    found = False
    for i, line in enumerate(lines):
        if line.startswith("WEBADMIN_PASSWORD="):
            lines[i] = f"WEBADMIN_PASSWORD={new_password}\n"
            found = True
            break
    if not found:
        lines.append(f"WEBADMIN_PASSWORD={new_password}\n")

    fd, tmp_path = tempfile.mkstemp(dir=str(config_path.parent))
    try:
        os.write(fd, "".join(lines).encode())
        os.close(fd)
        os.replace(tmp_path, str(config_path))
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise

    os.environ["WEBADMIN_PASSWORD"] = new_password
