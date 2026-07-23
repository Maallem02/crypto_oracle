"""
Data Collector — Phase 2 : collecte des données pour entraîner le modèle IA
Sauvegarde chaque signal + résultat (TP ou SL) dans SQLite
"""
import MetaTrader5 as mt5
import sqlite3
import json
from datetime import datetime, timedelta
from core.database import get_db


def get_ml_db():
    """Returns connection to the SHARED ML database (all accounts combined)."""
    from core.config import runtime
    return sqlite3.connect(runtime.ml_db_path)


def _ensure_ml_table(conn):
    """Create trade_signals table in ML DB if not exists."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS trade_signals (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp       TEXT,
            symbol          TEXT,
            timeframe       TEXT,
            action          TEXT,
            scalping_score  REAL,
            adx             REAL,
            atr_ratio       REAL,
            rsi             REAL,
            stoch_k         REAL,
            stoch_d         REAL,
            structure       TEXT,
            lg_strength     REAL,
            pd_zone         TEXT,
            pd_pct          REAL,
            htf_consensus       TEXT,
            htf_timeframe       TEXT,
            volume_ratio        REAL,
            spread_pct          REAL,
            symbol_winrate      REAL,
            consecutive_losses  INTEGER,
            hour            INTEGER,
            day_of_week     INTEGER,
            rr_ratio        REAL,
            entry           REAL,
            ticket          INTEGER UNIQUE,
            outcome         INTEGER,
            profit          REAL,
            closed_at       TEXT,
            instance        TEXT
        )
    """)
    existing = {row[1] for row in conn.execute("PRAGMA table_info(trade_signals)").fetchall()}
    for col, ctype in [
        ("htf_timeframe",      "TEXT"),
        ("volume_ratio",       "REAL"),
        ("spread_pct",         "REAL"),
        ("symbol_winrate",     "REAL"),
        ("consecutive_losses", "INTEGER"),
        ("smc_confluence",     "TEXT"),
        ("mfe_r",              "REAL"),
        ("mae_r",              "REAL"),
    ]:
        if col not in existing:
            conn.execute(f"ALTER TABLE trade_signals ADD COLUMN {col} {ctype}")


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
        "adx":               "REAL",
        "atr_ratio":         "REAL",
        "rsi":               "REAL",
        "stoch_k":           "REAL",
        "stoch_d":           "REAL",
        "htf_timeframe":     "TEXT",
        "volume_ratio":      "REAL",
        "spread_pct":        "REAL",
        "symbol_winrate":    "REAL",
        "consecutive_losses":"INTEGER",
        "smc_confluence":    "TEXT",
        # Max favorable/adverse excursion in R units, updated live by
        # manage_open_positions — the ground truth for tuning exits
        # (BE trigger, trail distance, TP placement).
        "mfe_r":             "REAL",
        "mae_r":             "REAL",
    }
    existing = {row[1] for row in conn.execute("PRAGMA table_info(trade_signals)").fetchall()}
    for col, col_type in new_columns.items():
        if col not in existing:
            conn.execute(f"ALTER TABLE trade_signals ADD COLUMN {col} {col_type}")
            print(f"[OK] Migration: colonne '{col}' ajoutee a trade_signals")

    conn.commit()
    conn.close()


def init_scalp_log_table():
    """
    Full scalp_history row (score breakdown + conditions text), stored as
    JSON. Schema-free on purpose — every time a new score component gets
    added (zone_boost, ema_boost, ...) it's captured automatically without
    needing a migration. Survives restarts, unlike the in-memory list.

    outcome/profit are tracked HERE (not just in trade_signals), because
    trade_signals only ever gets a row via save_signal() — which is only
    called on the LG-primary path. The S/R and M1-confirmation fallback
    trades have no trade_signals row at all, so their outcomes had nowhere
    to attach and silently vanished from any stats query. scalp_log covers
    every strategy uniformly, so outcomes belong here.
    """
    conn = get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS scalp_log (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp     TEXT    NOT NULL,
            symbol        TEXT    NOT NULL,
            action        TEXT,
            ticket        INTEGER,
            blended_score REAL,
            data          TEXT    NOT NULL,
            outcome       INTEGER DEFAULT NULL,
            profit        REAL    DEFAULT NULL,
            closed_at     TEXT    DEFAULT NULL
        )
    """)
    existing = {row[1] for row in conn.execute("PRAGMA table_info(scalp_log)").fetchall()}
    for col, col_type in [("outcome", "INTEGER"), ("profit", "REAL"), ("closed_at", "TEXT")]:
        if col not in existing:
            conn.execute(f"ALTER TABLE scalp_log ADD COLUMN {col} {col_type}")
    conn.commit()
    conn.close()


def save_scalp_log(entry: dict):
    """Persist one scalp_history row to disk."""
    try:
        conn = get_db()
        conn.execute(
            "INSERT INTO scalp_log (timestamp, symbol, action, ticket, blended_score, data) "
            "VALUES (?,?,?,?,?,?)",
            (
                entry.get("timestamp"),
                entry.get("symbol"),
                entry.get("action"),
                (entry.get("result") or {}).get("ticket"),
                entry.get("blended_score"),
                json.dumps(entry, default=str),
            ),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[SCALP-LOG] save failed: {e}")


def load_scalp_log(limit: int = 500) -> list:
    """Load persisted scalp_history entries from disk, oldest→newest."""
    try:
        conn = get_db()
        rows = conn.execute(
            "SELECT data FROM scalp_log ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        conn.close()
        entries = [json.loads(r[0]) for r in rows]
        entries.reverse()
        return entries
    except Exception as e:
        print(f"[SCALP-LOG] load failed: {e}")
        return []


def init_rejection_log_table():
    """
    Persisted rejection log — unlike the in-memory scan_rejections list
    (capped at 200, gets drowned out by repetitive messages like streak
    pauses), this keeps every rejection on disk so filtering decisions
    (M1 momentum, counter-trend gate, HTF conflict...) can be checked
    against what price actually did afterward, days later if needed.
    """
    conn = get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS rejection_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp   TEXT    NOT NULL,
            symbol      TEXT    NOT NULL,
            timeframe   TEXT,
            bias        TEXT,
            price       REAL,
            reason      TEXT    NOT NULL,
            signal_type TEXT,
            data        TEXT
        )
    """)
    existing = {row[1] for row in conn.execute("PRAGMA table_info(rejection_log)").fetchall()}
    if "signal_type" not in existing:
        conn.execute("ALTER TABLE rejection_log ADD COLUMN signal_type TEXT")
    conn.commit()
    conn.close()


