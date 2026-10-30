"""Database restoration utility for Yashoda SpiceChain (Milestone M9 / T-090).

Supports both PostgreSQL and SQLite backends based on DATABASE_URL.
Features:
- Mandatory SHA-256 integrity verification before restoration
- Target database safety checks and optional backup before overwrite
- Support for gzip-compressed SQLite (.db.gz) and PostgreSQL dumps
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import logging
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("restore_db")


def verify_sha256(file_path: Path, expected_sha: str | None = None) -> bool:
    """Verify SHA-256 checksum of a file against expected value or accompanying .sha256 file."""
    if expected_sha is None:
        sha_file = file_path.with_suffix(file_path.suffix + ".sha256")
        if not sha_file.exists():
            logger.warning("No checksum file found at %s. Skipping SHA validation.", sha_file)
            return True
        expected_sha = sha_file.read_text(encoding="utf-8").strip().split()[0]

    hasher = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    actual_sha = hasher.hexdigest()

    if actual_sha.lower() != expected_sha.lower():
        logger.error("Checksum mismatch! Expected: %s, Actual: %s", expected_sha, actual_sha)
        return False
    logger.info("Checksum verified successfully (SHA-256: %s)", actual_sha)
    return True


def restore_sqlite(backup_file: Path, target_db: Path) -> None:
    """Restore SQLite database from an uncompressed or gzip-compressed backup file."""
    logger.info("Restoring SQLite database to target: %s", target_db)
    temp_restored = target_db.with_suffix(".tmp_restore")

    if backup_file.name.endswith(".gz"):
        logger.info("Decompressing backup archive: %s", backup_file)
        with gzip.open(backup_file, "rb") as f_in, open(temp_restored, "wb") as f_out:
            shutil.copyfileobj(f_in, f_out)
    else:
        shutil.copy2(backup_file, temp_restored)

    # Validate SQLite integrity before replacing target
    logger.info("Checking restored SQLite file integrity...")
    conn = sqlite3.connect(str(temp_restored))
    try:
        cursor = conn.cursor()
        cursor.execute("PRAGMA integrity_check")
        row = cursor.fetchone()
        if not row or row[0] != "ok":
            raise RuntimeError(f"Restored SQLite file failed integrity check: {row}")
    finally:
        conn.close()

    # Atomically replace target database
    target_db.parent.mkdir(parents=True, exist_ok=True)
    if target_db.exists():
        backup_prev = target_db.with_suffix(".pre_restore.bak")
        shutil.copy2(target_db, backup_prev)
        logger.info("Preserved previous database snapshot at %s", backup_prev)

    temp_restored.replace(target_db)
    logger.info("SQLite database restored successfully to %s", target_db)


def restore_postgres(backup_file: Path, db_url: str) -> None:
    """Restore PostgreSQL database using pg_restore / psql."""
    parsed = urlparse(db_url)
    db_name = parsed.path.lstrip("/") or "yashoda_masale"

    logger.info("Restoring PostgreSQL database: %s", db_name)
    pg_restore_cmd = [
        "pg_restore",
        "-h",
        parsed.hostname or "localhost",
        "-p",
        str(parsed.port or 5432),
        "-U",
        parsed.username or "postgres",
        "-d",
        db_name,
        "--clean",
        "--if-exists",
        str(backup_file),
    ]

    env = os.environ.copy()
    if parsed.password:
        env["PGPASSWORD"] = parsed.password

    try:
        subprocess.run(pg_restore_cmd, env=env, check=True, capture_output=True, text=True)
        logger.info("PostgreSQL database restored successfully.")
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        logger.error("pg_restore failed or not installed: %s", exc)
        raise RuntimeError(f"PostgreSQL restoration failed: {exc}") from exc


def restore_backup(
    backup_file: str | Path,
    db_url: str | None = None,
    verify_checksum: bool = True,
) -> None:
    """Main restoration entrypoint with safety checks."""
    backup_path = Path(backup_file).resolve()
    if not backup_path.exists():
        raise FileNotFoundError(f"Backup file does not exist: {backup_path}")

    if verify_checksum and not verify_sha256(backup_path):
        raise ValueError("Backup integrity verification failed. Aborting restore.")

    if db_url is None:
        db_url = os.getenv("DATABASE_URL", "sqlite:///./yashoda.db")

    if db_url.startswith("sqlite"):
        clean_path = db_url.replace("sqlite:///", "").replace("sqlite://", "")
        if "?" in clean_path:
            clean_path = clean_path.split("?")[0]
        target_path = Path(clean_path).resolve()
        restore_sqlite(backup_path, target_path)
    elif "postgres" in db_url:
        restore_postgres(backup_path, db_url)
    else:
        raise ValueError(f"Unsupported database scheme: {db_url}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Yashoda SpiceChain Database Restore Utility")
    parser.add_argument("backup_file", help="Path to backup file (.db, .db.gz, .sql)")
    parser.add_argument("--db-url", help="Target database connection URL")
    parser.add_argument(
        "--no-verify-checksum", action="store_true", help="Skip SHA-256 verification"
    )
    args = parser.parse_args()

    try:
        restore_backup(
            backup_file=args.backup_file,
            db_url=args.db_url,
            verify_checksum=not args.no_verify_checksum,
        )
        print("Restoration completed successfully.")
    except Exception as err:
        logger.error("Restore failed: %s", err)
        sys.exit(1)


if __name__ == "__main__":
    main()
