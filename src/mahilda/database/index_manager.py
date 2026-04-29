from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from sqlalchemy import MetaData, text
from tqdm import tqdm

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection


class IndexManager:
    """
    Manages index creation (especially for SQLite).
    """

    def __init__(self, conn: Connection, metadata: MetaData) -> None:
        self.conn = conn
        self.metadata = metadata
        self.logger = logging.getLogger("query_time")

    def create_indexes(self) -> None:
        """Create indexes for all columns in all tables."""
        with self.conn.begin():
            for table in self.metadata.tables.values():
                for column in table.columns:
                    index_name = f"idx_{table.name}_{column.name}"
                    self.conn.execute(
                        text(f'CREATE INDEX IF NOT EXISTS "{index_name}" ON "{table.name}" ("{column.name}");')
                    )

    def create_composed_indexes(self, cols_list: list[tuple[str, str, str, str]]) -> None:
        """Create composed indexes for tuples of columns."""
        for t1, c1, t2, c2 in tqdm(cols_list, desc="Creating composed indexes", leave=False):
            if t1 == t2 and c1 != c2:
                index_name = f"idx_{t1}_{c1}_{c2}"
                self.conn.execute(text(f"CREATE INDEX IF NOT EXISTS {index_name} ON {t1} ({c1}, {c2});"))
                self.conn.commit()
