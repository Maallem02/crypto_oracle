# Session handoff — crypto_oracle bot (2026-08-14)

Paste this into a new session to continue without re-deriving anything.

---

## 1. Basics

| | |
|---|---|
| Backend | `C:\Users\MSI\Desktop\loumi\crypt\crypto_oracle-backend` |
| Run | `venv\Scripts\python.exe main.py --port 8000` (PowerShell, no `&&`) |
| **Live DB** | **`crypto_oracle_8000.db`** — 279 MB, WAL, indexed. `crypto_oracle.db` is stale, ignore it |
| Shared ML DB | `crypto_oracle_ml.db` |
| Offline dataset | `offline_dataset.sqlite` — 152k labelled M5 samples, Jan 11 → Jul 23 |
| App | Flutter, `crypto_oracle-main`. Backend URL now `http://100.89.32.70:8000` (Tailscale) |

**Account: $69.97.** All-time 572 closed trades, **−$488.64**. Since the 08-03 rebuild: 89 closed, −$11.11.
Note a **−$21.09 withdrawal** on 08-09 — not a trading loss.

---

## 2. Current configuration

| Setting | Value | Why |
|---|---|---|
| `MAX_RISK_PCT` (executor.py) | **0.07** hard reject | was warn-only up to 90%; a gold trade lost 19.7% of the account |
| `htf_4h_filter_enabled` | True | +0.077 → +0.109 R/trade |
| `trail_start_r` | 1.0 | R-based, not % of TP |
| `be_trigger_r` | 1.5 | neutral, rarely fires |
| `pending_entry_enabled` | True, **buys only** | pending sells are 0-for-9 live |
| `pending_max_age_minutes` | 480 | was 120; 18 of 37 cancelled orders would have filled |
| `skip_equilibrium_pd` / `min_adx` | False / 0 | available, measured as profit-destroying |
| `meta_gate_enabled` | **False (shadow)** | armed then disarmed — see §4 |
| `PA_ALLOWED_SYMBOLS` | XAUUSD, XAGUSD | PA loses on BTC/ETH/EURUSD |
| `BLOCKED_SYMBOLS` | GBPJPY, USDJPY | 61 trades, 21.3% WR, −$88 |
| `SR_FALLBACK` / `M1_FALLBACK` | unwired | flags exist but calls were removed |

App overwrites settings on `/scalping/start`. Code-level constants (`MAX_RISK_PCT`,
`BLOCKED_SYMBOLS`, `PA_ALLOWED_SYMBOLS`) survive that; settings fields do not.

---

## 3. Bugs found and fixed

1. **4 divergent MT5 inits** — `executor._mt5_init` force-relogged on every order, flipping accounts. Now all delegate to `mt5_client.ensure_mt5()`.
2. **Trail trigger** — was "40% of TP", equal to 1.0R only at RR 2.5. Pending entries run RR 6–25, so the trail armed at 4–10R and effectively never fired. Now R-based.
3. **Gate A** — a 5m+1h consensus applied to *all* timeframes, so 15m/30m trades were vetoed by the 5-minute chart. Removed; `consensus` still computed as an ML feature.
4. **Risk cap** — see §2.
5. **Price action** — pin bars were undetectable (`upper <= body*0.5` unsatisfiable), every trend read as "range" (plateau bars counted as swings), and it evaluated the *forming* candle.
6. **PA double-filtered** — 41 setups killed by an H4 EMA gate that contradicted PA's own H4 structure read; 27 by an OBV gate already proven harmful for LG.
7. **Dead code** — 2nd-position logic unreachable (`place_trade` blocks any 2nd position); `GET /bot/scan` placed real trades, now dry-run.
8. **DB** — 771k constant "strategy is disabled" rows purged (archived to `archive_rejection_disabled_20260801.jsonl.gz`), 487 MB → 218 MB, WAL + indexes. A regime query went 282 ms → 0.91 ms.

---

## 4. Measured findings — do not re-derive

