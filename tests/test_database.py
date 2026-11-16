import sqlite3

from sqlalchemy import create_engine, inspect

from dracs import db_initialize, System
from dracs.db import get_session, upsert_system, query_by_service_tag, query_by_hostname


def test_db_initialize_enables_wal(temp_db):
    db_initialize(temp_db)

    con = sqlite3.connect(temp_db)
    assert con.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    con.close()


def test_add_column_safe_duplicate_column(temp_db):
    import pytest
    from sqlalchemy.exc import OperationalError

    from dracs.db import _add_column_safe

    db_initialize(temp_db)
    engine = create_engine(f"sqlite:///{temp_db}")

    # Concurrent workers may both run the same migration; the second
    # ALTER must be tolerated ("duplicate column name").
    stmt = "ALTER TABLE systems ADD COLUMN extra_col VARCHAR"
    _add_column_safe(engine, stmt)
    _add_column_safe(engine, stmt)

    # Other ALTER errors must still propagate.
    with pytest.raises(OperationalError):
        _add_column_safe(engine, "ALTER TABLE missing_table ADD COLUMN x TEXT")


def test_db_initialize_same_url_reuses_engine(temp_db):
    from dracs import db as db_mod

    db_initialize(temp_db)
    engine = db_mod._engine
    # Job-pool threads re-run db_initialize per job; same URL must be a no-op.
    db_initialize(temp_db)
    assert db_mod._engine is engine


def test_db_initialize_blocks_on_init_lock(temp_db):
    """A thread that passes the URL check while another thread holds the
    init lock must reuse the winner's engine (check-then-act race)."""
    import os
    import threading
    import time

    from dracs import db as db_mod

    db_initialize(temp_db)
    url2 = f"{temp_db}.race.db"
    canon2 = "sqlite:///" + os.path.abspath(url2)

    db_mod._init_lock.acquire()
    errors = []

    def worker():
        try:
            db_initialize(url2)
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    thread = threading.Thread(target=worker)
    thread.start()
    time.sleep(0.2)  # worker passed the URL check and is blocked on the lock

    # Another thread finished initializing url2 while we held the lock.
    fake_engine = db_mod.create_engine(canon2)
    db_mod._engine = fake_engine
    db_mod._db_url = canon2
    db_mod._init_lock.release()

    thread.join(timeout=10)
    assert not thread.is_alive()
    assert not errors
    # The reuse branch must leave the winner's engine in place; a second
    # engine creation here would mean the re-check is broken.
    assert db_mod._engine is fake_engine
    if os.path.exists(url2):
        os.unlink(url2)


def test_db_initialize_recovers_after_partial_failure(temp_db):
    """A same-URL retry after a failed init attempt (no keepalive) re-inits."""
    from dracs import db as db_mod

    db_initialize(temp_db)
    # Simulate an init that died between engine creation and keepalive.
    db_mod._keepalive = None

    db_initialize(temp_db)

    assert db_mod._keepalive is not None
    assert db_mod._keepalive[0] is db_mod._engine


def test_keepalive_pins_wal_files(temp_db):
    """The pinned keepalive connection keeps -wal/-shm after writes."""
    import os

    from sqlalchemy import text

    from dracs.db import get_session

    db_initialize(temp_db)
    with get_session() as session:
        session.execute(
            text(
                "INSERT INTO sites (name, is_primary, created_at) "
                "VALUES (:name, 1, :ts)"
            ),
            {"name": "pin", "ts": "now"},
        )
        session.commit()

    assert os.path.exists(temp_db + "-wal")
    assert os.path.exists(temp_db + "-shm")


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
        "start_date",
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


def test_migrate_host_config_to_eav(temp_db):
    from sqlalchemy import text

    # Create a pre-EAV host_config table (with idrac_hostname_value so it
    # survives _migrate_schema's rebuild check) and verify it gets fully
    # migrated to host_config_attr and dropped.
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
                "ps_rapid_on TEXT, "
                "dns_from_dhcp TEXT, "
                "ipmi_lan_enable TEXT, "
                "host_header_check TEXT, "
                "sys_profile TEXT, "
                "idrac_hostname INTEGER, "
                "idrac_hostname_value VARCHAR, "
                "ssl_self_signed INTEGER, "
                "ssl_valid_name INTEGER, "
                "ssl_expiry VARCHAR, "
                "ssl_fingerprint VARCHAR, "
                "collected_at VARCHAR"
                ")"
            )
        )
        conn.execute(
            text(
                "INSERT INTO host_config "
                "(hostname, site_id, ps_rapid_on, collected_at) "
                "VALUES ('server01.example.com', 1, 'Disabled', '2026-01-01T00:00:00')"
            )
        )
    engine.dispose()

    db_initialize(temp_db)

    engine2 = create_engine(f"sqlite:///{temp_db}")
    insp = inspect(engine2)
    table_names = insp.get_table_names()
    # Old table must be gone
    assert "host_config" not in table_names
    # EAV table must exist
    assert "host_config_attr" in table_names
    engine2.dispose()
