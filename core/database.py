import sqlite3, hashlib, os
from core.config import runtime

def get_db():
    conn = sqlite3.connect(runtime.db_path)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            username TEXT NOT NULL,
            hashed_password TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()
    # Initialiser la table de collecte de données IA
    from features.trading.data_collector import init_signals_table
    init_signals_table()
