"""Stratégie Volume Profile — ajoutée le 2026-09-24, en PARALLÈLE de la
stratégie SMC existante, pour être testée séparément.

    profile.py   calcul du profil (POC / VAH / VAL) d'une session terminée
    strategy.py  les trois setups : rebond POC, réintégration, cassure
    runner.py    branchement dans la boucle de scan, ordres, journal vp_log

Tout ce qui vient d'ici est identifiable sans ambiguïté :
    MT5      magic 234100 (VP_MAGIC) au lieu de 234000, commentaire "VP-..."
    base     table vp_log, jamais scalp_log (qui alimente les pauses, le
             breaker directionnel et le ML de la stratégie SMC)
"""
