from contextlib import contextmanager
from typing import List

from sqlalchemy import create_engine, String, Integer
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


def make_db_url(path: str) -> str:
    if "://" in path:
        return path
    return f"sqlite:///{path}"


def db_initialize(db_url: str) -> None:
    global _engine, _SessionFactory
    url = make_db_url(db_url)
    _engine = create_engine(url)
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
