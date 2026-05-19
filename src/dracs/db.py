from contextlib import contextmanager
from typing import List

from sqlalchemy import create_engine, ForeignKey, String, Integer
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


class System(Base):
    __tablename__ = "systems"

    svc_tag: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str | None] = mapped_column(String)
    model: Mapped[str | None] = mapped_column(String)
    idrac_version: Mapped[str | None] = mapped_column(String)
    bios_version: Mapped[str | None] = mapped_column(String)
    exp_date: Mapped[str | None] = mapped_column(String)
    exp_epoch: Mapped[int | None] = mapped_column(Integer)

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


def make_db_url(path: str) -> str:
    if "://" in path:
        return path
    return f"sqlite:///{path}"


def db_initialize(db_url: str) -> None:
    global _engine, _SessionFactory
    url = make_db_url(db_url)

    # Use NullPool for SQLite to prevent connection pooling issues
    # which can cause "too many open files" errors during mass operations
    if url.startswith("sqlite"):
        _engine = create_engine(url, poolclass=NullPool)
    else:
        _engine = create_engine(url)  # pragma: no cover

    Base.metadata.create_all(_engine)
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


def query_by_service_tag(db_url: str, service_tag: str) -> List[tuple]:
    with get_session() as session:
        results = session.query(System).filter(System.svc_tag == service_tag).all()
        return [r.to_tuple() for r in results]


def query_by_hostname(db_url: str, hostname: str) -> List[tuple]:
    with get_session() as session:
        results = session.query(System).filter(System.name == hostname).all()
        return [r.to_tuple() for r in results]


def query_by_model(db_url: str, model: str) -> List[tuple]:
    with get_session() as session:
        results = session.query(System).filter(System.model == model).all()
        return [r.to_tuple() for r in results]


def query_all_systems(db_url: str) -> List[tuple]:
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
        else:
            system = System(
                svc_tag=svc_tag,
                name=name,
                model=model,
                idrac_version=idrac_version,
                bios_version=bios_version,
                exp_date=exp_date,
                exp_epoch=exp_epoch,
            )
            session.add(system)
        session.commit()
