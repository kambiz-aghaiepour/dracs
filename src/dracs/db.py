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


# ── Flexible config collection schema ────────────────────────────────────────


class ConfigAttrDef(Base):
    """Global catalog of collectible iDRAC attributes."""

    __tablename__ = "config_attr_def"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    label: Mapped[str] = mapped_column(String, nullable=False)
    endpoint_type: Mapped[str] = mapped_column(String, nullable=False)
    attribute_path: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    push_key: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    is_writable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    post_push_command: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    display_type: Mapped[str] = mapped_column(String, nullable=False, default="string")
    display_order: Mapped[int] = mapped_column(Integer, nullable=False, default=100)


class ConfigAttrChoice(Base):
    """Named, selectable desired values for a writable attribute."""

    __tablename__ = "config_attr_choice"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    attr_def_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("config_attr_def.id"), nullable=False
    )
    choice_label: Mapped[str] = mapped_column(String, nullable=False)
    push_value: Mapped[str] = mapped_column(String, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class ConfigAttrSiteSettings(Base):
    """Per-site enable/refresh/desired-value settings for each attribute."""

    __tablename__ = "config_attr_site_settings"
    __table_args__ = (UniqueConstraint("attr_def_id", "site_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    attr_def_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("config_attr_def.id"), nullable=False
    )
    site_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("sites.id"), nullable=False
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    hours: Mapped[int] = mapped_column(Integer, nullable=False, default=24)
    desired_choice_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("config_attr_choice.id"), nullable=True
    )


class HostConfigAttr(Base):
    """EAV table: one row per (hostname, site, attribute), storing the collected value."""

    __tablename__ = "host_config_attr"
    __table_args__ = (UniqueConstraint("hostname", "site_id", "attr_def_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    hostname: Mapped[str] = mapped_column(String, nullable=False)
    site_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("sites.id"), nullable=False
    )
    attr_def_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("config_attr_def.id"), nullable=False
    )
    value: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    collected_at: Mapped[Optional[str]] = mapped_column(String, nullable=True)


# ── SSL certificate management ────────────────────────────────────────────────


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


# ── Seed data for the attribute catalog ───────────────────────────────────────

_SEED_ATTR_DEFS = [
    {
        "name": "ps_rapid_on",
        "label": "PS Rapid On",
        "endpoint_type": "system_oem_dell",
        "attribute_path": "Attributes.ServerPwr.1.PSRapidOn",
        "push_key": "System.ServerPwr.PSRapidOn",
        "is_writable": True,
        "post_push_command": None,
        "display_type": "bool",
        "display_order": 10,
        "choices": [("Disabled", "Disabled", 0), ("Enabled", "Enabled", 1)],
    },
    {
        "name": "dns_from_dhcp",
        "label": "DNS from DHCP",
        "endpoint_type": "idrac_attributes",
        "attribute_path": "Attributes.IPv4.1.DNSFromDHCP",
        "push_key": "iDRAC.IPv4.DNSFromDHCP",
        "is_writable": True,
        "post_push_command": None,
        "display_type": "bool",
        "display_order": 20,
        "choices": [("Enabled", "Enabled", 0), ("Disabled", "Disabled", 1)],
    },
    {
        "name": "ipmi_lan_enable",
        "label": "IPMI LAN",
        "endpoint_type": "idrac_attributes",
        "attribute_path": "Attributes.IPMILan.1.Enable",
        "push_key": "iDRAC.IPMILan.Enable",
        "is_writable": True,
        "post_push_command": None,
        "display_type": "bool",
        "display_order": 30,
        "choices": [("Enabled", "Enabled", 0), ("Disabled", "Disabled", 1)],
    },
    {
        "name": "host_header_check",
        "label": "Host Header Check",
        "endpoint_type": "idrac_attributes",
        "attribute_path": "Attributes.WebServer.1.HostHeaderCheck",
        "push_key": "iDRAC.webserver.HostHeaderCheck",
        "is_writable": True,
        "post_push_command": None,
        "display_type": "bool",
        "display_order": 40,
        "choices": [("Enabled", "Enabled", 0), ("Disabled", "Disabled", 1)],
    },
    {
        "name": "sys_profile",
        "label": "Sys Profile",
        "endpoint_type": "bios",
        "attribute_path": "Attributes.SysProfile",
        "push_key": "BIOS.SysProfileSettings.SysProfile",
        "is_writable": True,
        "post_push_command": "jobqueue create BIOS.Setup.1-1",
        "display_type": "bool",
        "display_order": 50,
        "choices": [
            ("PerfPerWattOptimizedDapc", "PerfPerWattOptimizedDapc", 0),
            ("PerfPerWattOptimizedOs", "PerfPerWattOptimizedOs", 1),
            ("PerfOptimized", "PerfOptimized", 2),
            ("DenseCfgOptimized", "DenseCfgOptimized", 3),
            ("Custom", "Custom", 4),
        ],
    },
    {
        "name": "idrac_hostname",
        "label": "iDRAC Hostname",
        "endpoint_type": "system",
        "attribute_path": "HostName",
        "push_key": "System.ServerOS.Hostname",
        "is_writable": True,
        "post_push_command": None,
        "display_type": "int_bool",
        "display_order": 60,
        "choices": [("Match FQDN", "{idrac_fqdn}", 0)],
    },
    {
        "name": "ssl_self_signed",
        "label": "SSL CA-Signed",
        "endpoint_type": "ssl",
        "attribute_path": None,
        "push_key": None,
        "is_writable": False,
        "post_push_command": None,
        "display_type": "int_bool",
        "display_order": 70,
        "choices": [],
    },
    {
        "name": "ssl_valid_name",
        "label": "SSL Valid Name",
        "endpoint_type": "ssl",
        "attribute_path": None,
        "push_key": None,
        "is_writable": False,
        "post_push_command": None,
        "display_type": "int_bool",
        "display_order": 80,
        "choices": [],
    },
    {
        "name": "ssl_expiry",
        "label": "SSL Expiry",
        "endpoint_type": "ssl",
        "attribute_path": None,
        "push_key": None,
        "is_writable": False,
        "post_push_command": None,
        "display_type": "date",
        "display_order": 90,
        "choices": [],
    },
    {
        "name": "ssl_fingerprint",
        "label": "SSL Fingerprint",
        "endpoint_type": "ssl",
        "attribute_path": None,
        "push_key": None,
        "is_writable": False,
        "post_push_command": None,
        "display_type": "string",
        "display_order": 100,
        "choices": [],
    },
]

# Maps old site_config_collection column prefixes to new attr_def names.
# ssl is handled separately (one old column → four new attrs).
_OLD_SCC_ATTR_MAP = [
    ("ps_rapid_on", "ps_rapid_on"),
    ("dns_from_dhcp", "dns_from_dhcp"),
    ("ipmi_lan_enable", "ipmi_lan_enable"),
    ("host_header_check", "host_header_check"),
    ("sys_profile", "sys_profile"),
    ("idrac_hostname", "idrac_hostname"),
]
_OLD_SCC_SSL_ATTRS = [
    "ssl_self_signed",
    "ssl_valid_name",
    "ssl_expiry",
    "ssl_fingerprint",
]

# Maps old host_config column names to new attr_def names (column index in raw SELECT).
_OLD_HC_ATTR_MAP = [
    ("ps_rapid_on", 2),
    ("dns_from_dhcp", 3),
    ("ipmi_lan_enable", 4),
    ("host_header_check", 5),
    ("sys_profile", 6),
    ("idrac_hostname", 7),
    ("ssl_self_signed", 8),
    ("ssl_valid_name", 9),
    ("ssl_expiry", 10),
    ("ssl_fingerprint", 11),
]


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

    if "users" in tables:  # pragma: no cover
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
        if needs_rebuild:  # pragma: no cover
            with engine.begin() as conn:
                conn.execute(text("DROP TABLE host_config"))
        else:
            hc_cols = {c["name"] for c in inspector.get_columns("host_config")}
            if "ssl_fingerprint" not in hc_cols:  # pragma: no cover
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


def _seed_attr_defs(engine) -> None:
    """Populate config_attr_def and config_attr_choice if the catalog is empty."""
    from sqlalchemy import text

    with engine.begin() as conn:
        count = conn.execute(text("SELECT COUNT(*) FROM config_attr_def")).scalar()
        if count:
            return

        for defn in _SEED_ATTR_DEFS:
            result = conn.execute(
                text(
                    "INSERT INTO config_attr_def "
                    "(name, label, endpoint_type, attribute_path, push_key, "
                    " is_writable, post_push_command, display_type, display_order) "
                    "VALUES (:name, :label, :endpoint_type, :attribute_path, :push_key, "
                    "        :is_writable, :post_push_command, :display_type, :display_order)"
                ),
                {
                    "name": defn["name"],
                    "label": defn["label"],
                    "endpoint_type": defn["endpoint_type"],
                    "attribute_path": defn["attribute_path"],
                    "push_key": defn["push_key"],
                    "is_writable": defn["is_writable"],
                    "post_push_command": defn["post_push_command"],
                    "display_type": defn["display_type"],
                    "display_order": defn["display_order"],
                },
            )
            attr_def_id = result.lastrowid

            for choice_label, push_value, sort_order in defn["choices"]:
                conn.execute(
                    text(
                        "INSERT INTO config_attr_choice "
                        "(attr_def_id, choice_label, push_value, sort_order) "
                        "VALUES (:attr_def_id, :choice_label, :push_value, :sort_order)"
                    ),
                    {
                        "attr_def_id": attr_def_id,
                        "choice_label": choice_label,
                        "push_value": push_value,
                        "sort_order": sort_order,
                    },
                )


def _upsert_site_setting(
    conn, attr_def_id: int, site_id: int, enabled: bool, hours: int
) -> None:
    from sqlalchemy import text

    conn.execute(
        text(
            "INSERT OR IGNORE INTO config_attr_site_settings "
            "(attr_def_id, site_id, enabled, hours) "
            "VALUES (:attr_def_id, :site_id, :enabled, :hours)"
        ),
        {
            "attr_def_id": attr_def_id,
            "site_id": site_id,
            "enabled": enabled,
            "hours": hours,
        },
    )


def _migrate_scc_rows(conn, rows, attr_ids: dict) -> None:
    for row in rows:
        site_id = row[0]
        per_attr = {
            "ps_rapid_on": (bool(row[1]), int(row[2])),
            "dns_from_dhcp": (bool(row[3]), int(row[4])),
            "ipmi_lan_enable": (bool(row[5]), int(row[6])),
            "host_header_check": (bool(row[7]), int(row[8])),
            "sys_profile": (bool(row[9]), int(row[10])),
            "idrac_hostname": (bool(row[13]), int(row[14])),
        }
        ssl_enabled, ssl_hours = bool(row[11]), int(row[12])

        for attr_name, (enabled, hours) in per_attr.items():
            attr_def_id = attr_ids.get(attr_name)
            if attr_def_id is not None:
                _upsert_site_setting(conn, attr_def_id, site_id, enabled, hours)

        for ssl_attr in _OLD_SCC_SSL_ATTRS:
            attr_def_id = attr_ids.get(ssl_attr)
            if attr_def_id is not None:
                _upsert_site_setting(conn, attr_def_id, site_id, ssl_enabled, ssl_hours)


def _migrate_hc_rows(conn, rows, attr_ids: dict) -> None:
    from sqlalchemy import text

    for row in rows:
        hostname, site_id, collected_at = row[0], row[1], row[12]
        for attr_name, col_idx in _OLD_HC_ATTR_MAP:
            raw_val = row[col_idx]
            if raw_val is None:
                continue
            attr_def_id = attr_ids.get(attr_name)
            if attr_def_id is None:  # pragma: no cover
                continue
            conn.execute(
                text(
                    "INSERT OR IGNORE INTO host_config_attr "
                    "(hostname, site_id, attr_def_id, value, collected_at) "
                    "VALUES (:hostname, :site_id, :attr_def_id, :value, :collected_at)"
                ),
                {
                    "hostname": hostname,
                    "site_id": site_id,
                    "attr_def_id": attr_def_id,
                    "value": str(raw_val),
                    "collected_at": collected_at,
                },
            )


def _migrate_collection_tables(engine) -> None:
    """One-time migration: move site_config_collection and host_config to EAV model.

    No-op when the old tables are absent (fresh install or already migrated).
    """
    from sqlalchemy import inspect, text

    tables = inspect(engine).get_table_names()

    with engine.begin() as conn:
        attr_ids = {
            row[0]: row[1]
            for row in conn.execute(text("SELECT name, id FROM config_attr_def"))
        }
        if not attr_ids:  # pragma: no cover
            return  # Seed hasn't run; nothing to migrate against.

        if "site_config_collection" in tables:
            rows = conn.execute(
                text(
                    "SELECT site_id, "
                    "ps_rapid_on_enabled, ps_rapid_on_hours, "
                    "dns_from_dhcp_enabled, dns_from_dhcp_hours, "
                    "ipmi_lan_enable_enabled, ipmi_lan_enable_hours, "
                    "host_header_check_enabled, host_header_check_hours, "
                    "sys_profile_enabled, sys_profile_hours, "
                    "ssl_enabled, ssl_hours, "
                    "idrac_hostname_enabled, idrac_hostname_hours "
                    "FROM site_config_collection"
                )
            ).fetchall()
            _migrate_scc_rows(conn, rows, attr_ids)
            conn.execute(text("DROP TABLE site_config_collection"))

        if "host_config" in tables:
            rows = conn.execute(
                text(
                    "SELECT hostname, site_id, ps_rapid_on, dns_from_dhcp, "
                    "ipmi_lan_enable, host_header_check, sys_profile, "
                    "idrac_hostname, ssl_self_signed, ssl_valid_name, "
                    "ssl_expiry, ssl_fingerprint, collected_at "
                    "FROM host_config"
                )
            ).fetchall()
            _migrate_hc_rows(conn, rows, attr_ids)
            conn.execute(text("DROP TABLE host_config"))


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
    _seed_attr_defs(_engine)
    _migrate_collection_tables(_engine)
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


def get_hosts_for_site(site_id: int) -> list:
    with get_session() as session:
        systems = (
            session.query(System)
            .filter(System.site_id == site_id, System.name.isnot(None))
            .all()
        )
        return [{"hostname": s.name, "svc_tag": s.svc_tag} for s in systems]


# ── Config attribute catalog accessors ───────────────────────────────────────


def _build_catalog_entry(d, site_cfg, choices: list, session) -> dict:
    desired_value = None
    if site_cfg and site_cfg.desired_choice_id:
        ch = session.get(ConfigAttrChoice, site_cfg.desired_choice_id)
        if ch:
            desired_value = ch.push_value
    return {
        "id": d.id,
        "name": d.name,
        "label": d.label,
        "endpoint_type": d.endpoint_type,
        "push_key": d.push_key,
        "is_writable": d.is_writable,
        "post_push_command": d.post_push_command,
        "display_type": d.display_type,
        "display_order": d.display_order,
        "choices": [
            {"id": c.id, "label": c.choice_label, "push_value": c.push_value}
            for c in choices
        ],
        "site_settings": {
            "enabled": bool(site_cfg.enabled) if site_cfg else False,
            "hours": site_cfg.hours if site_cfg else 24,
            "desired_choice_id": site_cfg.desired_choice_id if site_cfg else None,
            "desired_value": desired_value,
        },
    }


def get_attr_catalog_for_site(site_id: int) -> list:
    """Return all attr defs with per-site settings and choices, in display order."""
    with get_session() as session:
        defs = session.query(ConfigAttrDef).order_by(ConfigAttrDef.display_order).all()
        settings_map = {
            r.attr_def_id: r
            for r in session.query(ConfigAttrSiteSettings)
            .filter(ConfigAttrSiteSettings.site_id == site_id)
            .all()
        }
        result = []
        for d in defs:
            choices = (
                session.query(ConfigAttrChoice)
                .filter(ConfigAttrChoice.attr_def_id == d.id)
                .order_by(ConfigAttrChoice.sort_order)
                .all()
            )
            result.append(
                _build_catalog_entry(d, settings_map.get(d.id), choices, session)
            )
        return result


def get_enabled_attr_defs_for_site(site_id: int) -> list:
    """Return only enabled attr defs with site settings. Used by the collector."""
    return [
        a for a in get_attr_catalog_for_site(site_id) if a["site_settings"]["enabled"]
    ]


def get_host_config_attrs(site_id: int, hostnames: list) -> list:
    """Return per-host EAV data as a list of host dicts.

    Each dict: {hostname, attrs: {attr_name: {value, collected_at}}}
    """
    with get_session() as session:
        attr_names = {d.id: d.name for d in session.query(ConfigAttrDef).all()}

        query = session.query(HostConfigAttr).filter(HostConfigAttr.site_id == site_id)
        if hostnames:
            query = query.filter(HostConfigAttr.hostname.in_(hostnames))

        host_map: dict = {}
        for r in query.all():
            if r.hostname not in host_map:
                host_map[r.hostname] = {"hostname": r.hostname, "attrs": {}}
            attr_name = attr_names.get(r.attr_def_id, str(r.attr_def_id))
            host_map[r.hostname]["attrs"][attr_name] = {
                "value": r.value,
                "collected_at": r.collected_at,
            }

        return sorted(host_map.values(), key=lambda h: h["hostname"])


def upsert_host_config_attr(
    hostname: str,
    site_id: int,
    attr_def_id: int,
    value: Optional[str],
    collected_at: str,
) -> None:
    """Insert or update one EAV row in host_config_attr."""
    with get_session() as session:
        row = (
            session.query(HostConfigAttr)
            .filter(
                HostConfigAttr.hostname == hostname,
                HostConfigAttr.site_id == site_id,
                HostConfigAttr.attr_def_id == attr_def_id,
            )
            .first()
        )
        if row is None:
            row = HostConfigAttr(
                hostname=hostname, site_id=site_id, attr_def_id=attr_def_id
            )
            session.add(row)
        row.value = value
        row.collected_at = collected_at
        session.commit()


def get_attr_def_by_name(name: str) -> Optional[dict]:
    """Return a single attr def dict by name, including choices."""
    with get_session() as session:
        d = session.query(ConfigAttrDef).filter(ConfigAttrDef.name == name).first()
        if d is None:
            return None
        choices = (
            session.query(ConfigAttrChoice)
            .filter(ConfigAttrChoice.attr_def_id == d.id)
            .order_by(ConfigAttrChoice.sort_order)
            .all()
        )
        return {
            "id": d.id,
            "name": d.name,
            "label": d.label,
            "endpoint_type": d.endpoint_type,
            "attribute_path": d.attribute_path,
            "push_key": d.push_key,
            "is_writable": d.is_writable,
            "post_push_command": d.post_push_command,
            "display_type": d.display_type,
            "display_order": d.display_order,
            "choices": [
                {"id": c.id, "label": c.choice_label, "push_value": c.push_value}
                for c in choices
            ],
        }


def get_all_attr_defs() -> list:
    """Return all attr defs in display order, with choices. Used by the admin catalog UI."""
    with get_session() as session:
        defs = session.query(ConfigAttrDef).order_by(ConfigAttrDef.display_order).all()
        result = []
        for d in defs:
            choices = (
                session.query(ConfigAttrChoice)
                .filter(ConfigAttrChoice.attr_def_id == d.id)
                .order_by(ConfigAttrChoice.sort_order)
                .all()
            )
            result.append(
                {
                    "id": d.id,
                    "name": d.name,
                    "label": d.label,
                    "endpoint_type": d.endpoint_type,
                    "attribute_path": d.attribute_path,
                    "push_key": d.push_key,
                    "is_writable": d.is_writable,
                    "post_push_command": d.post_push_command,
                    "display_type": d.display_type,
                    "display_order": d.display_order,
                    "choices": [
                        {
                            "id": c.id,
                            "label": c.choice_label,
                            "push_value": c.push_value,
                            "sort_order": c.sort_order,
                        }
                        for c in choices
                    ],
                }
            )
        return result


def create_attr_def(
    name: str,
    label: str,
    endpoint_type: str,
    attribute_path: Optional[str],
    push_key: Optional[str],
    is_writable: bool,
    post_push_command: Optional[str],
    display_type: str,
    display_order: int,
    choices: list,
) -> dict:
    """Insert a new attr def + choices. Returns the new entry dict."""
    with get_session() as session:
        d = ConfigAttrDef(
            name=name,
            label=label,
            endpoint_type=endpoint_type,
            attribute_path=attribute_path or None,
            push_key=push_key or None,
            is_writable=is_writable,
            post_push_command=post_push_command or None,
            display_type=display_type,
            display_order=display_order,
        )
        session.add(d)
        session.flush()
        new_choices = []
        for i, ch in enumerate(choices):
            c = ConfigAttrChoice(
                attr_def_id=d.id,
                choice_label=ch["label"],
                push_value=ch["push_value"],
                sort_order=i,
            )
            session.add(c)
            new_choices.append(c)
        session.commit()
        return {
            "id": d.id,
            "name": d.name,
            "label": d.label,
            "endpoint_type": d.endpoint_type,
            "attribute_path": d.attribute_path,
            "push_key": d.push_key,
            "is_writable": d.is_writable,
            "post_push_command": d.post_push_command,
            "display_type": d.display_type,
            "display_order": d.display_order,
            "choices": [
                {
                    "id": c.id,
                    "label": c.choice_label,
                    "push_value": c.push_value,
                    "sort_order": c.sort_order,
                }
                for c in new_choices
            ],
        }


def update_attr_def(
    attr_def_id: int,
    name: str,
    label: str,
    endpoint_type: str,
    attribute_path: Optional[str],
    push_key: Optional[str],
    is_writable: bool,
    post_push_command: Optional[str],
    display_type: str,
    display_order: int,
    choices: list,
) -> dict:
    """Update an attr def and replace its choices. Nullifies desired_choice_id on site settings first."""
    with get_session() as session:
        d = session.query(ConfigAttrDef).filter(ConfigAttrDef.id == attr_def_id).first()
        if d is None:
            raise ValueError(f"attr_def_id {attr_def_id} not found")
        # Nullify desired_choice_id before deleting old choices to avoid FK violation
        session.query(ConfigAttrSiteSettings).filter(
            ConfigAttrSiteSettings.attr_def_id == attr_def_id
        ).update({"desired_choice_id": None}, synchronize_session=False)
        session.query(ConfigAttrChoice).filter(
            ConfigAttrChoice.attr_def_id == attr_def_id
        ).delete(synchronize_session=False)
        d.name = name
        d.label = label
        d.endpoint_type = endpoint_type
        d.attribute_path = attribute_path or None
        d.push_key = push_key or None
        d.is_writable = is_writable
        d.post_push_command = post_push_command or None
        d.display_type = display_type
        d.display_order = display_order
        new_choices = []
        for i, ch in enumerate(choices):
            c = ConfigAttrChoice(
                attr_def_id=attr_def_id,
                choice_label=ch["label"],
                push_value=ch["push_value"],
                sort_order=i,
            )
            session.add(c)
            new_choices.append(c)
        session.commit()
        return {
            "id": d.id,
            "name": d.name,
            "label": d.label,
            "endpoint_type": d.endpoint_type,
            "attribute_path": d.attribute_path,
            "push_key": d.push_key,
            "is_writable": d.is_writable,
            "post_push_command": d.post_push_command,
            "display_type": d.display_type,
            "display_order": d.display_order,
            "choices": [
                {
                    "id": c.id,
                    "label": c.choice_label,
                    "push_value": c.push_value,
                    "sort_order": c.sort_order,
                }
                for c in new_choices
            ],
        }


def delete_attr_def(attr_def_id: int) -> dict:
    """Delete an attr def and all associated data. Returns counts of deleted records."""
    with get_session() as session:
        n_host = (
            session.query(HostConfigAttr)
            .filter(HostConfigAttr.attr_def_id == attr_def_id)
            .count()
        )
        n_site = (
            session.query(ConfigAttrSiteSettings)
            .filter(ConfigAttrSiteSettings.attr_def_id == attr_def_id)
            .count()
        )
        session.query(ConfigAttrSiteSettings).filter(
            ConfigAttrSiteSettings.attr_def_id == attr_def_id
        ).update({"desired_choice_id": None}, synchronize_session=False)
        session.query(HostConfigAttr).filter(
            HostConfigAttr.attr_def_id == attr_def_id
        ).delete(synchronize_session=False)
        session.query(ConfigAttrSiteSettings).filter(
            ConfigAttrSiteSettings.attr_def_id == attr_def_id
        ).delete(synchronize_session=False)
        session.query(ConfigAttrChoice).filter(
            ConfigAttrChoice.attr_def_id == attr_def_id
        ).delete(synchronize_session=False)
        session.query(ConfigAttrDef).filter(ConfigAttrDef.id == attr_def_id).delete(
            synchronize_session=False
        )
        session.commit()
        return {"deleted_host_records": n_host, "deleted_site_settings": n_site}


def upsert_attr_site_settings(
    attr_def_id: int,
    site_id: int,
    enabled: bool,
    hours: int,
    desired_choice_id: Optional[int],
) -> None:
    """Create or update per-site settings for one attribute."""
    with get_session() as session:
        row = (
            session.query(ConfigAttrSiteSettings)
            .filter(
                ConfigAttrSiteSettings.attr_def_id == attr_def_id,
                ConfigAttrSiteSettings.site_id == site_id,
            )
            .first()
        )
        if row is None:
            row = ConfigAttrSiteSettings(attr_def_id=attr_def_id, site_id=site_id)
            session.add(row)
        row.enabled = enabled
        row.hours = hours
        row.desired_choice_id = desired_choice_id
        session.commit()


# ── SSL certificate management ────────────────────────────────────────────────


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