def save_rejection(entry: dict):
    """Persist one rejection-log row to disk."""
    try:
        conn = get_db()
        conn.execute(
            "INSERT INTO rejection_log (timestamp, symbol, timeframe, bias, price, reason, signal_type, data) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (
                entry.get("ts"),
                entry.get("symbol"),
                entry.get("timeframe"),
                entry.get("bias"),
                entry.get("price"),
                entry.get("reason"),
                entry.get("signal_type", "lg_primary"),
                json.dumps(entry, default=str),
            ),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[REJECTION-LOG] save failed: {e}")


def load_rejections(limit: int = 500, symbol: str = None, reason_contains: str = None,
                     signal_type: str = None) -> list:
    """Load persisted rejections from disk, optionally filtered."""
    try:
        conn = get_db()
        query = "SELECT data FROM rejection_log WHERE 1=1"
        params = []
        if symbol:
            query += " AND symbol = ?"
            params.append(symbol)
        if reason_contains:
            query += " AND (reason LIKE ? OR data LIKE ?)"
            params.append(f"%{reason_contains}%")
            params.append(f"%{reason_contains}%")
        if signal_type:
            query += " AND signal_type = ?"
            params.append(signal_type)
        query += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(query, params).fetchall()
        conn.close()
        entries = [json.loads(r[0]) for r in rows]
        entries.reverse()
        return entries
    except Exception as e:
        print(f"[REJECTION-LOG] load failed: {e}")
        return []


