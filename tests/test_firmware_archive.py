import gzip
import hashlib
import json
import os
import tempfile
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from dracs.db import db_initialize, upsert_system
from dracs.webapp import (
    _find_latest_bios,
    _find_latest_idrac_firmware,
)

CATALOG_WITH_HASH = """<?xml version="1.0" encoding="utf-16"?>
<Manifest>
  <SoftwareComponent path="FOLDER/firmware.EXE" vendorVersion="7.30.10.50"
      dateTime="2025-03-15T10:00:00Z" hash="abcdef1234567890" hashAlgorithm="SHA256">
    <ComponentType value="FRMW"/>
    <Category><Display>iDRAC with Lifecycle Controller</Display></Category>
    <SupportedSystems><Brand><Model><Display>R660</Display></Model></Brand></SupportedSystems>
  </SoftwareComponent>
  <SoftwareComponent path="FOLDER/bios.EXE" vendorVersion="2.10.1"
      dateTime="2025-03-10T10:00:00Z" hash="fedcba0987654321" hashAlgorithm="SHA256">
    <ComponentType value="BIOS"/>
    <SupportedSystems><Brand><Model><Display>R660</Display></Model></Brand></SupportedSystems>
  </SoftwareComponent>
</Manifest>"""


class TestFinderReturnsSha256:
    def test_firmware_includes_hash(self):
        xml_bytes = CATALOG_WITH_HASH.encode("utf-16")
        result = _find_latest_idrac_firmware(xml_bytes, "R660")
        assert result is not None
        assert result["hash_sha256"] == "abcdef1234567890"

    def test_bios_includes_hash(self):
        xml_bytes = CATALOG_WITH_HASH.encode("utf-16")
        result = _find_latest_bios(xml_bytes, "R660")
        assert result is not None
        assert result["hash_sha256"] == "fedcba0987654321"

    def test_firmware_missing_hash(self):
        xml = """<?xml version="1.0" encoding="utf-16"?>
        <Manifest>
          <SoftwareComponent path="FOLDER/fw.EXE" vendorVersion="1.0"
              dateTime="2025-01-01T10:00:00Z">
            <ComponentType value="FRMW"/>
            <Category><Display>iDRAC with Lifecycle Controller</Display></Category>
            <SupportedSystems><Brand><Model><Display>R660</Display></Model></Brand></SupportedSystems>
          </SoftwareComponent>
        </Manifest>"""
        result = _find_latest_idrac_firmware(xml.encode("utf-16"), "R660")
        assert result is not None
        assert result["hash_sha256"] == ""


@pytest.fixture
def webapp_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db_initialize(path)
    upsert_system(
        path,
        "TAG001",
        "server01",
        "R660",
        "7.0.0",
        "2.1.0",
        "Jan 1, 2027",
        1893456000,
    )
    yield path
    if os.path.exists(path):
        os.unlink(path)


@pytest.fixture
def client(webapp_db):
    with patch.dict(os.environ, {"DRACS_DB": webapp_db}):
        with patch.dict(
            os.environ,
            {"DRACS_DNS_STRING": "mgmt-", "DRACS_DNS_MODE": "prefix"},
        ):
            import dracs.webapp as webapp_mod

            webapp_mod.DB_PATH = webapp_db
            webapp_mod.db_initialize(webapp_db)
            webapp_mod.app.config["TESTING"] = True
            with webapp_mod.app.test_client() as c:
                yield c


def _login(client):
    client.post(
        "/login",
        data=json.dumps({"username": "admin", "password": "admin"}),
        content_type="application/json",
    )


def _make_firmware_exe(tmp_path):
    pkg_xml = '<SoftwareComponent vendorVersion="7.30.10.50"></SoftwareComponent>'
    exe_path = tmp_path / "firmware.EXE"
    with zipfile.ZipFile(exe_path, "w") as zf:
        zf.writestr("package.xml", pkg_xml)
        zf.writestr("payload/firmimgFIT.d9", b"firmware data")
    return exe_path