**Worked:**
- 4H EMA filter: removes a −0.267R cohort, keeps 92% of volume
- Trail trigger in R: +0.47R on pending trades, **zero change** on market trades (proven identical on 21,703)
- Gate A removal: total R +1021 → +1821
- Global direction breaker (consec=2, 3h): +$80 over 131 trades, 8 days better / 1 worse

**Measured and rejected:**
- **OTE / fib limit entries** — trades that never retrace are the winners. Filled: −0.43R baseline; unfilled: **+1.24R**. Baseline beat OTE at every expiry.
- **LSTM** (`build_lstm.py`) — AUC 0.494–0.534. The one symbol that beat the rule didn't survive a seed change (spread 0.23R).
- **Fast reversal detectors** — every one blocked trades averaging +0.25R or better.
- **Regime gate** — real gradient (efficiency ratio 0.31 → 0.09) but **no regime is negative**, so gating costs money.
- **Wider trail / partial exits** — U-shaped: tight (now) and very wide (5R) both work, 1–2×ATR is worst. 5R earns +25% but drops WR to 19.5% → 13-loss streaks → −49% at current sizing.

**Meta-model (`meta_gate.py`, `train_meta_model.py`, `meta_model.joblib`):**
- Walk-forward +0.125R, positive 4/4 folds; calibration transferred live (11.0% vs 10% target)
- Armed 08-13 on 6 trades (Spearman +0.886), **disarmed 08-14**
- Paired within-hour test, 15 hours: passed −0.292R vs blocked **+0.202R**, difference −0.494R, CI [−0.839, −0.149]
- Every threshold worse than no threshold. Score deciles are monotonically **inverted**
- Counter-evidence: 10 real trades give Spearman +0.576 (p=0.082) — but range-restricted, 6 of 10 above 0.5
- **Needs 30+ real scored trades before any re-arming**

---

## 5. Known open issues

- **PA duplicate entries** — same signal re-fires within minutes (15m candle stays valid, `cooldown_minutes=0`). Fired 3×, always doubling a loss. User chose to keep.
- **PA doesn't log ATR** → its trades can't be analysed by stop width.
- **Metals can't be risk-managed properly** — XAUUSD needs ~$1,900 for 2% risk at min lot, XAGUSD ~$2,270. They only "work" now via tight pending stops.
- **`is_trading_session`** has no weekday check for metals/forex → wasted weekend orders.
- **No auth on `/trading/*`** with `allow_origins=["*"]`. Tailscale-only now, but anything on the tailnet can start the bot or close all positions.
- **Meta model is static** — trained to Jul 23, nothing retrains or monitors drift. Watch the pass rate, not P&L.
- **Offline dataset is stale** (ends Jul 23) and `build_lstm.py` refetches M5 from MT5, so it silently loses samples if broker history rolls off.

---

## 6. The one lesson that kept repeating

**Under ~30 observations this system's numbers are not informative, however good the p-value.**

Three times a small sample pointed the wrong way:
- metals looked bad (told user to disable) → made all the profit
- pending entries looked bad (told user to disable) → made all the profit
- meta gate looked good (user armed it) → measurably harmful

Every large win came from **finding something broken**, not from adding intelligence.
The ML contributed +0.125R; the trail bug fix contributed +0.47R.

**Current stance: the config has changed a lot in two days. It needs a stable stretch
of 30–50 trades more than it needs another adjustment.**

---

## 7. Study scripts (all re-runnable)

`gate_a_removal_study.py` · `trail_trigger_study.py` · `exit_rule_study.py` ·
`exit_timing_study.py` · `ote_entry_study.py` · `reversal_detector_study.py` ·
`regime_classifier_study.py` · `price_action_backtest.py` · `pending_expiry_study.py` ·
`global_breaker_sim.py` · `meta_model_study.py` · `meta_blocked_study.py` · `train_meta_model.py`

Most join `gate_study_rows.csv` (written by `gate_a_removal_study.py`) with
`offline_dataset.sqlite` and replay M5 bars from MT5. Run that one first if the CSV is missing.
