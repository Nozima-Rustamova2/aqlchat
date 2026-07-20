"""Website signup/login (app/auth/) - goes through real FastAPI routes
that commit for real, so these use the routed_session/client fixtures
(see tests/conftest.py) rather than the plain db_session fixture.
"""

import pytest

from app.auth import service as auth_service
from app.db.models import EmailVerificationCode, Merchant, WebSession


@pytest.fixture
def captured_codes(monkeypatch):
    sent = []

    def _fake_send(email, code):
        sent.append((email, code))

    monkeypatch.setattr(auth_service, "send_verification_code", _fake_send)
    return sent


DEFAULT_PASSWORD = "correct-horse-battery"


def _signup(client, email="nozima@example.com", owner_name="Nozima", phone="+998901234567", password=DEFAULT_PASSWORD):
    return client.post(
        "/auth/signup",
        json={"owner_name": owner_name, "email": email, "phone_number": phone, "password": password},
    )


def test_signup_creates_unverified_merchant_and_sends_code(client, routed_session, captured_codes):
    response = _signup(client)
    assert response.status_code == 200

    merchant = routed_session.query(Merchant).filter_by(email="nozima@example.com").one()
    assert merchant.owner_name == "Nozima"
    assert merchant.phone_number == "+998901234567"
    assert merchant.email_verified_at is None
    assert merchant.name is None  # shop name not collected at signup
    assert merchant.telegram_bot_token is None
    assert merchant.password_hash is not None
    assert DEFAULT_PASSWORD not in merchant.password_hash

    assert len(captured_codes) == 1
    assert captured_codes[0][0] == "nozima@example.com"
    assert len(captured_codes[0][1]) == 6

    code_row = routed_session.query(EmailVerificationCode).filter_by(merchant_id=merchant.id).one()
    assert code_row.consumed_at is None
    assert code_row.attempts == 0


def test_signup_twice_unverified_resends_instead_of_erroring(client, routed_session, captured_codes):
    _signup(client)
    # cooldown blocks an immediate resend
    response = _signup(client)
    assert response.status_code == 429


def test_verify_wrong_code_returns_400_and_increments_attempts(client, routed_session, captured_codes):
    _signup(client)
    response = client.post("/auth/verify", json={"email": "nozima@example.com", "code": "000000"})
    assert response.status_code == 400

    merchant = routed_session.query(Merchant).filter_by(email="nozima@example.com").one()
    code_row = routed_session.query(EmailVerificationCode).filter_by(merchant_id=merchant.id).one()
    assert code_row.attempts == 1


def test_verify_correct_code_marks_verified_and_sets_session_cookie(client, routed_session, captured_codes):
    _signup(client)
    email, code = captured_codes[0]

    response = client.post("/auth/verify", json={"email": email, "code": code})
    assert response.status_code == 200
    body = response.json()
    assert body["email"] == email
    assert body["owner_name"] == "Nozima"
    assert body["telegram_connected"] is False
    assert body["instagram_connected"] is False
    assert "webhook_slug" in body

    merchant = routed_session.query(Merchant).filter_by(email=email).one()
    assert merchant.email_verified_at is not None

    assert "dukan_session" in response.cookies
    session_row = routed_session.query(WebSession).filter_by(merchant_id=merchant.id).one()
    assert session_row.revoked_at is None


def test_verify_same_code_twice_fails_second_time(client, routed_session, captured_codes):
    _signup(client)
    email, code = captured_codes[0]
    first = client.post("/auth/verify", json={"email": email, "code": code})
    assert first.status_code == 200

    second = client.post("/auth/verify", json={"email": email, "code": code})
    assert second.status_code == 400


def test_me_requires_session_cookie(client, routed_session):
    response = client.get("/auth/me")
    assert response.status_code == 401


def test_me_with_valid_session_returns_merchant(client, routed_session, captured_codes):
    _signup(client)
    email, code = captured_codes[0]
    verify_response = client.post("/auth/verify", json={"email": email, "code": code})
    token = verify_response.cookies["dukan_session"]

    client.cookies.set("dukan_session", token)
    me_response = client.get("/auth/me")
    assert me_response.status_code == 200
    assert me_response.json()["email"] == email


def test_logout_revokes_session(client, routed_session, captured_codes):
    _signup(client)
    email, code = captured_codes[0]
    verify_response = client.post("/auth/verify", json={"email": email, "code": code})
    token = verify_response.cookies["dukan_session"]
    client.cookies.set("dukan_session", token)

    logout_response = client.post("/auth/logout")
    assert logout_response.status_code == 200

    client.cookies.set("dukan_session", token)
    me_response = client.get("/auth/me")
    assert me_response.status_code == 401


def test_login_unknown_email_returns_401(client, routed_session):
    response = client.post("/auth/login", json={"email": "nobody@example.com", "password": "whatever123"})
    assert response.status_code == 401


def test_login_before_verification_returns_401(client, routed_session, captured_codes):
    _signup(client)
    response = client.post("/auth/login", json={"email": "nozima@example.com", "password": DEFAULT_PASSWORD})
    assert response.status_code == 401


def test_login_wrong_password_returns_401(client, routed_session, captured_codes):
    _signup(client)
    email, code = captured_codes[0]
    client.post("/auth/verify", json={"email": email, "code": code})

    response = client.post("/auth/login", json={"email": email, "password": "not-the-password"})
    assert response.status_code == 401


def test_login_after_verification_succeeds_with_password(client, routed_session, captured_codes):
    _signup(client)
    email, code = captured_codes[0]
    client.post("/auth/verify", json={"email": email, "code": code})

    response = client.post("/auth/login", json={"email": email, "password": DEFAULT_PASSWORD})
    assert response.status_code == 200
    assert response.json()["email"] == email
    assert "dukan_session" in response.cookies
    # Login doesn't send a new code - it's a direct password check now.
    assert len(captured_codes) == 1


def test_dashboard_page_serves_html(client):
    response = client.get("/dashboard")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "Dukan AI" in response.text


def test_dashboard_settings_page_serves_html(client):
    response = client.get("/dashboard/settings")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "Sozlamalar" in response.text


def test_signup_page_serves_html(client):
    response = client.get("/signup")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "Dukan AI" in response.text
