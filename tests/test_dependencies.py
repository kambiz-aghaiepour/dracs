"""Tests for dependency version requirements in pyproject.toml."""

from pathlib import Path

import pytest

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib


def _get_dependencies():
    pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
    data = tomllib.loads(pyproject.read_text())
    return data["project"]["dependencies"]


def _assert_dependency(deps, expected):
    matches = [
        d
        for d in deps
        if d.split(">")[0].split("=")[0] == expected.split(">")[0].split("=")[0]
    ]
    assert (
        len(matches) == 1
    ), f"Expected exactly one entry for {expected}, found {matches}"
    assert matches[0] == expected


@pytest.fixture(scope="module")
def deps():
    return _get_dependencies()


def test_requests(deps):
    _assert_dependency(deps, "requests>=2.32.0")


def test_python_dotenv(deps):
    _assert_dependency(deps, "python-dotenv>=1.0.0")


def test_tabulate(deps):
    _assert_dependency(deps, "tabulate>=0.9.0")


def test_rich(deps):
    _assert_dependency(deps, "rich>=13.9.0")


def test_pysnmp(deps):
    _assert_dependency(deps, "pysnmp>=7.1.8")


def test_sqlalchemy(deps):
    _assert_dependency(deps, "sqlalchemy>=2.0.0")


def test_flask(deps):
    _assert_dependency(deps, "flask>=3.0.0")


def test_gunicorn(deps):
    _assert_dependency(deps, "gunicorn>=21.2.0")


def test_uv(deps):
    _assert_dependency(deps, "uv")


def test_build(deps):
    _assert_dependency(deps, "build")


def test_twine(deps):
    _assert_dependency(deps, "twine")


def test_dependency_count(deps):
    assert len(deps) == 11
