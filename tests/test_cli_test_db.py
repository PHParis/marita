import sqlite3
from pathlib import Path

from mahilda.cli.main import main


def test_test_db_command_creates_sqlite_file(tmp_path: Path) -> None:
    db_path = tmp_path / "smoke.db"

    exit_code = main(["test-db", "--output", str(db_path)])

    assert exit_code == 0
    assert db_path.exists()

    with sqlite3.connect(db_path) as conn:
        table_names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "Person" in table_names

        row_count = conn.execute("SELECT COUNT(*) FROM Person").fetchone()[0]
        assert row_count == 2
