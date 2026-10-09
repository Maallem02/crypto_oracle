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
