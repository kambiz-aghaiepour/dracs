"""Tests for the dracs init command."""

from pathlib import Path

from dracs.cli import EXAMPLE_FILES, init_config_files


class TestExampleFilesBundled:
    def test_all_example_files_exist_in_package(self):
        examples_dir = (
            Path(__file__).resolve().parent.parent / "src" / "dracs" / "examples"
        )
        for src_name in EXAMPLE_FILES:
            assert (
                examples_dir / src_name
            ).exists(), f"Missing bundled file: {src_name}"

    def test_example_files_list(self):
        expected = {
            ".env.example": ".env.example",
            "drac-passwords.ini.example": "drac-passwords.ini.example",
            "BIOS-filename.ini.example": "BIOS-filename.ini.example",
        }
        assert EXAMPLE_FILES == expected


class TestInitConfigFiles:
    def test_creates_all_files(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        init_config_files()
        for dst_name in EXAMPLE_FILES.values():
            assert (tmp_path / dst_name).exists()

    def test_created_files_have_content(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        init_config_files()
        for dst_name in EXAMPLE_FILES.values():
            assert (tmp_path / dst_name).stat().st_size > 0

    def test_skips_existing_files(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".env.example").write_text("existing content")
        init_config_files()
        assert (tmp_path / ".env.example").read_text() == "existing content"
        output = capsys.readouterr().out
        assert "Skipped" in output
        assert ".env.example" in output

    def test_prints_created_files(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        init_config_files()
        output = capsys.readouterr().out
        assert "Created:" in output
        for dst_name in EXAMPLE_FILES.values():
            assert dst_name in output

    def test_prints_copy_reminder(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        init_config_files()
        output = capsys.readouterr().out
        assert "Copy .env.example to .env" in output

    def test_no_copy_reminder_when_nothing_created(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        for dst_name in EXAMPLE_FILES.values():
            (tmp_path / dst_name).write_text("existing")
        init_config_files()
        output = capsys.readouterr().out
        assert "Copy .env.example to .env" not in output

    def test_partial_skip(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".env.example").write_text("existing")
        init_config_files()
        output = capsys.readouterr().out
        assert "Created:" in output
        assert "Skipped" in output
        assert "drac-passwords.ini.example" in output
        assert "BIOS-filename.ini.example" in output