def save_signal(
    analysis:           dict,
    entry_data:         dict,
    trade_result:       dict,
    symbol:             str,
    timeframe:          str,
    htf_consensus:      str,
    htf_timeframe:      str   = None,
    volume_ratio:       float = None,
    spread_pct:         float = None,
    symbol_winrate:     float = None,
    consecutive_losses: int   = None,
):
    """Sauvegarde un signal + trade ouvert pour l'entraînement futur"""
    if not trade_result.get("success"):
        return   # on ne sauvegarde que les trades réellement exécutés

    smc_confluence = analysis.get("smc_confluence")

    now = datetime.now()
    lg  = analysis.get("liquidity_grab", {})
    pd  = analysis.get("premium_discount", {})

    from core.config import runtime

    sql = """
        INSERT INTO trade_signals
        (timestamp, symbol, timeframe, action,
         scalping_score, adx, atr, atr_ratio, rsi, stoch_k, stoch_d,
         structure, lg_strength, pd_zone, pd_pct,
         htf_consensus, htf_timeframe,
         volume_ratio, spread_pct, symbol_winrate, consecutive_losses, smc_confluence,
         hour, day_of_week, rr_ratio,
         entry, sl, tp1, ticket)
        VALUES (?,?,?,?, ?,?,?,?,?,?,?, ?,?,?,?, ?,?, ?,?,?,?,?, ?,?,?, ?,?,?,?)
    """

    values = (
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
        htf_consensus, htf_timeframe,
        volume_ratio, spread_pct, symbol_winrate, consecutive_losses, smc_confluence,
        now.hour, now.weekday(),
        entry_data.get("rr_ratio"),
        entry_data.get("entry"),
        entry_data.get("sl"),
        entry_data.get("tp1"),
        trade_result.get("ticket"),
    )

    # Save to own account DB
    conn = get_db()
    conn.execute(sql, values)
    conn.commit()
    conn.close()

    # Save to shared ML DB (all accounts combined)
    try:
        ml_conn = get_ml_db()
        _ensure_ml_table(ml_conn)
        ml_conn.execute("""
            INSERT OR IGNORE INTO trade_signals
            (timestamp, symbol, timeframe, action,
             scalping_score, adx, atr_ratio, rsi, stoch_k, stoch_d,
             structure, lg_strength, pd_zone, pd_pct,
             htf_consensus, htf_timeframe,
             volume_ratio, spread_pct, symbol_winrate, consecutive_losses, smc_confluence,
             hour, day_of_week, rr_ratio,
             entry, ticket, instance)
            VALUES (?,?,?,?, ?,?,?,?,?,?, ?,?,?,?, ?,?, ?,?,?,?,?, ?,?,?, ?,?,?)
        """, (
            now.isoformat(), symbol, timeframe, analysis.get("bias"),
            analysis.get("scalping_score"),
            analysis.get("adx"),
            analysis.get("atr_ratio"),
            analysis.get("rsi"),
            analysis.get("stoch_k"),
            analysis.get("stoch_d"),
            analysis.get("structure", {}).get("trend"),
            lg.get("strength", 0),
            pd.get("zone"),
            pd.get("position_pct"),
            htf_consensus, htf_timeframe,
            volume_ratio, spread_pct, symbol_winrate, consecutive_losses, smc_confluence,
            now.hour, now.weekday(),
            entry_data.get("rr_ratio"),
            entry_data.get("entry"),
            trade_result.get("ticket"),
            runtime.instance,
        ))
        ml_conn.commit()
        ml_conn.close()
    except Exception as e:
        print(f"[ML-DB] Failed to save to shared ML DB: {e}")


def update_excursion(ticket, mfe_r: float, mae_r: float):
    """
    Persist max favorable/adverse excursion (in R units) for an open trade.
    Called by manage_open_positions every 30s while values change; the last
    write before close is the trade's final MFE/MAE.
    """
    params = (round(mfe_r, 3), round(mae_r, 3), ticket)
    try:
        conn = get_db()
        conn.execute("UPDATE trade_signals SET mfe_r=?, mae_r=? WHERE ticket=?", params)
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[MFE] main-db update failed t={ticket}: {e}")
    try:
        ml_conn = get_ml_db()
        ml_conn.execute("UPDATE trade_signals SET mfe_r=?, mae_r=? WHERE ticket=?", params)
        ml_conn.commit()
        ml_conn.close()
    except Exception:
        pass


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

            outcome = 1 if deal.profit > 0 else 0
            params  = (outcome, round(deal.profit, 2), datetime.now().isoformat(), deal.position_id)

            row = conn.execute(
                "SELECT id FROM trade_signals WHERE ticket = ? AND outcome IS NULL",
                (deal.position_id,)
            ).fetchone()

            if row:
                conn.execute(
                    "UPDATE trade_signals SET outcome=?, profit=?, closed_at=? WHERE ticket=?",
                    params
                )
                try:
                    ml_conn = get_ml_db()
                    ml_conn.execute(
                        "UPDATE trade_signals SET outcome=?, profit=?, closed_at=? WHERE ticket=?",
                        params
                    )
                    ml_conn.commit()
                    ml_conn.close()
                except Exception:
                    pass

            # scalp_log covers ALL strategies (lg_primary, support_resistance,
            # m1_momentum) — trade_signals only ever gets a row for lg_primary,
            # so this is the only place S/R and M1 trades get an outcome at all.
            conn.execute(
                "UPDATE scalp_log SET outcome=?, profit=?, closed_at=? "
                "WHERE ticket=? AND outcome IS NULL",
                params
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
    """Returns labeled data from the SHARED ML DB (all accounts combined)."""
    try:
        conn = get_ml_db()
        _ensure_ml_table(conn)
    except Exception:
        conn = get_db()  # fallback to own DB
    rows = conn.execute("""
        SELECT scalping_score, adx, atr_ratio, rsi, stoch_k, stoch_d,
               structure, lg_strength, pd_zone, pd_pct,
               htf_consensus, htf_timeframe,
               volume_ratio, spread_pct, symbol_winrate, consecutive_losses, smc_confluence,
               hour, day_of_week, rr_ratio,
               action, outcome
        FROM trade_signals
        WHERE outcome IS NOT NULL
        ORDER BY timestamp DESC
    """).fetchall()
    conn.close()
    cols = ["scalping_score","adx","atr_ratio","rsi","stoch_k","stoch_d",
            "structure","lg_strength","pd_zone","pd_pct",
            "htf_consensus","htf_timeframe",
            "volume_ratio","spread_pct","symbol_winrate","consecutive_losses","smc_confluence",
            "hour","day_of_week","rr_ratio","action","outcome"]
    return [dict(zip(cols, r)) for r in rows]
