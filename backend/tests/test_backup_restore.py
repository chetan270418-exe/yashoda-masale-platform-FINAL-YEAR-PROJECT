"""Tests for database backup and restore utilities (Milestone M9 / T-090).

Verifies:
1. Online SQLite backup creation with compression and SHA-256 generation.
2. Metadata manifest generation (.meta.json).
3. SHA-256 verification rejects tampered backup files.
4. Full database restoration restores all tables, constraints, and rows accurately.
"""

import json
import sqlite3
import tempfile
from pathlib import Path

import pytest

from scripts.backup_db import compute_sha256, create_backup
from scripts.restore_db import restore_backup, verify_sha256


@pytest.fixture
def sample_sqlite_db():
    """Create a temporary SQLite database populated with sample data."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)

    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()
    cursor.execute("CREATE TABLE products (id TEXT PRIMARY KEY, name TEXT, price REAL)")
    cursor.execute("INSERT INTO products VALUES ('p1', 'Turmeric Powder', 150.00)")
    cursor.execute("INSERT INTO products VALUES ('p2', 'Chilli Powder', 220.50)")
    conn.commit()
    conn.close()

    yield db_path

    if db_path.exists():
        db_path.unlink()


def test_backup_and_restore_cycle(sample_sqlite_db: Path):
    """Test full cycle: backup -> checksum check -> restore -> verify data."""
    with tempfile.TemporaryDirectory() as out_dir:
        out_path = Path(out_dir)

        # 1. Create compressed backup
        db_url = f"sqlite:///{sample_sqlite_db}"
        res = create_backup(db_url=db_url, output_dir=out_path, compress=True)

        backup_file = Path(res["backup_file"])
        checksum_file = Path(res["checksum_file"])
        meta_file = Path(res["meta_file"])

        assert backup_file.exists()
        assert checksum_file.exists()
        assert meta_file.exists()
        assert backup_file.name.endswith(".db.gz")

        # 2. Check metadata content
        meta = json.loads(meta_file.read_text(encoding="utf-8"))
        assert meta["engine"] == "sqlite"
        assert meta["sha256"] == res["sha256"]
        assert meta["compressed"] is True

        # 3. Verify SHA256 matches actual file
        assert compute_sha256(backup_file) == res["sha256"]
        assert verify_sha256(backup_file) is True

        # 4. Restore to new location
        restored_db_path = out_path / "restored.db"
        restore_backup(
            backup_file=backup_file,
            db_url=f"sqlite:///{restored_db_path}",
            verify_checksum=True,
        )

        assert restored_db_path.exists()

        # 5. Verify restored data
        conn = sqlite3.connect(str(restored_db_path))
        cursor = conn.cursor()
        cursor.execute("SELECT id, name, price FROM products ORDER BY id")
        rows = cursor.fetchall()
        conn.close()

        assert len(rows) == 2
        assert rows[0] == ("p1", "Turmeric Powder", 150.00)
        assert rows[1] == ("p2", "Chilli Powder", 220.50)


def test_restore_rejects_tampered_backup(sample_sqlite_db: Path):
    """Restoration must abort if SHA-256 does not match."""
    with tempfile.TemporaryDirectory() as out_dir:
        out_path = Path(out_dir)
        db_url = f"sqlite:///{sample_sqlite_db}"
        res = create_backup(db_url=db_url, output_dir=out_path, compress=False)

        backup_file = Path(res["backup_file"])
        # Tamper with the backup file
        with open(backup_file, "ab") as f:
            f.write(b"\x00corrupt_data")

        restored_db_path = out_path / "restored_corrupt.db"
        with pytest.raises(ValueError, match="Backup integrity verification failed"):
            restore_backup(
                backup_file=backup_file,
                db_url=f"sqlite:///{restored_db_path}",
                verify_checksum=True,
            )
