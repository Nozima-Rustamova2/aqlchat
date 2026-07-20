from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from app.auth import service
from app.auth.dependencies import get_current_merchant
from app.auth.schemas import LoginRequest, MerchantOut, SignupRequest, UpdateLanguageRequest, VerifyRequest
from app.auth.service import SESSION_COOKIE_NAME, SESSION_TTL_DAYS
from app.db.models import Merchant
from app.db.session import get_db

router = APIRouter(prefix="/auth", tags=["auth"])


def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        SESSION_COOKIE_NAME,
        token,
        max_age=SESSION_TTL_DAYS * 24 * 3600,
        httponly=True,
        samesite="lax",
        secure=True,
    )


def _merchant_out(merchant: Merchant) -> MerchantOut:
    return MerchantOut(
        id=str(merchant.id),
        owner_name=merchant.owner_name,
        email=merchant.email,
        phone_number=merchant.phone_number,
        webhook_slug=merchant.webhook_slug,
        telegram_connected=merchant.telegram_bot_id is not None,
        instagram_connected=merchant.instagram_user_id is not None,
        vertical=merchant.vertical,
        telegram_bot_username=(merchant.profile or {}).get("telegram_bot_username"),
        ui_language=merchant.ui_language,
    )


@router.post("/signup")
def signup(body: SignupRequest, db: Session = Depends(get_db)) -> dict:
    try:
        service.start_signup(db, body.owner_name, body.email, body.phone_number, body.password)
    except service.AlreadyRegistered:
        raise HTTPException(status_code=409, detail="this email is already registered - log in instead")
    except service.ResendTooSoon:
        raise HTTPException(status_code=429, detail="a code was just sent - check your inbox or wait a bit")
    return {"ok": True}


@router.post("/login")
def login(body: LoginRequest, response: Response, db: Session = Depends(get_db)) -> MerchantOut:
    try:
        merchant, token = service.login(db, body.email, body.password)
    except service.InvalidCredentials:
        raise HTTPException(status_code=401, detail="wrong email or password")

    _set_session_cookie(response, token)
    return _merchant_out(merchant)


@router.post("/verify")
def verify(body: VerifyRequest, response: Response, db: Session = Depends(get_db)) -> MerchantOut:
    try:
        merchant, token = service.verify_code(db, body.email, body.code)
    except service.InvalidCode:
        raise HTTPException(status_code=400, detail="invalid or expired code")

    _set_session_cookie(response, token)
    return _merchant_out(merchant)


@router.post("/logout")
def logout(request: Request, response: Response, db: Session = Depends(get_db)) -> dict:
    # Revoke the WebSession row itself, not just the cookie - otherwise a
    # copy of the cookie value made before logout would still authenticate.
    service.revoke_session(db, request.cookies.get(SESSION_COOKIE_NAME, ""))
    response.delete_cookie(SESSION_COOKIE_NAME)
    return {"ok": True}


@router.get("/me")
def me(merchant: Merchant = Depends(get_current_merchant)) -> MerchantOut:
    return _merchant_out(merchant)


@router.patch("/me")
def update_me(
    body: UpdateLanguageRequest,
    db: Session = Depends(get_db),
    merchant: Merchant = Depends(get_current_merchant),
) -> MerchantOut:
    merchant.ui_language = body.ui_language
    db.commit()
    db.refresh(merchant)
    return _merchant_out(merchant)
