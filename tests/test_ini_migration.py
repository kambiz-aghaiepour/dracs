import configparser
import os
import tempfile
from pathlib import Path

import pytest

from dracs.sites import (
    _is_old_format,
    get_site_ini_config,
    migrate_passwords_ini,
    remove_site_ini_sections,
    rename_site_ini_sections,
    set_site_ini_config,
)
from dracs.validation import validate_site_name
from dracs.webapp import get_idrac_credentials


class TestValidateSiteName:
    def test_valid_alphanumeric(self):
        assert validate_site_name("Site2") is True

    def test_valid_all_caps(self):
        assert validate_site_name("MAIN") is True

    def test_valid_numbers_only(self):
        assert validate_site_name("123") is True

    def test_invalid_hyphen(self):
        assert validate_site_name("my-site") is False

    def test_valid_underscore(self):
        assert validate_site_name("my_site") is True

    def test_invalid_space(self):
        assert validate_site_name("my site") is False

    def test_invalid_empty(self):
        assert validate_site_name("") is False

    def test_invalid_none(self):
        assert validate_site_name(None) is False

    def test_too_long(self):
        assert validate_site_name("A" * 33) is False

    def test_max_length(self):
        assert validate_site_name("A" * 32) is True


class TestIsOldFormat:
    def test_bare_default_section(self):
        config = configparser.RawConfigParser()
        config.read_string("[DEFAULT]\nusername = root\n")
        assert _is_old_format(config) is True

    def test_bare_hostname_section(self):
        config = configparser.RawConfigParser()
        config.read_string("[host01.example.com]\nusername = admin\n")
        assert _is_old_format(config) is True

    def test_new_format(self):
        config = configparser.RawConfigParser()
        config.read_string(
            "[Default-DEFAULTS]\nusername = root\n\n"
            "[Default-host01]\nusername = admin\n"
        )
        assert _is_old_format(config) is False

    def test_empty_config(self):
        config = configparser.RawConfigParser()
        assert _is_old_format(config) is False


class TestMigratePasswordsIni:
    def test_migrate_old_format(self, tmp_path):
        ini = tmp_path / "drac-passwords.ini"
        ini.write_text(
            "[DEFAULT]\n"
            "username = root\n"
            "password = calvin\n"
            "vnc_port = 5901\n\n"
            "[host01.example.com]\n"
            "username = admin\n"
            "password = admin\n"
        )

        result = migrate_passwords_ini(ini)
        assert result is True

        backup = tmp_path / "drac-passwords.ini.bak"
        assert backup.exists()

        config = configparser.RawConfigParser()
        config.read(ini)
        assert "Default-DEFAULTS" in config.sections()
        assert "Default-host01.example.com" in config.sections()
        assert config.get("Default-DEFAULTS", "username") == "root"
        assert config.get("Default-host01.example.com", "username") == "admin"

    def test_migrate_creates_backup(self, tmp_path):
        ini = tmp_path / "drac-passwords.ini"
        ini.write_text("[DEFAULT]\nusername = root\n")

        migrate_passwords_ini(ini)

        backup = tmp_path / "drac-passwords.ini.bak"
        assert backup.exists()
        assert "username = root" in backup.read_text()

    def test_idempotent_skip_new_format(self, tmp_path):
        ini = tmp_path / "drac-passwords.ini"
        ini.write_text("[Default-DEFAULTS]\nusername = root\n")

        result = migrate_passwords_ini(ini)
        assert result is False

    def test_no_file_returns_false(self):
        result = migrate_passwords_ini(None)
        assert result is False

    def test_host_only_no_default(self, tmp_path):
        ini = tmp_path / "drac-passwords.ini"
        ini.write_text("[host01]\nusername = admin\npassword = secret\n")

        result = migrate_passwords_ini(ini)
        assert result is True

        config = configparser.RawConfigParser()
        config.read(ini)
        assert "Default-host01" in config.sections()
        assert config.get("Default-host01", "username") == "admin"


