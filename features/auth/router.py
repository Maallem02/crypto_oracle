from fastapi import APIRouter, HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from features.auth.models import RegisterRequest, LoginRequest, TokenResponse
from core.security import hash_password, verify_password, create_access_token, decode_token
from core.database import get_db

router = APIRouter(prefix="/auth", tags=["auth"])
security = HTTPBearer()

@router.post("/register", response_model=TokenResponse)
def register(req: RegisterRequest):
    db = get_db()
    # Vérifier si email déjà utilisé
    existing = db.execute(
        "SELECT id FROM users WHERE email = ?", (req.email,)
    ).fetchone()
    if existing:
        db.close()
        raise HTTPException(status_code=400, detail="Email déjà utilisé")

    hashed = hash_password(req.password)
    db.execute(
        "INSERT INTO users (email, username, hashed_password) VALUES (?, ?, ?)",
        (req.email, req.username, hashed)
    )
    db.commit()
    db.close()

    token = create_access_token({"sub": req.email, "username": req.username})
    return TokenResponse(access_token=token, username=req.username, email=req.email)

@router.post("/login", response_model=TokenResponse)
def login(req: LoginRequest):
    db = get_db()
    user = db.execute(
        "SELECT * FROM users WHERE email = ?", (req.email,)
    ).fetchone()
    db.close()

    if not user or not verify_password(req.password, user["hashed_password"]):
        raise HTTPException(status_code=401, detail="Email ou mot de passe incorrect")

    token = create_access_token({"sub": user["email"], "username": user["username"]})
    return TokenResponse(access_token=token, username=user["username"], email=user["email"])

def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    payload = decode_token(credentials.credentials)
    if not payload:
        raise HTTPException(status_code=401, detail="Token invalide ou expiré")
    return payload
