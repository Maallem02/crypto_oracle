"""Bot Daily Trend — troisième bot indépendant (2026-10-09).

Suivi de tendance journalier (canal de Donchian 20/10, achats seulement) sur
BTC et ETH, validé sur l'historique Exness 2018 -> 2026 et Binance 2023 -> 2026
(trend_following_study.py, trend_robustness_study.py).

    MT5      magic 234200 (DT_MAGIC), commentaire "DT long"
    journal  table dt_log
    état     features/daily_trend/dt_state.json (marche/arrêt, réglages)
    API      /trading/dt/status | start | stop | settings | history
"""
