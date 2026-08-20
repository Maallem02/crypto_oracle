import sqlite3
from core.config import runtime

# NOTE: runtime.db_path only resolves correctly when main.py's startup code
# has loaded .env.<port> first. Standalone scripts don't do that, so we
# point explicitly at the real per-instance db here.
REAL_MAIN_DB = "crypto_oracle_8000.db"

for path in (REAL_MAIN_DB, runtime.ml_db_path):
    print(f"\n=== {path} ===")
    try:
        conn = sqlite3.connect(path)
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [r[0] for r in cur.fetchall()]
        print("tables:", tables)
        for t in tables:
            cur.execute(f"PRAGMA table_info({t})")
            cols = [r[1] for r in cur.fetchall()]
            cur.execute(f"SELECT COUNT(*) FROM {t}")
            count = cur.fetchone()[0]
            print(f"  {t}: {count} rows, columns={cols}")
        conn.close()
    except Exception as e:
        print("error:", e)
