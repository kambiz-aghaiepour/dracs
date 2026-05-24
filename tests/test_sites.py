import pytest
from sqlalchemy import create_engine, inspect, text

from dracs.db import (
    Site,
    UserSiteRole,
    create_site,
    db_initialize,
    delete_site,
    get_default_site_id,
    get_session,
    get_site_by_name,
    list_sites,
    rename_site,
    upsert_system,
)
from dracs.users import create_user


class TestSiteSchema:
    def test_sites_table_created(self, temp_db):
        db_initialize(temp_db)

        engine = create_engine(f"sqlite:///{temp_db}")
        inspector = inspect(engine)
        assert "sites" in inspector.get_table_names()
        engine.dispose()

    def test_user_site_roles_table_created(self, temp_db):
        db_initialize(temp_db)

        engine = create_engine(f"sqlite:///{temp_db}")
        inspector = inspect(engine)
        assert "user_site_roles" in inspector.get_table_names()
        engine.dispose()

    def test_sites_table_columns(self, temp_db):
        db_initialize(temp_db)

        engine = create_engine(f"sqlite:///{temp_db}")
        inspector = inspect(engine)
        columns = {c["name"] for c in inspector.get_columns("sites")}
        assert columns == {"id", "name", "is_primary", "created_at"}
        engine.dispose()

    def test_user_site_roles_table_columns(self, temp_db):
        db_initialize(temp_db)

        engine = create_engine(f"sqlite:///{temp_db}")
        inspector = inspect(engine)
        columns = {c["name"] for c in inspector.get_columns("user_site_roles")}
        assert columns == {"id", "user_id", "site_id", "role"}
        engine.dispose()

    def test_jobs_table_has_site_id(self, temp_db):
        db_initialize(temp_db)

        engine = create_engine(f"sqlite:///{temp_db}")
        inspector = inspect(engine)
        columns = {c["name"] for c in inspector.get_columns("jobs")}
        assert "site_id" in columns
        engine.dispose()


class TestGrandfatherSites:
    def test_default_site_created(self, temp_db):
        db_initialize(temp_db)

        site = get_site_by_name("Default")
        assert site is not None
        assert site["name"] == "Default"
        assert site["is_primary"] is True

    def test_default_site_idempotent(self, temp_db):
        db_initialize(temp_db)
        db_initialize(temp_db)

        sites = list_sites()
        primary_sites = [s for s in sites if s["is_primary"]]
        assert len(primary_sites) == 1

    def test_existing_systems_assigned_to_default(self, temp_db):
        db_initialize(temp_db)
        default_id = get_default_site_id()

        upsert_system(
            temp_db,
            "TAG001",
            "host01",
            "R660",
            "7.0.0",
            "2.1.0",
            "Jan 1, 2027",
            1893456000,
        )

        with get_session() as session:
            system = (
                session.query(__import__("dracs.db", fromlist=["System"]).System)
                .filter_by(svc_tag="TAG001")
                .first()
            )
            assert system.site_id == default_id

    def test_existing_users_get_site_roles(self, temp_db):
        db_initialize(temp_db)

        create_user("testuser", "password123", role="admin")
        db_initialize(temp_db)

        default_id = get_default_site_id()
        with get_session() as session:
            role = (
                session.query(UserSiteRole)
                .filter_by(site_id=default_id)
                .join(
                    __import__("dracs.db", fromlist=["User"]).User,
                    UserSiteRole.user_id
                    == __import__("dracs.db", fromlist=["User"]).User.id,
                )
                .filter(
                    __import__("dracs.db", fromlist=["User"]).User.username
                    == "testuser"
                )
                .first()
            )
            assert role is not None
            assert role.role == "admin"

    def test_user_site_role_not_duplicated(self, temp_db):
        db_initialize(temp_db)

        create_user("testuser", "password123", role="user")
        db_initialize(temp_db)
        db_initialize(temp_db)

        default_id = get_default_site_id()
        with get_session() as session:
            from dracs.db import User

            user = session.query(User).filter_by(username="testuser").first()
            roles = (
                session.query(UserSiteRole)
                .filter_by(user_id=user.id, site_id=default_id)
                .all()
            )
            assert len(roles) == 1

    def test_migrate_adds_site_id_to_legacy_jobs(self, temp_db):
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
        inspector = inspect(engine2)
        columns = {c["name"] for c in inspector.get_columns("jobs")}
        assert "site_id" in columns
        assert "metadata_json" in columns
        engine2.dispose()

    def test_migrate_adds_site_id_to_legacy_systems(self, temp_db):
        engine = create_engine(f"sqlite:///{temp_db}")
        with engine.begin() as conn:
            conn.execute(
                text(
                    "CREATE TABLE systems ("
                    "svc_tag TEXT PRIMARY KEY, "
                    "name TEXT, "
                    "model TEXT, "
                    "idrac_version TEXT, "
                    "bios_version TEXT, "
                    "exp_date TEXT, "
                    "exp_epoch INTEGER"
                    ")"
                )
            )
            conn.execute(
                text(
                    "INSERT INTO systems (svc_tag, name, model) "
                    "VALUES ('TAG001', 'host01', 'R660')"
                )
            )
        engine.dispose()

        db_initialize(temp_db)

        engine2 = create_engine(f"sqlite:///{temp_db}")
        inspector = inspect(engine2)
        columns = {c["name"] for c in inspector.get_columns("systems")}
        assert "site_id" in columns

        with engine2.begin() as conn:
            row = conn.execute(
                text("SELECT site_id FROM systems WHERE svc_tag = 'TAG001'")
            ).fetchone()
            assert row[0] is not None
        engine2.dispose()


