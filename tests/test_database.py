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
        "site_id",
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

    results = query_by_service_tag("ABC1234")
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

    results = query_by_service_tag("ABC1234")
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

    results = query_by_hostname("server01")
    assert len(results) == 1
    assert results[0][0] == "ABC1234"


def test_query_by_hostname_not_found(temp_db):
    db_initialize(temp_db)

    results = query_by_hostname("nonexistent")
    assert len(results) == 0


def test_db_initialize_with_url(temp_db):
    url = f"sqlite:///{temp_db}"
    db_initialize(url)

    engine = create_engine(url)
    inspector = inspect(engine)
    tables = inspector.get_table_names()

    assert "systems" in tables


def test_migrate_adds_metadata_json_column(temp_db):
    from sqlalchemy import text

    engine = create_engine(f"sqlite:///{temp_db}")
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE jobs ("
                "id INTEGER PRIMARY KEY, "
                "parent_id INTEGER, "
                "job_type TEXT NOT NULL, "
                "target TEXT NOT NULL, "
                "status TEXT NOT NULL DEFAULT 'pending', "
                "created_at TEXT NOT NULL, "
                "started_at TEXT, "
                "completed_at TEXT, "
                "result TEXT, "
                "error TEXT, "
                "worker_id TEXT"
                ")"
            )
        )
    engine.dispose()

    db_initialize(temp_db)

    engine2 = create_engine(f"sqlite:///{temp_db}")
    insp = inspect(engine2)
    columns = {c["name"] for c in insp.get_columns("jobs")}
    assert "metadata_json" in columns
    engine2.dispose()


def test_migrate_noop_when_column_exists(temp_db):
    db_initialize(temp_db)
    db_initialize(temp_db)

    engine = create_engine(f"sqlite:///{temp_db}")
    insp = inspect(engine)
    columns = {c["name"] for c in insp.get_columns("jobs")}
    assert "metadata_json" in columns
    engine.dispose()


def test_migrate_adds_ssl_fingerprint_to_host_config(temp_db):
    from sqlalchemy import text

    # Create host_config without ssl_fingerprint, but with idrac_hostname_value
    # so the rebuild path is NOT triggered — the ALTER TABLE path runs instead.
    engine = create_engine(f"sqlite:///{temp_db}")
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE sites ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "name VARCHAR NOT NULL UNIQUE, "
                "is_primary BOOLEAN NOT NULL DEFAULT 0, "
                "created_at VARCHAR NOT NULL"
                ")"
            )
        )
        conn.execute(
            text(
                "INSERT INTO sites (name, is_primary, created_at) "
                "VALUES ('Default', 1, '2026-01-01')"
            )
        )
        conn.execute(
            text(
                "CREATE TABLE host_config ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "hostname VARCHAR NOT NULL, "
                "site_id INTEGER NOT NULL, "
                "idrac_hostname INTEGER, "
                "idrac_hostname_value VARCHAR, "
                "ssl_self_signed INTEGER, "
                "ssl_valid_name INTEGER, "
                "ssl_expiry VARCHAR, "
                "collected_at VARCHAR"
                ")"
            )
        )
    engine.dispose()

    db_initialize(temp_db)

    engine2 = create_engine(f"sqlite:///{temp_db}")
    insp = inspect(engine2)
    columns = {c["name"] for c in insp.get_columns("host_config")}
    assert "ssl_fingerprint" in columns
    engine2.dispose()
