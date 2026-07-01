from contextlib import contextmanager
from datetime import datetime
from typing import List, Optional

from sqlalchemy import (
    Boolean,
    UniqueConstraint,
    create_engine,
    ForeignKey,
    String,
    Integer,
    Text,
)
from sqlalchemy.pool import NullPool
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    sessionmaker,
)

_engine = None
_SessionFactory = None


class Base(DeclarativeBase):
    pass


class Site(Base):
    __tablename__ = "sites"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[str] = mapped_column(String, nullable=False)
    sort_order: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True, default=None
    )
    allowed_domains: Mapped[Optional[str]] = mapped_column(
        String, nullable=True, default=None
    )


class System(Base):
    __tablename__ = "systems"

    svc_tag: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str | None] = mapped_column(String)
    model: Mapped[str | None] = mapped_column(String)
    idrac_version: Mapped[str | None] = mapped_column(String)
    bios_version: Mapped[str | None] = mapped_column(String)
    exp_date: Mapped[str | None] = mapped_column(String)
    exp_epoch: Mapped[int | None] = mapped_column(Integer)
    site_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("sites.id"), nullable=True
    )

    def to_tuple(self):
        return (
            self.svc_tag,
            self.name,
            self.model,
            self.idrac_version,
            self.bios_version,
            self.exp_date,
            self.exp_epoch,
        )


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String, nullable=False)
    role: Mapped[str | None] = mapped_column(String, nullable=True, default=None)
    created_at: Mapped[str] = mapped_column(String, nullable=False)
    created_by: Mapped[str | None] = mapped_column(String, nullable=True)


class UserSiteRole(Base):
    __tablename__ = "user_site_roles"
    __table_args__ = (UniqueConstraint("user_id", "site_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=False
    )
    site_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("sites.id"), nullable=False
    )
    role: Mapped[str] = mapped_column(String, nullable=False)


class ApiToken(Base):
    __tablename__ = "api_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    token: Mapped[str] = mapped_column(String, unique=True, nullable=False, index=True)
    username: Mapped[str] = mapped_column(String, nullable=False)
    role: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[str] = mapped_column(String, nullable=False)
    last_used: Mapped[str] = mapped_column(String, nullable=False)
    expires_seconds: Mapped[int] = mapped_column(Integer, nullable=False)


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    parent_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("jobs.id"), nullable=True
    )
    job_type: Mapped[str] = mapped_column(String, nullable=False)
    target: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="pending")
    created_at: Mapped[str] = mapped_column(String, nullable=False)
    started_at: Mapped[str | None] = mapped_column(String, nullable=True)
    completed_at: Mapped[str | None] = mapped_column(String, nullable=True)
    result: Mapped[str | None] = mapped_column(String, nullable=True)
    error: Mapped[str | None] = mapped_column(String, nullable=True)
    worker_id: Mapped[str | None] = mapped_column(String, nullable=True)
    metadata_json: Mapped[str | None] = mapped_column(String, nullable=True)
    site_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("sites.id"), nullable=True
    )


class SiteConfigCollection(Base):
    __tablename__ = "site_config_collection"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    site_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("sites.id"), nullable=False, unique=True
    )
    ps_rapid_on_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    ps_rapid_on_hours: Mapped[int] = mapped_column(Integer, nullable=False, default=24)
    dns_from_dhcp_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    dns_from_dhcp_hours: Mapped[int] = mapped_column(
        Integer, nullable=False, default=24
    )
    ipmi_lan_enable_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    ipmi_lan_enable_hours: Mapped[int] = mapped_column(
        Integer, nullable=False, default=24
    )
    host_header_check_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    host_header_check_hours: Mapped[int] = mapped_column(
        Integer, nullable=False, default=24
    )
    sys_profile_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    sys_profile_hours: Mapped[int] = mapped_column(Integer, nullable=False, default=24)
    ssl_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    ssl_hours: Mapped[int] = mapped_column(Integer, nullable=False, default=24)
    idrac_hostname_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    idrac_hostname_hours: Mapped[int] = mapped_column(
        Integer, nullable=False, default=24
    )


