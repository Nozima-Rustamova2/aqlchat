import uuid

from app.llm.budget import DAILY_CUSTOMER_CALL_LIMIT, check_and_consume_budget
from app.llm.cache import get_cached_answer, set_cached_answer
from app.redis_client import get_redis


def test_budget_allows_calls_under_the_limit():
    redis_client = get_redis()
    merchant_id = str(uuid.uuid4())
    customer_id = str(uuid.uuid4())

    assert check_and_consume_budget(redis_client, merchant_id, customer_id) is True


def test_budget_blocks_customer_after_daily_limit():
    redis_client = get_redis()
    merchant_id = str(uuid.uuid4())
    customer_id = str(uuid.uuid4())

    for _ in range(DAILY_CUSTOMER_CALL_LIMIT):
        assert check_and_consume_budget(redis_client, merchant_id, customer_id) is True

    assert check_and_consume_budget(redis_client, merchant_id, customer_id) is False


def test_budget_is_scoped_per_customer():
    # One customer exhausting their cap shouldn't block a different
    # customer on the same merchant.
    redis_client = get_redis()
    merchant_id = str(uuid.uuid4())
    customer_a = str(uuid.uuid4())
    customer_b = str(uuid.uuid4())

    for _ in range(DAILY_CUSTOMER_CALL_LIMIT):
        check_and_consume_budget(redis_client, merchant_id, customer_a)
    assert check_and_consume_budget(redis_client, merchant_id, customer_a) is False

    assert check_and_consume_budget(redis_client, merchant_id, customer_b) is True


def test_cache_miss_returns_none():
    redis_client = get_redis()
    merchant_id = str(uuid.uuid4())
    assert get_cached_answer(redis_client, merchant_id, "narxi qancha") is None


def test_cache_set_then_get_returns_the_answer():
    redis_client = get_redis()
    merchant_id = str(uuid.uuid4())
    set_cached_answer(redis_client, merchant_id, "narxi qancha", "250000 so'm")
    assert get_cached_answer(redis_client, merchant_id, "narxi qancha") == "250000 so'm"


def test_cache_is_scoped_per_merchant():
    redis_client = get_redis()
    merchant_a = str(uuid.uuid4())
    merchant_b = str(uuid.uuid4())
    set_cached_answer(redis_client, merchant_a, "narxi qancha", "answer for A")
    assert get_cached_answer(redis_client, merchant_b, "narxi qancha") is None