class TestGetDefaultSiteId:
    def test_returns_primary_site_id(self, temp_db):
        db_initialize(temp_db)

        site_id = get_default_site_id()
        assert isinstance(site_id, int)
        assert site_id > 0


class TestGetSiteByName:
    def test_found(self, temp_db):
        db_initialize(temp_db)

        site = get_site_by_name("Default")
        assert site is not None
        assert site["name"] == "Default"
        assert site["is_primary"] is True

    def test_not_found(self, temp_db):
        db_initialize(temp_db)

        assert get_site_by_name("NonExistent") is None


class TestListSites:
    def test_default_site_listed(self, temp_db):
        db_initialize(temp_db)

        sites = list_sites()
        assert len(sites) == 1
        assert sites[0]["name"] == "Default"
        assert sites[0]["is_primary"] is True
        assert sites[0]["host_count"] == 0

    def test_includes_host_count(self, temp_db):
        db_initialize(temp_db)
        upsert_system(
            temp_db,
            "TAG001",
            "host01",
            "R660",
            "7.0.0",
            "2.1.0",
            "Jan 1, 2027",
            1893456000,
        )
        upsert_system(
            temp_db,
            "TAG002",
            "host02",
            "R660",
            "7.0.0",
            "2.1.0",
            "Jan 1, 2027",
            1893456000,
        )

        sites = list_sites()
        assert sites[0]["host_count"] == 2

    def test_multiple_sites(self, temp_db):
        db_initialize(temp_db)
        create_site("Site2")

        sites = list_sites()
        assert len(sites) == 2
        names = {s["name"] for s in sites}
        assert names == {"Default", "Site2"}


class TestCreateSite:
    def test_create(self, temp_db):
        db_initialize(temp_db)

        site = create_site("Site2")
        assert site["name"] == "Site2"
        assert site["is_primary"] is False
        assert site["id"] is not None

    def test_create_is_not_primary(self, temp_db):
        db_initialize(temp_db)

        site = create_site("NewSite")
        assert site["is_primary"] is False

    def test_duplicate_name_raises(self, temp_db):
        db_initialize(temp_db)
        create_site("Site2")

        with pytest.raises(Exception):
            create_site("Site2")


