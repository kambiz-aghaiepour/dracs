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
                conn.execute(
                    text(
                        """
                    CREATE TABLE users_new (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        username VARCHAR NOT NULL UNIQUE,
                        password_hash VARCHAR NOT NULL,
                        role VARCHAR,
                        created_at VARCHAR NOT NULL,
                        created_by VARCHAR
                    )
                    """
                    )
                )
                conn.execute(text("INSERT INTO users_new SELECT * FROM users"))
                conn.execute(text("DROP TABLE users"))
                conn.execute(text("ALTER TABLE users_new RENAME TO users"))


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
        }


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
            .order_by(Site.id)
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
    with get_session() as session:
        site = Site(
            name=name,
            is_primary=False,
            created_at=datetime.now().isoformat(),
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
