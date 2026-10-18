import hashlib
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from app.config import ROOT


@contextmanager
def connect(path: Path, *, write=False):
    connection = sqlite3.connect(path, timeout=30, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
        yield connection
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()


def migrate(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path, timeout=30) as db:
        db.execute("PRAGMA journal_mode = WAL")
        db.execute("CREATE TABLE IF NOT EXISTS schema_migrations (name TEXT PRIMARY KEY)")
        for migration in sorted((ROOT / "migrations").glob("*.sql")):
            if not db.execute("SELECT 1 FROM schema_migrations WHERE name = ?", (migration.name,)).fetchone():
                # Migration names are repository-owned, never user input.
                db.executescript(
                    "BEGIN IMMEDIATE;\n" + migration.read_text(encoding="utf-8")
                    + f"\nINSERT INTO schema_migrations VALUES ('{migration.name}');\nCOMMIT;"
                )


def key_hash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def tenant_for_key(path: Path, key: str):
    with connect(path) as db:
        row = db.execute("SELECT id FROM tenants WHERE api_key_hash = ?", (key_hash(key),)).fetchone()
        return row["id"] if row else None

