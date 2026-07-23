from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import MetaData, String, create_engine
from sqlalchemy.sql.sqltypes import Date, DateTime, Time

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection, Engine


class DatabaseConnectionManager:
    """Manage SQLAlchemy engine, metadata reflection, and open connection."""

    def __init__(self, db_url: str):
        self.db_url = db_url
        self.engine: Engine | None = None
        self.conn: Connection | None = None
        try:
            self.engine = create_engine(db_url)
            self.metadata = MetaData()
            self.metadata.reflect(bind=self.engine)
            if self.engine.dialect.name == "sqlite":
                self._coerce_sqlite_datetime_columns_to_string()
            self.conn = self.engine.connect()
        except Exception:
            self.close()
            raise

    def _coerce_sqlite_datetime_columns_to_string(self) -> None:
        for table in self.metadata.tables.values():
            for column in table.columns:
                if isinstance(column.type, Date | DateTime | Time):
                    column.type = String()

    def close(self) -> None:
        if self.conn is not None:
            self.conn.close()
            self.conn = None
        if self.engine is not None:
            self.engine.dispose()
            self.engine = None
