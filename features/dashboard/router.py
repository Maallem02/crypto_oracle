"""Endpoints du tableau de bord de l'app — lecture seule."""
from fastapi import APIRouter, HTTPException

from features.dashboard import service

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("/summary")
def dashboard_summary():
    """Compte, performance par période / bot / symbole, courbe du solde, risque."""
    try:
        return service.summary()
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"dashboard unavailable: {e}")


@router.get("/news")
def dashboard_news():
    """Calendrier économique des devises tradées + titres crypto / or / dollar."""
    try:
        return service.news()
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"news unavailable: {e}")


@router.get("/expectancy")
def dashboard_expectancy(days: int = 0):
    """Espérance réelle par bot (R par trade, taux de gain vs seuil de rentabilité,
    plage à 90 %, frais, risque par trade). days = 0 : tout l'historique."""
    try:
        return service.expectancy(days)
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"expectancy unavailable: {e}")