class TestMigrateWithMixedFormat:
    def test_migrate_preserves_already_prefixed_sections(self, tmp_path):
        ini = tmp_path / "drac-passwords.ini"
        ini.write_text(
            "[DEFAULT]\nusername = root\n\n"
            "[Site2-DEFAULTS]\nusername = s2user\n\n"
            "[host01]\nusername = admin\n"
        )

        result = migrate_passwords_ini(ini)
        assert result is True

        config = configparser.RawConfigParser()
        config.read(ini)
        assert "Site2-DEFAULTS" in config.sections()
        assert "Default-host01" in config.sections()

    def test_colon_delimited_values(self, tmp_path):
        ini = tmp_path / "drac-passwords.ini"
        ini.write_text("[host01]\nusername: colonuser\npassword: colonpass\n")

        result = migrate_passwords_ini(ini)
        assert result is True

        config = configparser.RawConfigParser()
        config.read(ini)
        assert "Default-host01" in config.sections()
        assert config.get("Default-host01", "username") == "colonuser"


class TestRemoveSiteIniSections:
    def test_remove_sections(self, tmp_path, monkeypatch):
        ini = tmp_path / "drac-passwords.ini"
        ini.write_text(
            "[Default-DEFAULTS]\nusername = root\n\n"
            "[Site2-DEFAULTS]\nusername = s2\n\n"
            "[Site2-host01]\npassword = h1pass\n"
        )
        monkeypatch.chdir(tmp_path)

        result = remove_site_ini_sections("Site2")
        assert result is True

        config = configparser.RawConfigParser()
        config.read(ini)
        assert "Site2-DEFAULTS" not in config.sections()
        assert "Site2-host01" not in config.sections()
        assert "Default-DEFAULTS" in config.sections()

    def test_remove_creates_backup(self, tmp_path, monkeypatch):
        ini = tmp_path / "drac-passwords.ini"
        ini.write_text("[Site2-DEFAULTS]\nusername = s2\n")
        monkeypatch.chdir(tmp_path)

        remove_site_ini_sections("Site2")
        assert (tmp_path / "drac-passwords.ini.bak").exists()

    def test_remove_nonexistent_site(self, tmp_path, monkeypatch):
        ini = tmp_path / "drac-passwords.ini"
        ini.write_text("[Default-DEFAULTS]\nusername = root\n")
        monkeypatch.chdir(tmp_path)

        result = remove_site_ini_sections("NoSuch")
        assert result is False

    def test_remove_no_ini_file(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = remove_site_ini_sections("Site2")
        assert result is False


class TestCredentialSiteResolution:
    def test_idrac_resolves_site_from_host(self, tmp_path, monkeypatch):
        from dracs.db import create_site, db_initialize, upsert_system

        fd, db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        db_initialize(db_path)
        site2 = create_site("Site2")
        upsert_system(
            db_path,
            "TAG001",
            "host01",
            "R660",
            "7.0.0",
            "2.1.0",
            "Jan 2027",
            1893456000,
            site_id=site2["id"],
        )

        ini = tmp_path / "drac-passwords.ini"
        ini.write_text(
            "[Default-DEFAULTS]\nusername = root\npassword = calvin\n\n"
            "[Site2-DEFAULTS]\nusername = site2user\npassword = site2pass\n"
        )
        monkeypatch.chdir(tmp_path)

        user, pwd = get_idrac_credentials("host01")
        assert user == "site2user"
        assert pwd == "site2pass"
        os.unlink(db_path)

    def test_vnc_resolves_site_from_host(self, tmp_path, monkeypatch):
        from dracs.db import create_site, db_initialize, upsert_system
        from dracs.vnc import get_vnc_credentials

        fd, db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        db_initialize(db_path)
        site2 = create_site("Site2")
        upsert_system(
            db_path,
            "TAG001",
            "host01",
            "R660",
            "7.0.0",
            "2.1.0",
            "Jan 2027",
            1893456000,
            site_id=site2["id"],
        )

        ini = tmp_path / "drac-passwords.ini"
        ini.write_text(
            "[Default-DEFAULTS]\nvnc_port = 5901\nvnc_password = defpass\n\n"
            "[Site2-DEFAULTS]\nvnc_port = 5910\nvnc_password = s2pass\n"
        )
        monkeypatch.chdir(tmp_path)

        port, pwd = get_vnc_credentials("host01")
        assert port == 5910
        assert pwd == "s2pass"
        os.unlink(db_path)

    def test_idrac_falls_back_to_primary_site_name(self, tmp_path, monkeypatch):
        from dracs.db import db_initialize, rename_site, get_default_site_id

        fd, db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        db_initialize(db_path)
        rename_site(get_default_site_id(), "MainSite")

        ini = tmp_path / "drac-passwords.ini"
        ini.write_text(
            "[MainSite-DEFAULTS]\nusername = mainuser\npassword = mainpass\n"
        )
        monkeypatch.chdir(tmp_path)

        user, pwd = get_idrac_credentials("unknown_host")
        assert user == "mainuser"
        assert pwd == "mainpass"
        os.unlink(db_path)

    def test_vnc_resolves_primary_for_host_without_site_id(self, tmp_path, monkeypatch):
        from dracs.db import (
            db_initialize,
            get_session,
            System,
            rename_site,
            get_default_site_id,
        )
        from dracs.vnc import get_vnc_credentials
        from sqlalchemy import text

        fd, db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        db_initialize(db_path)
        rename_site(get_default_site_id(), "MySite")

        with get_session() as sess:
            sess.execute(
                text(
                    "INSERT INTO systems (svc_tag, name, model) "
                    "VALUES ('NOTAG', 'orphan_host', 'R660')"
                )
            )
            sess.commit()

        ini = tmp_path / "drac-passwords.ini"
        ini.write_text("[MySite-DEFAULTS]\nvnc_port = 5555\nvnc_password = mypass\n")
        monkeypatch.chdir(tmp_path)

        port, pwd = get_vnc_credentials("orphan_host")
        assert port == 5555
        assert pwd == "mypass"
        os.unlink(db_path)

    def test_vnc_falls_back_to_primary_for_default_host(self, tmp_path, monkeypatch):
        from dracs.db import db_initialize, upsert_system
        from dracs.vnc import get_vnc_credentials

        fd, db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        db_initialize(db_path)
        upsert_system(
            db_path,
            "TAG001",
            "defhost",
            "R660",
            "7.0.0",
            "2.1.0",
            "Jan 2027",
            1893456000,
        )

        ini = tmp_path / "drac-passwords.ini"
        ini.write_text("[Default-DEFAULTS]\nvnc_port = 5999\nvnc_password = defvnc\n")
        monkeypatch.chdir(tmp_path)

        port, pwd = get_vnc_credentials("defhost")
        assert port == 5999
        assert pwd == "defvnc"
        os.unlink(db_path)

    def test_primary_site_name(self, temp_db):
        from dracs.db import (
            db_initialize,
            get_primary_site_name,
            get_default_site_id,
            rename_site,
        )

        db_initialize(temp_db)
        assert get_primary_site_name() == "Default"
        rename_site(get_default_site_id(), "RDU2")
        assert get_primary_site_name() == "RDU2"


class TestGetIdracCredentialsSiteAware:
    def test_default_site_implicit(self, tmp_path, monkeypatch):
        ini = tmp_path / "drac-passwords.ini"
        ini.write_text("[Default-DEFAULTS]\nusername = root\npassword = calvin\n")
        monkeypatch.chdir(tmp_path)

        user, pwd = get_idrac_credentials("host01")
        assert user == "root"
        assert pwd == "calvin"

    def test_explicit_site(self, tmp_path, monkeypatch):
        ini = tmp_path / "drac-passwords.ini"
        ini.write_text(
            "[Default-DEFAULTS]\nusername = root\npassword = calvin\n\n"
            "[Site2-DEFAULTS]\nusername = s2user\npassword = s2pass\n"
        )
        monkeypatch.chdir(tmp_path)

        user, pwd = get_idrac_credentials("host01", site="Site2")
        assert user == "s2user"
        assert pwd == "s2pass"

    def test_host_override_in_site(self, tmp_path, monkeypatch):
        ini = tmp_path / "drac-passwords.ini"
        ini.write_text(
            "[Site2-DEFAULTS]\nusername = s2user\npassword = s2pass\n\n"
            "[Site2-host01]\nusername = override\n"
        )
        monkeypatch.chdir(tmp_path)

        user, pwd = get_idrac_credentials("host01", site="Site2")
        assert user == "override"
        assert pwd == "s2pass"

    def test_unknown_site_returns_hardcoded_defaults(self, tmp_path, monkeypatch):
        ini = tmp_path / "drac-passwords.ini"
        ini.write_text("[Default-DEFAULTS]\nusername = root\npassword = calvin\n")
        monkeypatch.chdir(tmp_path)

        user, pwd = get_idrac_credentials("host01", site="NoSuchSite")
        assert user == "root"
        assert pwd == "calvin"


class TestRenameSiteIniSections:
    def test_rename_sections(self, tmp_path, monkeypatch):
        ini = tmp_path / "drac-passwords.ini"
        ini.write_text(
            "[Default-DEFAULTS]\nusername = root\n\n"
            "[Site2-DEFAULTS]\nusername = s2\n\n"
            "[Site2-host01]\npassword = h1pass\n"
        )
        monkeypatch.chdir(tmp_path)

        result = rename_site_ini_sections("Site2", "Lab3")
        assert result is True

        config = configparser.RawConfigParser()
        config.read(ini)
        assert "Lab3-DEFAULTS" in config.sections()
        assert "Lab3-host01" in config.sections()
        assert "Site2-DEFAULTS" not in config.sections()
        assert config.get("Lab3-DEFAULTS", "username") == "s2"

    def test_rename_creates_backup(self, tmp_path, monkeypatch):
        ini = tmp_path / "drac-passwords.ini"
        ini.write_text("[Site2-DEFAULTS]\nusername = s2\n")
        monkeypatch.chdir(tmp_path)

        rename_site_ini_sections("Site2", "Lab3")

        backup = tmp_path / "drac-passwords.ini.bak"
        assert backup.exists()

    def test_rename_preserves_other_sites(self, tmp_path, monkeypatch):
        ini = tmp_path / "drac-passwords.ini"
        ini.write_text(
            "[Default-DEFAULTS]\nusername = root\n\n"
            "[Site2-DEFAULTS]\nusername = s2\n"
        )
        monkeypatch.chdir(tmp_path)

        rename_site_ini_sections("Site2", "Lab3")

        config = configparser.RawConfigParser()
        config.read(ini)
        assert "Default-DEFAULTS" in config.sections()
        assert config.get("Default-DEFAULTS", "username") == "root"

    def test_rename_nonexistent_site(self, tmp_path, monkeypatch):
        ini = tmp_path / "drac-passwords.ini"
        ini.write_text("[Default-DEFAULTS]\nusername = root\n")
        monkeypatch.chdir(tmp_path)

        result = rename_site_ini_sections("NoSuchSite", "NewName")
        assert result is False

    def test_rename_no_ini_file(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = rename_site_ini_sections("Site2", "Lab3")
        assert result is False


class TestGetSiteIniConfig:
    def test_get_existing_site(self, tmp_path, monkeypatch):
        ini = tmp_path / "drac-passwords.ini"
        ini.write_text(
            "[Default-DEFAULTS]\nusername = root\npassword = calvin\n\n"
            "[Default-host01]\nusername = admin\n"
        )
        monkeypatch.chdir(tmp_path)

        result = get_site_ini_config("Default")
        assert result["defaults"]["username"] == "root"
        assert "host01" in result["hosts"]
        assert result["hosts"]["host01"]["username"] == "admin"

    def test_get_nonexistent_site(self, tmp_path, monkeypatch):
        ini = tmp_path / "drac-passwords.ini"
        ini.write_text("[Default-DEFAULTS]\nusername = root\n")
        monkeypatch.chdir(tmp_path)

        result = get_site_ini_config("NoSuchSite")
        assert result == {"defaults": {}, "hosts": {}}

    def test_get_no_ini_file(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = get_site_ini_config("Default")
        assert result == {"defaults": {}, "hosts": {}}


class TestSetSiteIniConfig:
    def test_write_new_site_config(self, tmp_path, monkeypatch):
        ini = tmp_path / "drac-passwords.ini"
        ini.write_text("[Default-DEFAULTS]\nusername = root\n")
        monkeypatch.chdir(tmp_path)

        set_site_ini_config(
            "Site2",
            {
                "defaults": {"username": "s2user", "password": "s2pass"},
                "hosts": {"host01": {"username": "h1user"}},
            },
        )

        config = configparser.RawConfigParser()
        config.read(ini)
        assert config.get("Site2-DEFAULTS", "username") == "s2user"
        assert config.get("Site2-host01", "username") == "h1user"
        assert config.get("Default-DEFAULTS", "username") == "root"

    def test_overwrite_existing_site_config(self, tmp_path, monkeypatch):
        ini = tmp_path / "drac-passwords.ini"
        ini.write_text(
            "[Site2-DEFAULTS]\nusername = old\n\n"
            "[Site2-host01]\nusername = oldhost\n"
        )
        monkeypatch.chdir(tmp_path)

        set_site_ini_config(
            "Site2",
            {
                "defaults": {"username": "new"},
                "hosts": {},
            },
        )

        config = configparser.RawConfigParser()
        config.read(ini)
        assert config.get("Site2-DEFAULTS", "username") == "new"
        assert "Site2-host01" not in config.sections()

    def test_creates_file_if_missing(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)

        set_site_ini_config(
            "Default",
            {
                "defaults": {"username": "root", "password": "calvin"},
            },
        )

        ini = tmp_path / "drac-passwords.ini"
        assert ini.exists()

    def test_creates_backup(self, tmp_path, monkeypatch):
        ini = tmp_path / "drac-passwords.ini"
        ini.write_text("[Default-DEFAULTS]\nusername = root\n")
        monkeypatch.chdir(tmp_path)

        set_site_ini_config("Site2", {"defaults": {"username": "s2"}})

        backup = tmp_path / "drac-passwords.ini.bak"
        assert backup.exists()


class TestScheduleIniSiteField:
    def test_parse_with_site(self, tmp_path):
        from dracs.jobqueue import parse_schedule_config

        ini = tmp_path / "schedule.ini"
        ini.write_text(
            "[tsr-site2]\n"
            "type = tsr\n"
            "schedule = daily\n"
            "time = 02:00\n"
            "target = all\n"
            "site = Site2\n"
        )

        tasks = parse_schedule_config(str(ini))
        assert len(tasks) == 1
        assert tasks[0]["site"] == "Site2"

    def test_parse_without_site(self, tmp_path):
        from dracs.jobqueue import parse_schedule_config

        ini = tmp_path / "schedule.ini"
        ini.write_text(
            "[refresh-daily]\n"
            "type = refresh\n"
            "schedule = daily\n"
            "time = 04:00\n"
            "target = all\n"
        )

        tasks = parse_schedule_config(str(ini))
        assert len(tasks) == 1
        assert tasks[0]["site"] is None
