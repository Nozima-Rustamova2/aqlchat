"""Pipeline-mode routing (app/telegram/webhook.py) - the core of the
LLM-first mode-switch: a merchant's pipeline_mode decides whether the
layered NLP pipeline (keyword/FAQ/intent) is reachable at all. Routing
lives inside receive_update, which commits for real, so these use the
routed_session/routed_merchant/client fixtures (tests/conftest.py)
rather than the plain db_session fixture.
"""

import app.telegram.webhook as webhook_module
from app.db.models import Conversation, Customer, MerchantAdmin, Message
from app.telegram.webhook import _resolution_path_for
from tests.conftest import make_flow


def _text_update(update_id: int, chat_id: int, user_id: int, text: str) -> dict:
    return {
        "update_id": update_id,
        "message": {
            "message_id": update_id,
            "date": 0,
            "chat": {"id": chat_id, "type": "private"},
            "from": {"id": user_id, "is_bot": False, "first_name": "Test"},
            "text": text,
        },
    }


def test_llm_first_never_calls_the_layered_pipeline(client, routed_session, routed_merchant, monkeypatch):
    # routed_merchant defaults to pipeline_mode="llm_first" (the
    # migration default) - no explicit setup needed.
    calls: list[str] = []

    def _spy(name):
        def _raise(*args, **kwargs):
            calls.append(name)
            raise AssertionError(f"{name} should never be called in llm_first mode")

        return _raise

    monkeypatch.setattr(webhook_module, "match_flow", _spy("match_flow"))
    monkeypatch.setattr(webhook_module, "match_faq", _spy("match_faq"))
    monkeypatch.setattr(webhook_module, "classify_intent", _spy("classify_intent"))
    monkeypatch.setattr(webhook_module, "get_fallback_reply", _spy("get_fallback_reply"))

    response = client.post(
        f"/telegram/webhook/tenant/{routed_merchant.webhook_slug}",
        json=_text_update(1, 900001, 900001, "salom, narxi qancha?"),
        headers={"X-Telegram-Bot-Api-Secret-Token": routed_merchant.webhook_secret},
    )

    assert response.status_code == 200
    assert calls == []


def test_llm_first_stub_hands_off_and_flags_needs_human(client, routed_session, routed_merchant):
    admin = MerchantAdmin(merchant_id=routed_merchant.id, telegram_user_id=900002)
    routed_session.add(admin)
    routed_session.flush()

    response = client.post(
        f"/telegram/webhook/tenant/{routed_merchant.webhook_slug}",
        json=_text_update(2, 900003, 900003, "salom"),
        headers={"X-Telegram-Bot-Api-Secret-Token": routed_merchant.webhook_secret},
    )
    assert response.status_code == 200

    customer = routed_session.query(Customer).filter_by(telegram_user_id=900003).first()
    conversation = routed_session.query(Conversation).filter_by(customer_id=customer.id).first()
    assert conversation.needs_human is True

    outbound = routed_session.query(Message).filter_by(conversation_id=conversation.id, direction="out").first()
    assert outbound.response_source == "handoff"
    assert outbound.resolution_path == "handoff"


def test_layered_mode_still_runs_the_full_pipeline(client, routed_session, routed_merchant):
    routed_merchant.pipeline_mode = "layered"
    routed_session.add(routed_merchant)
    routed_session.flush()
    make_flow(routed_session, routed_merchant.id, "greeting", ["salom"], "Salom! Xush kelibsiz.")

    response = client.post(
        f"/telegram/webhook/tenant/{routed_merchant.webhook_slug}",
        json=_text_update(3, 900004, 900004, "salom"),
        headers={"X-Telegram-Bot-Api-Secret-Token": routed_merchant.webhook_secret},
    )
    assert response.status_code == 200

    customer = routed_session.query(Customer).filter_by(telegram_user_id=900004).first()
    conversation = routed_session.query(Conversation).filter_by(customer_id=customer.id).first()
    outbound = routed_session.query(Message).filter_by(conversation_id=conversation.id, direction="out").first()

    assert outbound.response_source == "rule"
    assert outbound.resolution_path == "deterministic"
    assert outbound.raw_text == "Salom! Xush kelibsiz."


def test_resolution_path_mapping():
    assert _resolution_path_for("rule") == "deterministic"
    assert _resolution_path_for("faq") == "deterministic"
    assert _resolution_path_for("intent") == "deterministic"
    assert _resolution_path_for("image") == "deterministic"
    assert _resolution_path_for("forward_match") == "deterministic"
    assert _resolution_path_for("post_link_match") == "deterministic"
    assert _resolution_path_for("llm") == "llm"
    assert _resolution_path_for("handoff") == "handoff"
    assert _resolution_path_for("human") == "handoff"
    assert _resolution_path_for(None) is None
    assert _resolution_path_for("something_unrecognized") == "handoff"
