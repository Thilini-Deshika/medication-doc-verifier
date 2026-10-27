from fastapi import HTTPException, Request, status

from app.config import get_settings
from app.db import SessionLocal
from app.models import User
from app.security import decode_token


def current_user(request: Request) -> User:
    """Authenticate from the session cookie.

    Uses its own short-lived session so long-lived responses (SSE) do not hold a
    pooled connection open for the duration of the stream.
    """
    token = request.cookies.get(get_settings().cookie_name)
    user_id = decode_token(token) if token else None
    if user_id is not None:
        with SessionLocal() as db:
            user = db.get(User, user_id)
            if user is not None:
                return user
    raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
