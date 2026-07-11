from app.db.models import Conversation, Customer
from app.handoff.service import escalate, escalation_reply_text, handle_admin_command


def _make_conversation(db_session, merchant_id, telegram_user_id: int) -> tuple[Customer, Conversation]:
    customer = Customer(merchant_id=merchant_id, telegram_user_id=telegram_user_id)
    db_session.add(customer)
    db_session.flush()
    conversation = Conversation(merchant_id=merchant_id, customer_id=customer.id)
    db_session.add(conversation)
    db_session.flush()
    return customer, conversation


def test_escalate_sets_needs_human(db_session, test_merchant):
    customer, conversation = _make_conversation(db_session, test_merchant.id, 555001)
    assert conversation.needs_human is False

    escalate(db_session, test_merchant, customer, conversation, "salom, yordam kerak", reason="unhandled")

    assert conversation.needs_human is True


def test_escalate_without_admin_chat_id_does_not_raise(db_session, test_merchant):
    # test_merchant fixture has no admin_chat_id set - escalation should
    # still flip the flag, just skip the notification.
    assert test_merchant.admin_chat_id is None
    customer, conversation = _make_conversation(db_session, test_merchant.id, 555002)

    escalate(db_session, test_merchant, customer, conversation, "narxi qancha", reason="unhandled")

    assert conversation.needs_human is True


def test_escalation_reply_text_uzbek():
    assert "sotuvchi" in escalation_reply_text("uz")


def test_escalation_reply_text_russian():
    assert "продавцу" in escalation_reply_text("ru")


def test_escalation_reply_text_falls_back_to_uzbek_for_unknown_language():
    assert escalation_reply_text("mixed") == escalation_reply_text("uz")
    assert escalation_reply_text(None) == escalation_reply_text("uz")


def test_admin_command_unrecognized_text_returns_none(db_session, test_merchant):
    assert handle_admin_command(db_session, test_merchant, "hello there") is None


def test_admin_release_with_no_matching_conversation(db_session, test_merchant):
    result = handle_admin_command(db_session, test_merchant, "/release 999999999")
    assert result is not None
    assert "No conversation" in result.confirmation_text


def test_admin_release_clears_needs_human(db_session, test_merchant):
    customer, conversation = _make_conversation(db_session, test_merchant.id, 555003)
    conversation.needs_human = True
    db_session.flush()

    result = handle_admin_command(db_session, test_merchant, f"/release {customer.telegram_user_id}")

    assert "Released" in result.confirmation_text
    assert conversation.needs_human is False


def test_admin_reply_to_unknown_customer(db_session, test_merchant):
    result = handle_admin_command(db_session, test_merchant, "/reply 999999999 salom")
    assert result is not None
    assert "No customer" in result.confirmation_text


def test_admin_reply_to_known_customer_attempts_send(db_session, test_merchant):
    # test_merchant's bot token is fake, so the real Telegram call 401s -
    # same pattern as the flow-executor/FAQ live verification. Confirms
    # the code path actually runs a real HTTP call rather than silently
    # no-op'ing, and fails gracefully instead of raising.
    customer, conversation = _make_conversation(db_session, test_merchant.id, 555004)
    conversation.needs_human = True
    db_session.flush()

    result = handle_admin_command(db_session, test_merchant, f"/reply {customer.telegram_user_id} Salom, javob beryapman")

    assert result is not None
    assert "Failed to send" in result.confirmation_text
