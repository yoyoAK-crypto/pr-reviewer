import hashlib
import hmac
import logging
import os
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import OAuth2PasswordBearer

from app.repositories.accounts import get_account

logger = logging.getLogger(__name__)
router = APIRouter()
oauth2 = OAuth2PasswordBearer(tokenUrl="login")

SECRET_KEY = os.environ["JWT_SECRET"]
ALGORITHM = "HS256"
TOKEN_TTL = timedelta(hours=1)


def hash_password(password: str, salt: bytes) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 200_000)


def _verify_password(account, password: str) -> bool:
    candidate = hash_password(password, account.salt)
    return hmac.compare_digest(candidate, account.password_hash)


def create_token(user_id: int) -> str:
    expires = datetime.now(timezone.utc) + TOKEN_TTL
    return jwt.encode({"sub": str(user_id), "exp": expires}, SECRET_KEY, algorithm=ALGORITHM)


def current_user_id(token: str = Depends(oauth2)) -> int:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="invalid token")
    return int(payload["sub"])


@router.post("/login")
def login(username: str, password: str) -> dict:
    account = get_account(username)
    if account is None:
        raise HTTPException(status_code=401, detail="invalid credentials")
    if not _verify_password(account, password):
        logger.warning("failed login for user=%s", username)
        raise HTTPException(status_code=401, detail="invalid credentials")
    logger.info("login ok user=%s", username)
    return {"access_token": create_token(account.id), "token_type": "bearer"}
