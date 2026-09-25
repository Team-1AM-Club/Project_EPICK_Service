"""Offline C-01 SQLite backup and restore commands.

Backup uses SQLite's online backup API. Restore is intentionally create-only;
operators must stop C-01 and retain or move the old database themselves.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path

CONSUMER_ID = "w3-c01/0.2-candidate/r2"


def _validate_c01(db: sqlite3.Connection) -> None:
    try:
        if db.execute("PRAGMA quick_check").fetchone() != ("ok",):
            raise ValueError("NOT_C01_DATABASE")
        if db.execute("SELECT consumer FROM identity").fetchone() != (CONSUMER_ID,):
            raise ValueError("NOT_C01_DATABASE")
        if db.execute("SELECT scope,ttl FROM c01_settings WHERE id=1").fetchone() is None:
            raise ValueError("NOT_C01_DATABASE")
    except sqlite3.Error:
        raise ValueError("NOT_C01_DATABASE") from None


def _copy_database(source: str | Path, target: str | Path) -> Path:
    source, target = Path(source), Path(target)
    if not source.is_file():
        raise ValueError("SOURCE_DATABASE_REQUIRED")
    if target.exists():
        raise ValueError("TARGET_ALREADY_EXISTS")
    target.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    os.close(handle)
    temporary = Path(temporary_name)
    try:
        with closing(sqlite3.connect(source)) as source_db:
            _validate_c01(source_db)
            with closing(sqlite3.connect(temporary)) as target_db:
                source_db.backup(target_db)
                _validate_c01(target_db)
        os.chmod(temporary, 0o600)
        temporary.replace(target)
        return target
    except (sqlite3.Error, OSError):
        raise ValueError("DATABASE_COPY_FAILED") from None
    finally:
        temporary.unlink(missing_ok=True)


def backup_database(source: str | Path, target: str | Path) -> Path:
    """Create a consistent online SQLite snapshot without replacing a file."""

    return _copy_database(source, target)


def restore_database(source: str | Path, target: str | Path) -> Path:
    """Validate a C-01 backup and restore it to a new, stopped-service path."""

    return _copy_database(source, target)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="C-01 SQLite backup/restore operator")
    commands = parser.add_subparsers(dest="command", required=True)
    backup = commands.add_parser("backup")
    backup.add_argument("--db", required=True, type=Path)
    backup.add_argument("--output", required=True, type=Path)
    restore = commands.add_parser("restore")
    restore.add_argument("--backup", required=True, type=Path)
    restore.add_argument("--db", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        target = (
            backup_database(args.db, args.output)
            if args.command == "backup"
            else restore_database(args.backup, args.db)
        )
    except ValueError as error:
        print(json.dumps({"status": "FAILED", "error": str(error)}))
        return 2
    print(json.dumps({"status": "CREATED", "path": str(target)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
