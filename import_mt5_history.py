"""
import_mt5_history.py — Parse MT5 deal history and insert into trade_signals DB.
Run once: python import_mt5_history.py history.txt
"""
import sys
import os
import re
import io
from datetime import datetime
from collections import defaultdict, deque

# Force UTF-8 output on Windows
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

os.chdir(os.path.dirname(os.path.abspath(__file__)))

from core.database import get_db, init_db
from features.trading.data_collector import init_signals_table


# ── Number parser: European format "76 728,13" → 76728.13 ────────────────────
def parse_num(s: str) -> float | None:
    if not s:
        return None
    s = s.strip()
    # Remove all space variants (thousand separators + "- 13" sign space)
    for ch in ('\xa0', ' ', ' ', ' '):
        s = s.replace(ch, '')
    s = s.replace(',', '.')
    try:
        return float(s)
    except ValueError:
        return None


def parse_confidence(comment: str) -> float | None:
    """'CryptoOracle 95%' → 95.0, 'CryptoOracle 105' → 105.0"""
    m = re.search(r'CryptoOracle\s+(\d+)', comment)
    return float(m.group(1)) if m else None


# MT5 symbol → short name used in our system
MT5_TO_SHORT = {
    'BTCUSDm': 'BTC',    'ETHUSDm': 'ETH',    'SOLUSDm': 'SOL',
    'BNBUSDm': 'BNB',    'XRPUSDm': 'XRP',    'XAUUSDm': 'XAUUSD',
    'XAGUSDm': 'XAGUSD', 'GBPJPYm': 'GBPJPY', 'EURUSDm': 'EURUSD',
    'USDJPYm': 'USDJPY',
}


# ── Row parser ────────────────────────────────────────────────────────────────
def parse_rows(lines: list[str]) -> list[dict]:
    rows = []
    for line in lines:
        parts = line.rstrip('\n\r').split('\t')
        if len(parts) < 5:
            continue
        if parts[0].strip() in ('Heure', 'Time', 'Open Time', ''):
            continue  # header

        symbol = parts[2].strip()
        rtype  = parts[3].strip().lower()   # buy / sell / balance
        rdir   = parts[4].strip().lower()   # in / out / out by / by

        # Skip balance / deposit / withdrawal rows
        if rtype in ('balance', 'credit', 'bonus', '') or not symbol:
            continue

        try:
            dt = datetime.strptime(parts[0].strip(), '%Y.%m.%d %H:%M:%S')
        except ValueError:
            continue

        row = {
            'dt':        dt,
            'deal_id':   parts[1].strip(),
            'symbol':    symbol,
            'type':      rtype,
            'direction': rdir,
            'volume':    parse_num(parts[5]) if len(parts) > 5 else None,
            'price':     parse_num(parts[6]) if len(parts) > 6 else None,
            'profit':    parse_num(parts[11]) if len(parts) > 11 else None,
            'comment':   parts[13].strip() if len(parts) > 13 else '',
        }
        rows.append(row)

    # Sort chronologically (two accounts may be interleaved)
    rows.sort(key=lambda r: r['dt'])
    return rows


# ── FIFO trade matcher ────────────────────────────────────────────────────────
def match_trades(rows: list[dict]) -> list[dict]:
    """
    Match 'in' (opening) deals to 'out' (closing) deals using FIFO per
    (symbol, original_direction).

    Only bot trades (comment contains 'CryptoOracle') are tracked as entries.
    Any 'out' deal (bot or manual) can close them — this is the expected MT5 behavior.
    """
    open_q: dict[tuple, deque] = defaultdict(deque)
    matched = []

    for row in rows:
        d = row['direction']
        sym   = row['symbol']
        rtype = row['type']

        if d == 'in':
            if 'CryptoOracle' in row['comment']:
                # Key: (symbol, direction-of-original-trade)
                open_q[(sym, rtype)].append(row)

        elif d in ('out', 'out by'):
            profit = row['profit']
            if profit is None:
                continue

            # Closing type is opposite of opening type
            original_type = 'buy' if rtype == 'sell' else 'sell'
            key = (sym, original_type)

            if open_q[key]:
                entry = open_q[key].popleft()
                matched.append({
                    'entry':   entry,
                    'exit':    row,
                    'profit':  profit,
                    'outcome': 1 if profit > 0 else 0,
                })

    orphaned = sum(len(q) for q in open_q.values())
    if orphaned:
        print(f"  ⚠ {orphaned} entries without matching close (still open or cut off)")

    return matched


