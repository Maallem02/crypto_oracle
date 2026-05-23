"""
Data Collector — Phase 2 : collecte des données pour entraîner le modèle IA
Sauvegarde chaque signal + résultat (TP ou SL) dans SQLite
"""
import MetaTrader5 as mt5
from datetime import datetime, timedelta
from core.database import get_db


def init_signals_table():
    """Crée la table trade_signals et applique les migrations si nécessaire"""
    conn = get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS trade_signals (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp    TEXT    NOT NULL,
            symbol       TEXT    NOT NULL,
            timeframe    TEXT    NOT NULL,
            action       TEXT    NOT NULL,

            -- Features SMC (inputs du modèle IA)
            scalping_score REAL,
            adx            REAL,
            atr            REAL,
            atr_ratio      REAL,
            rsi            REAL,
            stoch_k        REAL,
            stoch_d        REAL,
            structure      TEXT,
            lg_strength    REAL,
            pd_zone        TEXT,
            pd_pct         REAL,
            htf_consensus  TEXT,
            hour           INTEGER,
            day_of_week    INTEGER,
            rr_ratio       REAL,

            -- Trade info
            entry    REAL,
            sl       REAL,
            tp1      REAL,
            ticket   INTEGER,

            -- Outcome (rempli après fermeture du trade)
            outcome    INTEGER DEFAULT NULL,
            profit     REAL    DEFAULT NULL,
            closed_at  TEXT    DEFAULT NULL
        )
    """)

    # ── Migrations : ajoute les colonnes manquantes si la table existait déjà ──
    new_columns = {
        "adx":       "REAL",
        "atr_ratio": "REAL",
        "rsi":       "REAL",
        "stoch_k":   "REAL",
        "stoch_d":   "REAL",
    }
    existing = {row[1] for row in conn.execute("PRAGMA table_info(trade_signals)").fetchall()}
    for col, col_type in new_columns.items():
        if col not in existing:
            conn.execute(f"ALTER TABLE trade_signals ADD COLUMN {col} {col_type}")
            print(f"✅ Migration: colonne '{col}' ajoutée à trade_signals")

    conn.commit()
    conn.close()


def save_signal(
    analysis:      dict,
    entry_data:    dict,
    trade_result:  dict,
    symbol:        str,
    timeframe:     str,
    htf_consensus: str,
):
    """Sauvegarde un signal + trade ouvert pour l'entraînement futur"""
    if not trade_result.get("success"):
        return   # on ne sauvegarde que les trades réellement exécutés

    now = datetime.now()
    lg  = analysis.get("liquidity_grab", {})
    pd  = analysis.get("premium_discount", {})

    conn = get_db()
    conn.execute("""
        INSERT INTO trade_signals
        (timestamp, symbol, timeframe, action,
         scalping_score, adx, atr, atr_ratio, rsi, stoch_k, stoch_d,
         structure, lg_strength, pd_zone, pd_pct,
         htf_consensus, hour, day_of_week, rr_ratio,
         entry, sl, tp1, ticket)
        VALUES (?,?,?,?, ?,?,?,?,?,?,?, ?,?,?,?, ?,?,?,?, ?,?,?,?)
    """, (
        now.isoformat(), symbol, timeframe, analysis.get("bias"),
        analysis.get("scalping_score"),
        analysis.get("adx"),
        analysis.get("atr"),
        analysis.get("atr_ratio"),
        analysis.get("rsi"),
        analysis.get("stoch_k"),
        analysis.get("stoch_d"),
        analysis.get("structure", {}).get("trend"),
        lg.get("strength", 0),
        pd.get("zone"),
        pd.get("position_pct"),
        htf_consensus,
        now.hour,
        now.weekday(),
        entry_data.get("rr_ratio"),
        entry_data.get("entry"),
        entry_data.get("sl"),
        entry_data.get("tp1"),
        trade_result.get("ticket"),
    ))
    conn.commit()
    conn.close()


def check_and_update_outcomes():
    """
    Vérifie les trades MT5 fermés et met à jour les outcomes.
    Appelé à chaque scan automatique.
    """
    try:
        deals = mt5.history_deals_get(
            datetime.now() - timedelta(days=2),
            datetime.now(),
        )
        if not deals:
            return

        conn = get_db()

        for deal in deals:
            # Seulement les deals de fermeture (DEAL_ENTRY_OUT = 1)
            if deal.entry != 1:
                continue
            # Seulement nos trades (magic number 234000)
            if deal.magic != 234000:
                continue

            row = conn.execute(
                "SELECT id FROM trade_signals WHERE ticket = ? AND outcome IS NULL",
                (deal.position_id,)
            ).fetchone()

            if row:
                outcome = 1 if deal.profit > 0 else 0
                conn.execute(
                    "UPDATE trade_signals SET outcome=?, profit=?, closed_at=? WHERE ticket=?",
                    (outcome, round(deal.profit, 2), datetime.now().isoformat(), deal.position_id)
                )

        conn.commit()
        conn.close()

    except Exception as e:
        print(f"Data collector error: {e}")


def get_stats() -> dict:
    """Statistiques sur les données collectées"""
    conn = get_db()

    total   = conn.execute("SELECT COUNT(*) FROM trade_signals").fetchone()[0]
    labeled = conn.execute("SELECT COUNT(*) FROM trade_signals WHERE outcome IS NOT NULL").fetchone()[0]
    wins    = conn.execute("SELECT COUNT(*) FROM trade_signals WHERE outcome = 1").fetchone()[0]
    losses  = conn.execute("SELECT COUNT(*) FROM trade_signals WHERE outcome = 0").fetchone()[0]
    avg_profit = conn.execute(
        "SELECT AVG(profit) FROM trade_signals WHERE outcome IS NOT NULL"
    ).fetchone()[0] or 0.0

    conn.close()

    win_rate = round(wins / labeled * 100, 1) if labeled > 0 else 0.0

    return {
        "total_signals":  total,
        "labeled":        labeled,
        "unlabeled":      total - labeled,
        "wins":           wins,
        "losses":         losses,
        "win_rate_pct":   win_rate,
        "avg_profit":     round(avg_profit, 2),
        "ai_ready":       labeled >= 200,   # 200 trades minimum pour entraîner
        "ai_progress":    f"{min(labeled, 200)}/200",
    }


def get_training_data() -> list:
    """Retourne les données labellisées pour entraîner le modèle IA"""
    conn = get_db()
    rows = conn.execute("""
        SELECT scalping_score, adx, atr, atr_ratio, rsi, stoch_k, stoch_d,
               structure, lg_strength, pd_zone, pd_pct,
               htf_consensus, hour, day_of_week, rr_ratio,
               symbol, timeframe, action, outcome
        FROM trade_signals
        WHERE outcome IS NOT NULL
        ORDER BY timestamp DESC
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]