class TestFirmwareSha256AndArchive:
    def test_sha256_verified_and_archived(self, client, tmp_path):
        _login(client)

        src_exe = _make_firmware_exe(tmp_path)
        exe_bytes = src_exe.read_bytes()
        expected_hash = hashlib.sha256(exe_bytes).hexdigest()

        catalog_xml = f"""<?xml version="1.0" encoding="utf-16"?>
        <Manifest>
          <SoftwareComponent path="FOLDER/firmware.EXE" vendorVersion="7.30.10.50"
              dateTime="2025-03-15T10:00:00Z" hash="{expected_hash}">
            <ComponentType value="FRMW"/>
            <Category><Display>iDRAC with Lifecycle Controller</Display></Category>
            <SupportedSystems><Brand><Model><Display>R660</Display></Model></Brand></SupportedSystems>
          </SoftwareComponent>
        </Manifest>"""
        catalog_gz = gzip.compress(catalog_xml.encode("utf-16"))

        mock_catalog_resp = MagicMock()
        mock_catalog_resp.read.return_value = catalog_gz
        mock_catalog_resp.__enter__ = lambda s: s
        mock_catalog_resp.__exit__ = MagicMock(return_value=False)

        mock_exe_resp = MagicMock()
        mock_exe_resp.__enter__ = lambda s: s
        mock_exe_resp.__exit__ = MagicMock(return_value=False)

        def fake_urlopen(req, timeout=None):
            if "Catalog" in req.full_url:
                return mock_catalog_resp
            return mock_exe_resp

        fw_dir = tmp_path / "fw_dest"
        fw_dir.mkdir()
        fw_archive = tmp_path / "fw_archive"
        fw_archive.mkdir()

        import xml.etree.ElementTree as real_ET

        orig_parse = real_ET.parse

        def fake_et_parse(path):
            if "package.xml" in str(path):
                root = real_ET.fromstring(
                    '<SoftwareComponent vendorVersion="7.30.10.50"/>'
                )
                tree = MagicMock()
                tree.getroot.return_value = root
                return tree
            return orig_parse(path)

        def fake_copyfileobj(src, dst):
            dst.write(exe_bytes)

        with (
            patch("dracs.webapp.urllib.request.urlopen", side_effect=fake_urlopen),
            patch("dracs.webapp.shutil.copyfileobj", side_effect=fake_copyfileobj),
            patch("dracs.webapp.defused_ET.parse", side_effect=fake_et_parse),
            patch("dracs.webapp.FIRMWARE_IMAGE_DIR", fw_dir),
            patch("dracs.webapp.FIRMWARE_ARCHIVE_DIR", fw_archive),
        ):
            resp = client.post(
                "/api/latest-firmware",
                data=json.dumps(
                    {"model": "R660", "hostname": "server01", "current_version": "7.00"}
                ),
                content_type="application/json",
            )
            data_str = resp.get_data(as_text=True)

        assert "Verifying SHA256" in data_str
        assert "FAIL" not in data_str
        assert (fw_archive / "firmware.EXE").exists()
        assert (fw_archive / "firmware.EXE.sha256").exists()
        sha_content = (fw_archive / "firmware.EXE.sha256").read_text()
        assert expected_hash in sha_content

    def test_sha256_mismatch_stops(self, client, tmp_path):
        _login(client)

        src_exe = _make_firmware_exe(tmp_path)
        exe_bytes = src_exe.read_bytes()

        catalog_xml = """<?xml version="1.0" encoding="utf-16"?>
        <Manifest>
          <SoftwareComponent path="FOLDER/firmware.EXE" vendorVersion="7.30.10.50"
              dateTime="2025-03-15T10:00:00Z" hash="0000bad_hash_value">
            <ComponentType value="FRMW"/>
            <Category><Display>iDRAC with Lifecycle Controller</Display></Category>
            <SupportedSystems><Brand><Model><Display>R660</Display></Model></Brand></SupportedSystems>
          </SoftwareComponent>
        </Manifest>"""
        catalog_gz = gzip.compress(catalog_xml.encode("utf-16"))

        mock_catalog_resp = MagicMock()
        mock_catalog_resp.read.return_value = catalog_gz
        mock_catalog_resp.__enter__ = lambda s: s
        mock_catalog_resp.__exit__ = MagicMock(return_value=False)

        mock_exe_resp = MagicMock()
        mock_exe_resp.__enter__ = lambda s: s
        mock_exe_resp.__exit__ = MagicMock(return_value=False)

        def fake_urlopen(req, timeout=None):
            if "Catalog" in req.full_url:
                return mock_catalog_resp
            return mock_exe_resp

        fw_archive = tmp_path / "fw_archive"
        fw_archive.mkdir()

        def fake_copyfileobj(src, dst):
            dst.write(exe_bytes)

        with (
            patch("dracs.webapp.urllib.request.urlopen", side_effect=fake_urlopen),
            patch("dracs.webapp.shutil.copyfileobj", side_effect=fake_copyfileobj),
            patch("dracs.webapp.FIRMWARE_ARCHIVE_DIR", fw_archive),
        ):
            resp = client.post(
                "/api/latest-firmware",
                data=json.dumps(
                    {"model": "R660", "hostname": "server01", "current_version": "7.00"}
                ),
                content_type="application/json",
            )
            data_str = resp.get_data(as_text=True)

        assert "FAIL" in data_str
        assert '"type": "complete"' not in data_str