# ── DB insertion ──────────────────────────────────────────────────────────────
def insert_trades(matched: list[dict]) -> tuple[int, int]:
    init_db()
    init_signals_table()
    conn = get_db()

    inserted = skipped = 0

    for m in matched:
        entry   = m['entry']
        dt      = entry['dt']
        symbol  = MT5_TO_SHORT.get(entry['symbol'], entry['symbol'])
        action  = entry['type']            # 'buy' or 'sell'
        conf    = parse_confidence(entry['comment'])
        profit  = round(m['profit'], 2)
        outcome = m['outcome']

        # De-duplicate by deal_id stored as ticket
        try:
            deal_id = int(entry['deal_id'])
        except ValueError:
            continue

        existing = conn.execute(
            "SELECT id FROM trade_signals WHERE ticket = ?", (deal_id,)
        ).fetchone()
        if existing:
            skipped += 1
            continue

        conn.execute("""
            INSERT INTO trade_signals
            (timestamp, symbol, timeframe, action,
             scalping_score,
             hour, day_of_week,
             entry, ticket,
             outcome, profit, closed_at)
            VALUES (?,?,?,?, ?, ?,?, ?,?, ?,?,?)
        """, (
            dt.isoformat(),
            symbol,
            'imported',         # marks this as historical import
            action,
            conf,               # confidence → scalping_score
            dt.hour,
            dt.weekday(),       # 0=Mon … 6=Sun
            entry['price'],
            deal_id,
            outcome,
            profit,
            m['exit']['dt'].isoformat(),
        ))
        inserted += 1

    conn.commit()
    conn.close()
    return inserted, skipped


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    import argparse as _ap
    parser = _ap.ArgumentParser()
    parser.add_argument('file', nargs='?', help='MT5 history .txt file')
    parser.add_argument('--db', default=None, help='DB path (default: crypto_oracle.db)')
    args = parser.parse_args()

    # Override DB path before any DB import
    if args.db:
        from core.config import runtime
        runtime.db_path = args.db

    if args.file:
        with open(args.file, encoding='utf-8', errors='replace') as f:
            lines = f.readlines()
    else:
        print("Usage: python import_mt5_history.py history.txt [--db crypto_oracle_8000.db]")
        lines = sys.stdin.readlines()

    rows    = parse_rows(lines)
    matched = match_trades(rows)

    print(f"\n📋 Deals parsed   : {len(rows)}")
    print(f"🔗 Pairs matched  : {len(matched)}")

    wins   = sum(1 for m in matched if m['outcome'] == 1)
    losses = sum(1 for m in matched if m['outcome'] == 0)
    if matched:
        print(f"📊 Win rate       : {wins}/{len(matched)} = {wins/len(matched)*100:.1f}%")
        print(f"   Wins: {wins}  |  Losses: {losses}")

    inserted, skipped = insert_trades(matched)
    print(f"\n✅ Inserted : {inserted} new records")
    print(f"⏭  Skipped  : {skipped} duplicates")

    from features.trading.data_collector import get_stats
    stats = get_stats()
    print(f"\n📈 DB total labeled : {stats['labeled']}")
    print(f"   Win rate DB     : {stats['win_rate_pct']}%")
    print(f"   AI ready        : {stats['ai_ready']} ({stats['ai_progress']})")

    if stats['labeled'] >= 50:
        print("\n🧠 Training ML model...")
        from features.trading.ml_model import train_model
        meta = train_model()
        if meta:
            print(f"✅ Model trained!")
            print(f"   Accuracy : {meta['accuracy_pct']}% ± {meta.get('cv_std_pct', '?')}%")
            print(f"   Samples  : {meta['samples']}")
            top = list(meta['feature_importance'].items())[:5]
            print(f"   Top features:")
            for feat, imp in top:
                bar = '█' * int(imp * 100)
                print(f"     {feat:<20} {imp:.4f} {bar}")
        else:
            print("❌ Training failed — check logs above")
    else:
        remaining = 50 - stats['labeled']
        print(f"\n⏳ Need {remaining} more labeled trades to activate ML filter")


if __name__ == '__main__':
    main()
