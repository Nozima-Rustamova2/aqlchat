from cryptography.fernet import Fernet
from sqlalchemy import text

from app.db.crypto import EncryptedString, _get_fernet
from app.db.models import Merchant


def test_get_fernet_uses_configured_key():
    fernet = _get_fernet()
    assert isinstance(fernet, Fernet)


def test_process_bind_param_encrypts_to_different_ciphertext_each_time():
    # Fernet is randomized (fresh IV per call) - this is the exact
    # property that makes a DB-level unique constraint on the encrypted
    # column meaningless (see app/db/models.py's telegram_bot_id comment).
    col = EncryptedString()
    ciphertext_a = col.process_bind_param("123:ABC", dialect=None)
    ciphertext_b = col.process_bind_param("123:ABC", dialect=None)
    assert ciphertext_a != ciphertext_b


def test_round_trip_returns_original_plaintext():
    col = EncryptedString()
    ciphertext = col.process_bind_param("123:ABC-token", dialect=None)
    assert col.process_result_value(ciphertext, dialect=None) == "123:ABC-token"


def test_none_passes_through_unchanged():
    col = EncryptedString()
    assert col.process_bind_param(None, dialect=None) is None
    assert col.process_result_value(None, dialect=None) is None


def test_merchant_token_round_trips_through_the_orm(db_session):
    merchant = Merchant(
        name="crypto-roundtrip-test",
        telegram_bot_token="999999:PLAINTEXT-FOR-ROUNDTRIP-TEST",
        webhook_secret="whatever",
    )
    db_session.add(merchant)
    db_session.flush()
    db_session.expire(merchant)  # force a real reload from the DB, not the identity map

    reloaded = db_session.get(Merchant, merchant.id)
    assert reloaded.telegram_bot_token == "999999:PLAINTEXT-FOR-ROUNDTRIP-TEST"

    raw_ciphertext = db_session.execute(
        text("SELECT telegram_bot_token FROM merchants WHERE id = :id"), {"id": merchant.id}
    ).scalar()
    assert raw_ciphertext != "999999:PLAINTEXT-FOR-ROUNDTRIP-TEST"
    assert "PLAINTEXT" not in raw_ciphertext
