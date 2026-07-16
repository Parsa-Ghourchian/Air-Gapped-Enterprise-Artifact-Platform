import os
from typing import Any

from psycopg import Connection
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool


DATABASE_URL = os.getenv(
    "PORTAL_DATABASE_URL",
    "postgresql://portal:portal-change-me@postgres:5432/portal",
)
POOL_MIN_SIZE = int(os.getenv("PORTAL_DB_POOL_MIN_SIZE", "1"))
POOL_MAX_SIZE = int(os.getenv("PORTAL_DB_POOL_MAX_SIZE", "10"))
_pool: ConnectionPool[Connection[dict[str, Any]]] | None = None


def get_pool() -> ConnectionPool[Connection[dict[str, Any]]]:
    global _pool
    if _pool is None:
        _pool = ConnectionPool(
            DATABASE_URL,
            min_size=POOL_MIN_SIZE,
            max_size=POOL_MAX_SIZE,
            kwargs={"row_factory": dict_row},
        )
    return _pool


def close_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


class DBConnection:
    def __init__(self) -> None:
        self._conn: Connection[dict[str, Any]] | None = None
        self._ctx = None

    def __enter__(self) -> "DBConnection":
        self._ctx = get_pool().connection()
        self._conn = self._ctx.__enter__()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._conn is None:
            return
        try:
            if exc_type is None:
                self._conn.commit()
            else:
                self._conn.rollback()
        finally:
            if self._ctx is not None:
                self._ctx.__exit__(exc_type, exc, tb)
            self._conn = None
            self._ctx = None

    @staticmethod
    def _translate(sql: str) -> str:
        return sql.replace("?", "%s")

    def execute(self, sql: str, params: Any = None):
        if self._conn is None:
            raise RuntimeError("Database connection is not open.")
        return self._conn.execute(self._translate(sql), params)


def db_conn() -> DBConnection:
    return DBConnection()


def db_ping() -> bool:
    with db_conn() as conn:
        conn.execute("SELECT 1").fetchone()
    return True


def row_to_dict(row: Any) -> dict[str, Any]:
    return dict(row)