class TestDeleteSite:
    def test_delete_empty_site(self, temp_db):
        db_initialize(temp_db)
        site = create_site("Site2")

        result = delete_site(site["id"])
        assert result is True
        assert get_site_by_name("Site2") is None

    def test_delete_primary_site_raises(self, temp_db):
        db_initialize(temp_db)
        default_id = get_default_site_id()

        with pytest.raises(ValueError, match="primary"):
            delete_site(default_id)

    def test_delete_site_with_systems_raises(self, temp_db):
        db_initialize(temp_db)
        site = create_site("Site2")
        upsert_system(
            temp_db,
            "TAG001",
            "host01",
            "R660",
            "7.0.0",
            "2.1.0",
            "Jan 1, 2027",
            1893456000,
            site_id=site["id"],
        )

        with pytest.raises(ValueError, match="system"):
            delete_site(site["id"])

    def test_delete_nonexistent_returns_false(self, temp_db):
        db_initialize(temp_db)

        assert delete_site(9999) is False

    def test_delete_cleans_up_user_site_roles(self, temp_db):
        db_initialize(temp_db)
        site = create_site("Site2")
        create_user("testuser", "password123", role="user")

        with get_session() as session:
            from dracs.db import User

            user = session.query(User).filter_by(username="testuser").first()
            role_mapping = UserSiteRole(
                user_id=user.id, site_id=site["id"], role="admin"
            )
            session.add(role_mapping)
            session.commit()

        delete_site(site["id"])

        with get_session() as session:
            remaining = (
                session.query(UserSiteRole).filter_by(site_id=site["id"]).count()
            )
            assert remaining == 0


class TestRenameSite:
    def test_rename(self, temp_db):
        db_initialize(temp_db)
        site = create_site("Site2")

        result = rename_site(site["id"], "Lab3")
        assert result is True
        assert get_site_by_name("Lab3") is not None
        assert get_site_by_name("Site2") is None

    def test_rename_primary_site(self, temp_db):
        db_initialize(temp_db)
        default_id = get_default_site_id()

        result = rename_site(default_id, "Main")
        assert result is True
        assert get_site_by_name("Main") is not None
        assert get_site_by_name("Default") is None

    def test_rename_nonexistent_returns_false(self, temp_db):
        db_initialize(temp_db)

        assert rename_site(9999, "NewName") is False

    def test_rename_duplicate_raises(self, temp_db):
        db_initialize(temp_db)
        create_site("Site2")

        with pytest.raises(Exception):
            rename_site(get_default_site_id(), "Site2")


class TestUpsertSystemSiteId:
    def test_new_system_gets_default_site(self, temp_db):
        db_initialize(temp_db)
        default_id = get_default_site_id()

        upsert_system(
            temp_db,
            "TAG001",
            "host01",
            "R660",
            "7.0.0",
            "2.1.0",
            "Jan 1, 2027",
            1893456000,
        )

        with get_session() as session:
            from dracs.db import System

            system = session.query(System).filter_by(svc_tag="TAG001").first()
            assert system.site_id == default_id

    def test_new_system_with_explicit_site(self, temp_db):
        db_initialize(temp_db)
        site = create_site("Site2")

        upsert_system(
            temp_db,
            "TAG001",
            "host01",
            "R660",
            "7.0.0",
            "2.1.0",
            "Jan 1, 2027",
            1893456000,
            site_id=site["id"],
        )

        with get_session() as session:
            from dracs.db import System

            system = session.query(System).filter_by(svc_tag="TAG001").first()
            assert system.site_id == site["id"]

    def test_update_preserves_site_when_not_specified(self, temp_db):
        db_initialize(temp_db)
        site = create_site("Site2")

        upsert_system(
            temp_db,
            "TAG001",
            "host01",
            "R660",
            "7.0.0",
            "2.1.0",
            "Jan 1, 2027",
            1893456000,
            site_id=site["id"],
        )
        upsert_system(
            temp_db,
            "TAG001",
            "host01",
            "R660",
            "8.0.0",
            "3.0.0",
            "Jan 1, 2028",
            1924992000,
        )

        with get_session() as session:
            from dracs.db import System

            system = session.query(System).filter_by(svc_tag="TAG001").first()
            assert system.site_id == site["id"]

    def test_update_can_change_site(self, temp_db):
        db_initialize(temp_db)
        site2 = create_site("Site2")

        upsert_system(
            temp_db,
            "TAG001",
            "host01",
            "R660",
            "7.0.0",
            "2.1.0",
            "Jan 1, 2027",
            1893456000,
        )
        upsert_system(
            temp_db,
            "TAG001",
            "host01",
            "R660",
            "7.0.0",
            "2.1.0",
            "Jan 1, 2027",
            1893456000,
            site_id=site2["id"],
        )

        with get_session() as session:
            from dracs.db import System

            system = session.query(System).filter_by(svc_tag="TAG001").first()
            assert system.site_id == site2["id"]