class HostConfig(Base):
    __tablename__ = "host_config"
    __table_args__ = (UniqueConstraint("hostname", "site_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    hostname: Mapped[str] = mapped_column(String, nullable=False)
    site_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("sites.id"), nullable=False
    )
    ps_rapid_on: Mapped[str | None] = mapped_column(String, nullable=True)
    idrac_hostname: Mapped[int | None] = mapped_column(Integer, nullable=True)
    idrac_hostname_value: Mapped[str | None] = mapped_column(String, nullable=True)
    dns_from_dhcp: Mapped[str | None] = mapped_column(String, nullable=True)
    ipmi_lan_enable: Mapped[str | None] = mapped_column(String, nullable=True)
    host_header_check: Mapped[str | None] = mapped_column(String, nullable=True)
    sys_profile: Mapped[str | None] = mapped_column(String, nullable=True)
    ssl_self_signed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ssl_valid_name: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ssl_expiry: Mapped[str | None] = mapped_column(String, nullable=True)
    ssl_fingerprint: Mapped[str | None] = mapped_column(String, nullable=True)
    collected_at: Mapped[str | None] = mapped_column(String, nullable=True)


class SiteSslConfig(Base):
    __tablename__ = "site_ssl_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    site_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("sites.id"), nullable=False, unique=True
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    cert_pem: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    key_pem: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    cert_fingerprint: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    cert_expiry: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    schedule_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    schedule_frequency: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    schedule_time: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    schedule_last_run: Mapped[Optional[str]] = mapped_column(String, nullable=True)


class HostSslOverride(Base):
    __tablename__ = "host_ssl_override"
    __table_args__ = (UniqueConstraint("hostname", "site_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    hostname: Mapped[str] = mapped_column(String, nullable=False)
    site_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("sites.id"), nullable=False
    )
    cert_pem: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    key_pem: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    cert_fingerprint: Mapped[Optional[str]] = mapped_column(String, nullable=True)


def make_db_url(path: str) -> str:
    if "://" in path:
        return path
    return f"sqlite:///{path}"


def _migrate_schema(engine) -> None:
    from sqlalchemy import inspect, text

    inspector = inspect(engine)
    tables = inspector.get_table_names()

    if "jobs" in tables:
        columns = {c["name"] for c in inspector.get_columns("jobs")}
        if "metadata_json" not in columns:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE jobs ADD COLUMN metadata_json TEXT"))
        if "site_id" not in columns:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE jobs ADD COLUMN site_id INTEGER"))

    if "systems" in tables:
        columns = {c["name"] for c in inspector.get_columns("systems")}
        if "site_id" not in columns:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE systems ADD COLUMN site_id INTEGER"))

    if "users" in tables:
        user_cols = {c["name"]: c for c in inspector.get_columns("users")}
        if not user_cols.get("role", {}).get("nullable", True):
            with engine.begin() as conn:
                conn.execute(text("""
                    CREATE TABLE users_new (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        username VARCHAR NOT NULL UNIQUE,
                        password_hash VARCHAR NOT NULL,
                        role VARCHAR,
                        created_at VARCHAR NOT NULL,
                        created_by VARCHAR
                    )
                    """))
                conn.execute(text("INSERT INTO users_new SELECT * FROM users"))
                conn.execute(text("DROP TABLE users"))
                conn.execute(text("ALTER TABLE users_new RENAME TO users"))

    if "host_config" in tables:
        with engine.connect() as conn:
            col_types = {
                row[1]: row[2]
                for row in conn.execute(text("PRAGMA table_info(host_config)"))
            }
        needs_rebuild = (
            col_types.get("idrac_hostname", "").upper() in ("TEXT", "VARCHAR")
            or "idrac_hostname_value" not in col_types
        )
        if needs_rebuild:
            with engine.begin() as conn:
                conn.execute(text("DROP TABLE host_config"))
        else:
            hc_cols = {c["name"] for c in inspector.get_columns("host_config")}
            if "ssl_fingerprint" not in hc_cols:
                with engine.begin() as conn:
                    conn.execute(
                        text(
                            "ALTER TABLE host_config ADD COLUMN ssl_fingerprint VARCHAR"
                        )
                    )

    if "sites" in tables:
        site_cols = {c["name"] for c in inspector.get_columns("sites")}
        if "sort_order" not in site_cols:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE sites ADD COLUMN sort_order INTEGER"))
                conn.execute(text("UPDATE sites SET sort_order = id"))
        if "allowed_domains" not in site_cols:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE sites ADD COLUMN allowed_domains TEXT"))


def _grandfather_sites(engine) -> None:
    from sqlalchemy import text

    with engine.begin() as conn:
        row = conn.execute(text("SELECT id FROM sites WHERE is_primary = 1")).fetchone()
        if row is None:
            conn.execute(
                text(
                    "INSERT INTO sites (name, is_primary, created_at) "
                    "VALUES (:name, 1, :ts)"
                ),
                {"name": "Default", "ts": datetime.now().isoformat()},
            )
            row = conn.execute(
                text("SELECT id FROM sites WHERE is_primary = 1")
            ).fetchone()
        default_id = row[0]

        conn.execute(
            text("UPDATE systems SET site_id = :sid WHERE site_id IS NULL"),
            {"sid": default_id},
        )
        conn.execute(
            text("UPDATE jobs SET site_id = :sid WHERE site_id IS NULL"),
            {"sid": default_id},
        )


def db_initialize(db_url: str) -> None:
    global _engine, _SessionFactory
    url = make_db_url(db_url)

    if url.startswith("sqlite"):
        _engine = create_engine(url, poolclass=NullPool)
    else:
        _engine = create_engine(url)  # pragma: no cover

    _migrate_schema(_engine)
    Base.metadata.create_all(_engine)
    _grandfather_sites(_engine)
    _SessionFactory = sessionmaker(bind=_engine)


@contextmanager
def get_session():
    if _SessionFactory is None:
        raise RuntimeError("Database not initialized. Call db_initialize() first.")
    session = _SessionFactory()
    try:
        yield session
    finally:
        session.close()


def query_by_service_tag(service_tag: str) -> List[tuple]:
    with get_session() as session:
        results = session.query(System).filter(System.svc_tag == service_tag).all()
        return [r.to_tuple() for r in results]


def query_by_hostname(hostname: str) -> List[tuple]:
    with get_session() as session:
        results = session.query(System).filter(System.name == hostname).all()
        return [r.to_tuple() for r in results]


def query_by_model(model: str) -> List[tuple]:
    with get_session() as session:
        results = session.query(System).filter(System.model == model).all()
        return [r.to_tuple() for r in results]


def query_all_systems() -> List[tuple]:
    with get_session() as session:
        results = session.query(System).order_by(System.name).all()
        return [r.to_tuple() for r in results]


def upsert_system(
    db_url: str,
    svc_tag: str,
    name: str,
    model: str,
    idrac_version: str,
    bios_version: str,
    exp_date: str,
    exp_epoch: int,
    site_id: Optional[int] = None,
) -> None:
    with get_session() as session:
        existing = session.get(System, svc_tag)
        if existing:
            existing.name = name
            existing.model = model
            existing.idrac_version = idrac_version
            existing.bios_version = bios_version
            existing.exp_date = exp_date
            existing.exp_epoch = exp_epoch
            if site_id is not None:
                existing.site_id = site_id
        else:
            if site_id is None:
                site_id = get_default_site_id()
            system = System(
                svc_tag=svc_tag,
                name=name,
                model=model,
                idrac_version=idrac_version,
                bios_version=bios_version,
                exp_date=exp_date,
                exp_epoch=exp_epoch,
                site_id=site_id,
            )
            session.add(system)
        session.commit()


def get_default_site_id() -> int:
    with get_session() as session:
        site = session.query(Site).filter(Site.is_primary == True).first()  # noqa: E712
        if site is None:
            raise RuntimeError(
                "Default site not found. Database may not be initialized."
            )
        return site.id


def get_primary_site_name() -> str:
    with get_session() as session:
        site = session.query(Site).filter(Site.is_primary == True).first()  # noqa: E712
        return site.name if site else "Default"


def get_site_by_name(name: str) -> Optional[dict]:
    with get_session() as session:
        site = session.query(Site).filter(Site.name == name).first()
        if site is None:
            return None
        return {
            "id": site.id,
            "name": site.name,
            "is_primary": site.is_primary,
            "created_at": site.created_at,
            "allowed_domains": site.allowed_domains,
        }


def get_site_allowed_domains(site_id: int) -> list:
    with get_session() as session:
        site = session.get(Site, site_id)
        if site is None or not site.allowed_domains:
            return []
        return [d.strip() for d in site.allowed_domains.splitlines() if d.strip()]


def update_site_allowed_domains(site_id: int, domains: Optional[str]) -> None:
    with get_session() as session:
        site = session.get(Site, site_id)
        if site is None:
            raise ValueError(f"Site {site_id} not found.")
        site.allowed_domains = domains or None
        session.commit()


def list_sites() -> list:
    from sqlalchemy import func

    with get_session() as session:
        results = (
            session.query(
                Site.id,
                Site.name,
                Site.is_primary,
                Site.created_at,
                func.count(System.svc_tag).label("host_count"),
            )
            .outerjoin(System, System.site_id == Site.id)
            .group_by(Site.id)
            .order_by(func.coalesce(Site.sort_order, Site.id))
            .all()
        )
        return [
            {
                "id": r[0],
                "name": r[1],
                "is_primary": r[2],
                "created_at": r[3],
                "host_count": r[4],
            }
            for r in results
        ]


def create_site(name: str) -> dict:
    from sqlalchemy import func as _func

    with get_session() as session:
        max_order = session.query(_func.max(Site.sort_order)).scalar() or 0
        site = Site(
            name=name,
            is_primary=False,
            created_at=datetime.now().isoformat(),
            sort_order=max_order + 1,
        )
        session.add(site)
        session.commit()
        session.refresh(site)
        return {
            "id": site.id,
            "name": site.name,
            "is_primary": site.is_primary,
            "created_at": site.created_at,
        }


def delete_site(site_id: int) -> bool:
    with get_session() as session:
        site = session.get(Site, site_id)
        if site is None:
            return False
        if site.is_primary:
            raise ValueError("Cannot delete the primary site.")
        host_count = session.query(System).filter(System.site_id == site_id).count()
        if host_count > 0:
            raise ValueError(
                f"Cannot delete site with {host_count} assigned system(s)."
            )
        session.query(UserSiteRole).filter(UserSiteRole.site_id == site_id).delete()
        session.delete(site)
        session.commit()
        return True


def rename_site(site_id: int, new_name: str) -> bool:
    with get_session() as session:
        site = session.get(Site, site_id)
        if site is None:
            return False
        site.name = new_name
        session.commit()
        return True


def set_primary_site(site_id: int) -> bool:
    with get_session() as session:
        site = session.get(Site, site_id)
        if site is None:
            return False
        session.query(Site).filter(Site.is_primary == True).update(  # noqa: E712
            {"is_primary": False}
        )
        site.is_primary = True
        session.commit()
        return True


def reorder_sites(ordered_ids: list) -> None:
    with get_session() as session:
        for position, site_id in enumerate(ordered_ids):
            session.query(Site).filter(Site.id == site_id).update(
                {"sort_order": position}
            )
        session.commit()


_COLLECTION_ATTRS = [
    "ps_rapid_on",
    "dns_from_dhcp",
    "ipmi_lan_enable",
    "host_header_check",
    "sys_profile",
    "ssl",
    "idrac_hostname",
]


def get_site_config_collection(site_id: int) -> dict:
    with get_session() as session:
        row = (
            session.query(SiteConfigCollection)
            .filter(SiteConfigCollection.site_id == site_id)
            .first()
        )
        if row is None:
            result = {}
            for attr in _COLLECTION_ATTRS:
                result[f"{attr}_enabled"] = False
                result[f"{attr}_hours"] = 24
            return result
        result = {}
        for attr in _COLLECTION_ATTRS:
            result[f"{attr}_enabled"] = getattr(row, f"{attr}_enabled")
            result[f"{attr}_hours"] = getattr(row, f"{attr}_hours")
        return result


def upsert_site_config_collection(site_id: int, settings: dict) -> None:
    with get_session() as session:
        row = (
            session.query(SiteConfigCollection)
            .filter(SiteConfigCollection.site_id == site_id)
            .first()
        )
        if row is None:
            row = SiteConfigCollection(site_id=site_id)
            session.add(row)
        for key, value in settings.items():
            if hasattr(row, key):
                setattr(row, key, value)
        session.commit()


def get_host_config_data(site_id: int, hostnames: list) -> list:
    with get_session() as session:
        query = session.query(HostConfig).filter(HostConfig.site_id == site_id)
        if hostnames:
            query = query.filter(HostConfig.hostname.in_(hostnames))
        rows = query.order_by(HostConfig.hostname).all()
        return [
            {
                "hostname": r.hostname,
                "ps_rapid_on": r.ps_rapid_on,
                "idrac_hostname": r.idrac_hostname,
                "idrac_hostname_value": r.idrac_hostname_value,
                "dns_from_dhcp": r.dns_from_dhcp,
                "ipmi_lan_enable": r.ipmi_lan_enable,
                "host_header_check": r.host_header_check,
                "sys_profile": r.sys_profile,
                "ssl_self_signed": r.ssl_self_signed,
                "ssl_valid_name": r.ssl_valid_name,
                "ssl_expiry": r.ssl_expiry,
                "ssl_fingerprint": r.ssl_fingerprint,
                "collected_at": r.collected_at,
            }
            for r in rows
        ]


def upsert_host_config(hostname: str, site_id: int, data: dict) -> None:
    with get_session() as session:
        row = (
            session.query(HostConfig)
            .filter(
                HostConfig.hostname == hostname,
                HostConfig.site_id == site_id,
            )
            .first()
        )
        if row is None:
            row = HostConfig(hostname=hostname, site_id=site_id)
            session.add(row)
        for key, value in data.items():
            if hasattr(row, key):
                setattr(row, key, value)
        session.commit()


def get_hosts_for_site(site_id: int) -> list:
    with get_session() as session:
        systems = (
            session.query(System)
            .filter(System.site_id == site_id, System.name.isnot(None))
            .all()
        )
        return [{"hostname": s.name, "svc_tag": s.svc_tag} for s in systems]


# ── SSL certificate management ──────────────────────────────────────────────


def get_site_ssl_config(site_id: int) -> dict:
    """Return SSL cert management config for a site, with safe defaults."""
    with get_session() as session:
        row = (
            session.query(SiteSslConfig)
            .filter(SiteSslConfig.site_id == site_id)
            .first()
        )
        if row is None:
            return {
                "enabled": False,
                "has_cert": False,
                "has_key": False,
                "cert_pem": None,
                "key_pem": None,
                "cert_fingerprint": None,
                "cert_expiry": None,
                "schedule_enabled": False,
                "schedule_frequency": None,
                "schedule_time": None,
                "schedule_last_run": None,
            }
        return {
            "enabled": bool(row.enabled),
            "has_cert": bool(row.cert_pem),
            "has_key": bool(row.key_pem),
            "cert_pem": row.cert_pem,
            "key_pem": row.key_pem,
            "cert_fingerprint": row.cert_fingerprint,
            "cert_expiry": row.cert_expiry,
            "schedule_enabled": bool(row.schedule_enabled),
            "schedule_frequency": row.schedule_frequency,
            "schedule_time": row.schedule_time,
            "schedule_last_run": row.schedule_last_run,
        }


def upsert_site_ssl_config(site_id: int, data: dict) -> None:
    """Create or update SSL cert management config for a site."""
    with get_session() as session:
        row = (
            session.query(SiteSslConfig)
            .filter(SiteSslConfig.site_id == site_id)
            .first()
        )
        if row is None:
            row = SiteSslConfig(site_id=site_id)
            session.add(row)
        if "enabled" in data:
            row.enabled = bool(data["enabled"])
        if "cert_pem" in data:
            row.cert_pem = data["cert_pem"] or None
        if "key_pem" in data:
            row.key_pem = data["key_pem"] or None
        if "cert_fingerprint" in data:
            row.cert_fingerprint = data["cert_fingerprint"] or None
        if "cert_expiry" in data:
            row.cert_expiry = data["cert_expiry"] or None
        if "schedule_enabled" in data:
            row.schedule_enabled = bool(data["schedule_enabled"])
        if "schedule_frequency" in data:
            row.schedule_frequency = data["schedule_frequency"] or None
        if "schedule_time" in data:
            row.schedule_time = data["schedule_time"] or None
        session.commit()


def get_host_ssl_override(hostname: str, site_id: int) -> Optional[dict]:
    """Return per-host SSL cert override, or None if not set."""
    with get_session() as session:
        row = (
            session.query(HostSslOverride)
            .filter(
                HostSslOverride.hostname == hostname,
                HostSslOverride.site_id == site_id,
            )
            .first()
        )
        if row is None:
            return None
        return {
            "hostname": row.hostname,
            "has_cert": bool(row.cert_pem),
            "has_key": bool(row.key_pem),
            "cert_pem": row.cert_pem,
            "key_pem": row.key_pem,
            "cert_fingerprint": row.cert_fingerprint,
        }


def upsert_host_ssl_override(hostname: str, site_id: int, data: dict) -> None:
    """Create or update per-host SSL cert override."""
    with get_session() as session:
        row = (
            session.query(HostSslOverride)
            .filter(
                HostSslOverride.hostname == hostname,
                HostSslOverride.site_id == site_id,
            )
            .first()
        )
        if row is None:
            row = HostSslOverride(hostname=hostname, site_id=site_id)
            session.add(row)
        if "cert_pem" in data:
            row.cert_pem = data["cert_pem"] or None
        if "key_pem" in data:
            row.key_pem = data["key_pem"] or None
        if "cert_fingerprint" in data:
            row.cert_fingerprint = data["cert_fingerprint"] or None
        session.commit()


def delete_host_ssl_override(hostname: str, site_id: int) -> bool:
    """Remove per-host SSL cert override. Returns True if a row was deleted."""
    with get_session() as session:
        row = (
            session.query(HostSslOverride)
            .filter(
                HostSslOverride.hostname == hostname,
                HostSslOverride.site_id == site_id,
            )
            .first()
        )
        if row is None:
            return False
        session.delete(row)
        session.commit()
        return True


def get_all_host_ssl_overrides(site_id: int) -> dict:
    """Return {hostname: {has_cert, has_key, cert_fingerprint}} for all hosts in site."""
    with get_session() as session:
        rows = (
            session.query(HostSslOverride)
            .filter(HostSslOverride.site_id == site_id)
            .all()
        )
        return {
            row.hostname: {
                "has_cert": bool(row.cert_pem),
                "has_key": bool(row.key_pem),
                "cert_fingerprint": row.cert_fingerprint,
            }
            for row in rows
        }


def get_all_ssl_scheduled_sites() -> list:
    """Return all sites with SSL cert management enabled and a schedule configured."""
    with get_session() as session:
        rows = (
            session.query(SiteSslConfig)
            .filter(
                SiteSslConfig.enabled.is_(True),
                SiteSslConfig.schedule_enabled.is_(True),
            )
            .all()
        )
        result = []
        for row in rows:
            site = session.get(Site, row.site_id)
            if site:
                result.append(
                    {
                        "site_id": row.site_id,
                        "site_name": site.name,
                        "enabled": True,
                        "schedule_enabled": True,
                        "schedule_frequency": row.schedule_frequency,
                        "schedule_time": row.schedule_time,
                        "schedule_last_run": row.schedule_last_run,
                    }
                )
        return result


def update_ssl_schedule_last_run(site_id: int) -> None:
    """Stamp schedule_last_run to now for a site's SSL config."""
    with get_session() as session:
        row = (
            session.query(SiteSslConfig)
            .filter(SiteSslConfig.site_id == site_id)
            .first()
        )
        if row:
            row.schedule_last_run = datetime.now().isoformat()
            session.commit()
