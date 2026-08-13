"""Create a fresh SQLite database from schema.sql."""
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "consultbae.db"
SCHEMA = Path(__file__).parent / "schema.sql"

def main():
    if DB_PATH.exists():
        DB_PATH.unlink()          # start clean every run
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(SCHEMA.read_text())
    conn.commit()
    tables = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    conn.close()
    print(f"Created {DB_PATH}")
    print(f"{len(tables)} tables:", ", ".join(t[0] for t in tables))

if __name__ == "__main__":
    main()