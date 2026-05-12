from sqlalchemy import create_engine, inspect

from dracs import db_initialize, System
from dracs.db import get_session, upsert_system, query_by_service_tag, query_by_hostname


def test_db_initialize_creates_table(temp_db):
    db_initialize(temp_db)

    engine = create_engine(f"sqlite:///{temp_db}")
    inspector = inspect(engine)
    tables = inspector.get_table_names()

    assert "systems" in tables


def test_db_initialize_table_schema(temp_db):
    db_initialize(temp_db)

    engine = create_engine(f"sqlite:///{temp_db}")
    inspector = inspect(engine)
    columns = inspector.get_columns("systems")

    column_names = [col["name"] for col in columns]
    expected_columns = [
        "svc_tag",
        "name",
        "model",
        "idrac_version",
        "bios_version",
        "exp_date",
        "exp_epoch",
    ]

    assert column_names == expected_columns


def test_db_initialize_idempotent(temp_db):
    db_initialize(temp_db)
    db_initialize(temp_db)

    engine = create_engine(f"sqlite:///{temp_db}")
    inspector = inspect(engine)
    tables = inspector.get_table_names()

    assert "systems" in tables


def test_upsert_system_insert(temp_db):
    db_initialize(temp_db)

    upsert_system(
        temp_db,
        "ABC1234",
        "server01",
        "R660",
        "7.0.0",
        "2.1.0",
        "Jan 1, 2027",
        1735689600,
    )

    results = query_by_service_tag(temp_db, "ABC1234")
    assert len(results) == 1
    assert results[0][0] == "ABC1234"
    assert results[0][1] == "server01"
    assert results[0][2] == "R660"


def test_upsert_system_update(temp_db):
    db_initialize(temp_db)

    upsert_system(
        temp_db,
        "ABC1234",
        "server01",
        "R660",
        "7.0.0",
        "2.1.0",
        "Jan 1, 2027",
        1735689600,
    )
    upsert_system(
        temp_db,
        "ABC1234",
        "server01",
        "R760",
        "8.0.0",
        "3.0.0",
        "Jan 1, 2028",
        1767225600,
    )

    results = query_by_service_tag(temp_db, "ABC1234")
    assert len(results) == 1
    assert results[0][2] == "R760"
    assert results[0][3] == "8.0.0"


def test_query_by_hostname(temp_db):
    db_initialize(temp_db)

    upsert_system(
        temp_db,
        "ABC1234",
        "server01",
        "R660",
        "7.0.0",
        "2.1.0",
        "Jan 1, 2027",
        1735689600,
    )

    results = query_by_hostname(temp_db, "server01")
    assert len(results) == 1
    assert results[0][0] == "ABC1234"


def test_query_by_hostname_not_found(temp_db):
    db_initialize(temp_db)

    results = query_by_hostname(temp_db, "nonexistent")
    assert len(results) == 0


def test_db_initialize_with_url(temp_db):
    url = f"sqlite:///{temp_db}"
    db_initialize(url)

    engine = create_engine(url)
    inspector = inspect(engine)
    tables = inspector.get_table_names()

    assert "systems" in tables
