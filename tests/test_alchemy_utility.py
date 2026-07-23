from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

from marita.database.alchemy_utility import AlchemyUtility

if TYPE_CHECKING:
    from pathlib import Path


def test_sqlite_zero_date_sentinel_loads_as_string(tmp_path: Path) -> None:
    db_path = tmp_path / "zero_date.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE players (id INTEGER PRIMARY KEY, birthDate DATE)")
        conn.execute("INSERT INTO players (id, birthDate) VALUES (1, '0000-00-00')")

    with AlchemyUtility(
        f"sqlite:///{db_path}",
        database_path=str(tmp_path),
        create_index=False,
        create_csv=False,
        create_tsv=False,
    ) as db_util:
        assert db_util.tables_data["players"]["columns"] == ["id", "birthDate"]
        assert db_util.tables_data["players"]["rows"] == [(1, "0000-00-00")]
