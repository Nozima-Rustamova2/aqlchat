"""Gemini implementation of the llm_first pipeline mode's answer layer -
the parallel-stack counterpart to app/llm/claude_provider.py (Flag 7 of
the mode-switch plan: this does NOT extend claude_provider.py/fallback.py,
so the layered-mode Claude path is provably untouched).

Model choice: gemini-flash-latest, confirmed live 2026-07-12 against the
configured API key (resolves to gemini-3.5-flash) - low latency/cost fits
a customer-facing reply better than a reasoning-heavy model, same
rationale claude_provider.py gives for using "medium" effort instead of
"high".
"""

import json
from dataclasses import dataclass
from pathlib import Path

from google import genai
from google.genai import types

from app.config import settings

MODEL = "gemini-flash-latest"

# Milliseconds - SDK-level half of the two-layer timeout guarantee (see
# app/llm/answer.py's _TIMEOUT_SECONDS thread-pool wrapper for the other,
# customer-facing half, so the guarantee doesn't depend solely on this SDK
# parameter being honored). Kept a bit above that 12s so this backstop
# never fires before the thread-pool wrapper already would - and can't go
# below 10s regardless: a live call confirmed the API rejects any
# http_options.timeout under 10s outright with 400 INVALID_ARGUMENT
# ("Manually set deadline 8s is too short").
_TIMEOUT_MS = 15000

_PROMPT_TEMPLATE = (Path(__file__).parent / "prompts" / "grounded_answer_v1.md").read_text(encoding="utf-8")

_ANSWER_SCHEMA = {
    "type": "object",
    "properties": {
        "answerable": {
            "type": "boolean",
            "description": "True only if the context below actually contains the answer.",
        },
        "reply": {
            "anyOf": [{"type": "string"}, {"type": "null"}],
            "description": "Reply text - the real answer if answerable, else a short holding line. Never null.",
        },
        "matched_product_ref": {
            "anyOf": [{"type": "string"}, {"type": "null"}],
            "description": "id of the one product this reply is specifically about, else null.",
        },
    },
    "required": ["answerable", "reply", "matched_product_ref"],
    "additionalProperties": False,
}


@dataclass
class AnswerContext:
    query: str
    detected_language: str
    shop_name: str
    profile: dict
    products: list[dict]  # id, name, price, currency, description, price_status
    faqs: list[dict]  # question, answer
    last_matched_product: dict | None  # id, name, price, currency, price_status
    history: list[dict]  # direction ("in"/"out"), text


@dataclass
class AnswerResult:
    answerable: bool
    reply: str | None
    matched_product_ref: str | None
    provider_name: str


def _build_system_prompt(context: AnswerContext) -> str:
    product_block = (
        "\n".join(
            f"- id={p['id']} {p['name']}: "
            + (f"{p['price']} {p['currency']}" if p["price_status"] == "set" else "price not yet set")
            + f" - {p['description'] or 'no description'}"
            for p in context.products
        )
        or "(no matching products found)"
    )
    faq_block = (
        "\n".join(f"- Q: {faq['question']}\n  A: {faq['answer']}" for faq in context.faqs) or "(none configured)"
    )
    profile_block = json.dumps(context.profile, ensure_ascii=False) if context.profile else "(not configured)"
    last_product_block = (
        f"id={context.last_matched_product['id']} {context.last_matched_product['name']}"
        if context.last_matched_product
        else "(none)"
    )
    history_block = (
        "\n".join(f"{'Customer' if turn['direction'] == 'in' else 'You'}: {turn['text']}" for turn in context.history)
        or "(no prior turns)"
    )
    return (
        _PROMPT_TEMPLATE.format(shop_name=context.shop_name)
        + f"\n\nShop profile:\n{profile_block}\n\n"
        f"Products (top matches for this question):\n{product_block}\n\n"
        f"FAQs:\n{faq_block}\n\n"
        f"Product last discussed in this conversation: {last_product_block}\n\n"
        f"Recent conversation:\n{history_block}\n\n"
        f"Reply in this language: {context.detected_language}"
    )


class GeminiProvider:
    def __init__(self) -> None:
        self._client = genai.Client(api_key=settings.gemini_api_key)

    def generate(self, context: AnswerContext) -> AnswerResult:
        response = self._client.models.generate_content(
            model=MODEL,
            contents=context.query,
            config=types.GenerateContentConfig(
                system_instruction=_build_system_prompt(context),
                response_mime_type="application/json",
                response_json_schema=_ANSWER_SCHEMA,
                http_options=types.HttpOptions(timeout=_TIMEOUT_MS),
            ),
        )
        parsed = json.loads(response.text)
        return AnswerResult(
            answerable=parsed["answerable"],
            reply=parsed["reply"],
            matched_product_ref=parsed["matched_product_ref"],
            provider_name=f"gemini:{MODEL}",
        )
