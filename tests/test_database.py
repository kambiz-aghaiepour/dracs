import pytest
import sqlite3

from dracs import db_initialize

def test_db_initialize_creates_table(temp_db):
    """Test that db_initialize creates the systems table."""
    db_initialize(temp_db)

    conn = sqlite3.connect(temp_db)
    cursor = conn.cursor()

    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='systems'")
    result = cursor.fetchone()
    conn.close()

    assert result is not None
    assert result[0] == 'systems'

def test_db_initialize_table_schema(temp_db):
    """Test that the systems table has the correct schema."""
    db_initialize(temp_db)

    conn = sqlite3.connect(temp_db)
    cursor = conn.cursor()

    cursor.execute("PRAGMA table_info(systems)")
    columns = cursor.fetchall()
    conn.close()

    column_names = [col[1] for col in columns]
    expected_columns = ['svc_tag', 'name', 'model', 'idrac_version', 'bios_version', 'exp_date', 'exp_epoch']

    assert column_names == expected_columns

def test_db_initialize_idempotent(temp_db):
    """Test that calling db_initialize multiple times is safe."""
    db_initialize(temp_db)
    db_initialize(temp_db)

    conn = sqlite3.connect(temp_db)
    cursor = conn.cursor()

    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='systems'")
    result = cursor.fetchone()
    conn.close()

    assert result is not None
