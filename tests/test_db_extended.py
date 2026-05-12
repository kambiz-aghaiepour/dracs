import pytest

from dracs.db import (
    db_initialize,
    get_session,
    make_db_url,
    query_all_systems,
    query_by_model,
    upsert_system,
    System,
)


class TestMakeDbUrl:
    def test_plain_path(self):
        assert make_db_url("/tmp/test.db") == "sqlite:////tmp/test.db"

    def test_already_url(self):
        url = "sqlite:////tmp/test.db"
        assert make_db_url(url) == url

    def test_postgres_url(self):
        url = "postgresql://user:pass@host/db"
        assert make_db_url(url) == url


class TestGetSessionNotInitialized:
    def test_raises_without_init(self):
        import dracs.db as db_mod

        old_factory = db_mod._SessionFactory
        db_mod._SessionFactory = None
        try:
            with pytest.raises(RuntimeError, match="Database not initialized"):
                with get_session():
                    pass
        finally:
            db_mod._SessionFactory = old_factory


class TestQueryByModel:
    def test_returns_matching(self, temp_db):
        db_initialize(temp_db)
        upsert_system(
            temp_db,
            "TAG001",
            "host1",
            "R660",
            "7.0.0",
            "2.1.0",
            "Jan 1, 2027",
            1735689600,
        )
        upsert_system(
            temp_db,
            "TAG002",
            "host2",
            "R650",
            "6.0.0",
            "1.5.0",
            "Jan 1, 2027",
            1735689600,
        )
        upsert_system(
            temp_db,
            "TAG003",
            "host3",
            "R660",
            "7.1.0",
            "2.2.0",
            "Jan 1, 2027",
            1735689600,
        )

        results = query_by_model(temp_db, "R660")
        assert len(results) == 2
        tags = {r[0] for r in results}
        assert tags == {"TAG001", "TAG003"}

    def test_returns_empty_no_match(self, temp_db):
        db_initialize(temp_db)
        upsert_system(
            temp_db,
            "TAG001",
            "host1",
            "R660",
            "7.0.0",
            "2.1.0",
            "Jan 1, 2027",
            1735689600,
        )
        results = query_by_model(temp_db, "R750")
        assert len(results) == 0


class TestQueryAllSystems:
    def test_returns_all_ordered(self, temp_db):
        db_initialize(temp_db)
        upsert_system(
            temp_db,
            "TAG001",
            "charlie",
            "R660",
            "7.0.0",
            "2.1.0",
            "Jan 1, 2027",
            1735689600,
        )
        upsert_system(
            temp_db,
            "TAG002",
            "alpha",
            "R650",
            "6.0.0",
            "1.5.0",
            "Jan 1, 2027",
            1735689600,
        )
        upsert_system(
            temp_db,
            "TAG003",
            "bravo",
            "R660",
            "7.1.0",
            "2.2.0",
            "Jan 1, 2027",
            1735689600,
        )

        results = query_all_systems(temp_db)
        assert len(results) == 3
        assert results[0][1] == "alpha"
        assert results[1][1] == "bravo"
        assert results[2][1] == "charlie"

    def test_returns_empty_when_no_systems(self, temp_db):
        db_initialize(temp_db)
        results = query_all_systems(temp_db)
        assert len(results) == 0


class TestSystemToTuple:
    def test_to_tuple(self, temp_db):
        db_initialize(temp_db)
        upsert_system(
            temp_db,
            "TAG001",
            "host1",
            "R660",
            "7.0.0",
            "2.1.0",
            "Jan 1, 2027",
            1735689600,
        )
        with get_session() as session:
            system = session.query(System).first()
            t = system.to_tuple()

        assert t == (
            "TAG001",
            "host1",
            "R660",
            "7.0.0",
            "2.1.0",
            "Jan 1, 2027",
            1735689600,
        )


class TestDbInitializeNonSqlite:
    def test_non_sqlite_url(self, temp_db):
        db_initialize(f"sqlite:///{temp_db}")
        with get_session() as session:
            count = session.query(System).count()
            assert count == 0
