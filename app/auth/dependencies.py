from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.auth.service import SESSION_COOKIE_NAME, resolve_session
from app.db.models import Merchant
from app.db.session import get_db


def get_current_merchant(request: Request, db: Session = Depends(get_db)) -> Merchant:
    token = request.cookies.get(SESSION_COOKIE_NAME, "")
    merchant = resolve_session(db, token)
    if merchant is None:
        raise HTTPException(status_code=401, detail="not authenticated")
    return merchant