class TestBiosSha256AndArchive:
    def test_sha256_verified_and_hardlinked(self, client, tmp_path):
        _login(client)

        exe_content = b"fake bios content"
        expected_hash = hashlib.sha256(exe_content).hexdigest()

        catalog_xml = f"""<?xml version="1.0" encoding="utf-16"?>
        <Manifest>
          <SoftwareComponent path="FOLDER/bios.EXE" vendorVersion="2.10.1"
              dateTime="2025-03-10T10:00:00Z" hash="{expected_hash}">
            <ComponentType value="BIOS"/>
            <SupportedSystems><Brand><Model><Display>R660</Display></Model></Brand></SupportedSystems>
          </SoftwareComponent>
        </Manifest>"""
        catalog_gz = gzip.compress(catalog_xml.encode("utf-16"))

        mock_catalog_resp = MagicMock()
        mock_catalog_resp.read.return_value = catalog_gz
        mock_catalog_resp.__enter__ = lambda s: s
        mock_catalog_resp.__exit__ = MagicMock(return_value=False)

        mock_exe_resp = MagicMock()
        mock_exe_resp.__enter__ = lambda s: s
        mock_exe_resp.__exit__ = MagicMock(return_value=False)

        def fake_urlopen(req, timeout=None):
            if "Catalog" in req.full_url:
                return mock_catalog_resp
            return mock_exe_resp

        bios_dir = tmp_path / "bios_dest"
        bios_dir.mkdir()
        bios_archive = tmp_path / "bios_archive"
        bios_archive.mkdir()

        def fake_copyfileobj(src, dst):
            dst.write(exe_content)

        with (
            patch("dracs.webapp.urllib.request.urlopen", side_effect=fake_urlopen),
            patch("dracs.webapp.shutil.copyfileobj", side_effect=fake_copyfileobj),
            patch("dracs.webapp.BIOS_IMAGE_DIR", bios_dir),
            patch("dracs.webapp.BIOS_ARCHIVE_DIR", bios_archive),
        ):
            resp = client.post(
                "/api/latest-bios",
                data=json.dumps(
                    {
                        "model": "R660",
                        "hostname": "server01",
                        "current_version": "2.0.0",
                    }
                ),
                content_type="application/json",
            )
            data_str = resp.get_data(as_text=True)

        assert "Verifying SHA256" in data_str
        assert "FAIL" not in data_str
        assert (bios_archive / "bios.EXE").exists()
        assert (bios_archive / "bios.EXE.sha256").exists()

        web_path = bios_dir / "R660" / "bios.EXE"
        assert web_path.exists()
        assert os.stat(bios_archive / "bios.EXE").st_ino == os.stat(web_path).st_ino

    def test_hardlink_fallback_to_copy(self, client, tmp_path):
        _login(client)

        exe_content = b"fake bios for copy fallback"
        expected_hash = hashlib.sha256(exe_content).hexdigest()

        catalog_xml = f"""<?xml version="1.0" encoding="utf-16"?>
        <Manifest>
          <SoftwareComponent path="FOLDER/bios.EXE" vendorVersion="2.10.1"
              dateTime="2025-03-10T10:00:00Z" hash="{expected_hash}">
            <ComponentType value="BIOS"/>
            <SupportedSystems><Brand><Model><Display>R660</Display></Model></Brand></SupportedSystems>
          </SoftwareComponent>
        </Manifest>"""
        catalog_gz = gzip.compress(catalog_xml.encode("utf-16"))

        mock_catalog_resp = MagicMock()
        mock_catalog_resp.read.return_value = catalog_gz
        mock_catalog_resp.__enter__ = lambda s: s
        mock_catalog_resp.__exit__ = MagicMock(return_value=False)

        mock_exe_resp = MagicMock()
        mock_exe_resp.__enter__ = lambda s: s
        mock_exe_resp.__exit__ = MagicMock(return_value=False)

        def fake_urlopen(req, timeout=None):
            if "Catalog" in req.full_url:
                return mock_catalog_resp
            return mock_exe_resp

        bios_dir = tmp_path / "bios_dest"
        bios_dir.mkdir()
        bios_archive = tmp_path / "bios_archive"
        bios_archive.mkdir()

        def fake_copyfileobj(src, dst):
            dst.write(exe_content)

        with (
            patch("dracs.webapp.urllib.request.urlopen", side_effect=fake_urlopen),
            patch("dracs.webapp.shutil.copyfileobj", side_effect=fake_copyfileobj),
            patch("dracs.webapp.BIOS_IMAGE_DIR", bios_dir),
            patch("dracs.webapp.BIOS_ARCHIVE_DIR", bios_archive),
            patch("dracs.webapp.os.link", side_effect=OSError("cross-device")),
        ):
            resp = client.post(
                "/api/latest-bios",
                data=json.dumps(
                    {
                        "model": "R660",
                        "hostname": "server01",
                        "current_version": "2.0.0",
                    }
                ),
                content_type="application/json",
            )
            data_str = resp.get_data(as_text=True)

        assert "FAIL" not in data_str
        web_path = bios_dir / "R660" / "bios.EXE"
        assert web_path.exists()

    def test_sha256_mismatch_stops(self, client, tmp_path):
        _login(client)

        exe_content = b"fake bios content"

        catalog_xml = """<?xml version="1.0" encoding="utf-16"?>
        <Manifest>
          <SoftwareComponent path="FOLDER/bios.EXE" vendorVersion="2.10.1"
              dateTime="2025-03-10T10:00:00Z" hash="bad_hash_value">
            <ComponentType value="BIOS"/>
            <SupportedSystems><Brand><Model><Display>R660</Display></Model></Brand></SupportedSystems>
          </SoftwareComponent>
        </Manifest>"""
        catalog_gz = gzip.compress(catalog_xml.encode("utf-16"))

        mock_catalog_resp = MagicMock()
        mock_catalog_resp.read.return_value = catalog_gz
        mock_catalog_resp.__enter__ = lambda s: s
        mock_catalog_resp.__exit__ = MagicMock(return_value=False)

        mock_exe_resp = MagicMock()
        mock_exe_resp.__enter__ = lambda s: s
        mock_exe_resp.__exit__ = MagicMock(return_value=False)

        def fake_urlopen(req, timeout=None):
            if "Catalog" in req.full_url:
                return mock_catalog_resp
            return mock_exe_resp

        bios_archive = tmp_path / "bios_archive"
        bios_archive.mkdir()

        def fake_copyfileobj(src, dst):
            dst.write(exe_content)

        with (
            patch("dracs.webapp.urllib.request.urlopen", side_effect=fake_urlopen),
            patch("dracs.webapp.shutil.copyfileobj", side_effect=fake_copyfileobj),
            patch("dracs.webapp.BIOS_ARCHIVE_DIR", bios_archive),
        ):
            resp = client.post(
                "/api/latest-bios",
                data=json.dumps(
                    {
                        "model": "R660",
                        "hostname": "server01",
                        "current_version": "2.0.0",
                    }
                ),
                content_type="application/json",
            )
            data_str = resp.get_data(as_text=True)

        assert "FAIL" in data_str
        assert '"type": "complete"' not in data_str
