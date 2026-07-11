"""Claude implementation of the LLMProvider interface (app/llm/fallback.py).

Model choice: claude-opus-4-8, the default per current guidance - not
downgraded to Sonnet/Haiku for cost. This is deliberate here, not just a
default followed blindly: by the time a message reaches this layer, the
rule/FAQ/intent layers have already absorbed the cheap, high-volume
traffic, so call volume at this layer should be low and answer quality
(not reasoning depth) is what determines whether "answerable: false"
correctly avoids a hallucinated price or policy - worth paying for.

effort is explicitly set to "medium" rather than left at the "high"
default: this is a bounded grounded-QA task (answer from the merchant's
own FAQ/product text or say so), not open-ended reasoning, so the extra
depth "high"/"xhigh" buys elsewhere isn't needed here. thinking is left
unset (Opus 4.8 runs without thinking when omitted) for the same reason -
latency and cost matter for a customer-facing reply, and this task
doesn't need multi-step reasoning.
"""

import json

import anthropic

from app.llm.fallback import FallbackContext, FallbackResult

MODEL = "claude-opus-4-8"

_ANSWER_SCHEMA = {
    "type": "object",
    "properties": {
        "answerable": {
            "type": "boolean",
            "description": "True only if the merchant FAQs/products below actually contain the answer.",
        },
        "answer": {
            "anyOf": [{"type": "string"}, {"type": "null"}],
            "description": "The reply text if answerable, else null. Must not use any information outside what's given.",
        },
    },
    "required": ["answerable", "answer"],
    "additionalProperties": False,
}


def _build_system_prompt(context: FallbackContext) -> str:
    faq_block = (
        "\n".join(f"- Q: {faq['question']}\n  A: {faq['answer']}" for faq in context.faqs) or "(none configured)"
    )
    product_block = (
        "\n".join(
            f"- {p['name']}: {p['price']} {p['currency']} - {p['description'] or 'no description'}"
            for p in context.products
        )
        or "(none configured)"
    )
    return (
        "You are a customer support assistant for a small online shop's Telegram bot. "
        "Answer the customer's question using ONLY the merchant information listed below. "
        "Do not use outside knowledge, and do not guess at prices, stock, delivery terms, "
        "or policies that aren't explicitly stated here. If the question cannot be answered "
        "from this information, set answerable to false and leave answer null - the "
        "conversation will be handed to the human seller instead of getting a guessed answer.\n\n"
        f"Reply in this language: {context.detected_language}\n\n"
        f"Merchant FAQs:\n{faq_block}\n\n"
        f"Merchant products:\n{product_block}"
    )


class ClaudeProvider:
    def __init__(self) -> None:
        self._client = anthropic.Anthropic()

    def generate(self, context: FallbackContext) -> FallbackResult:
        response = self._client.messages.create(
            model=MODEL,
            max_tokens=1024,
            system=_build_system_prompt(context),
            output_config={"format": {"type": "json_schema", "schema": _ANSWER_SCHEMA}, "effort": "medium"},
            messages=[{"role": "user", "content": context.query}],
        )

        if response.stop_reason == "refusal":
            return FallbackResult(answerable=False, answer=None, provider_name=f"claude:{MODEL}")

        text = next(block.text for block in response.content if block.type == "text")
        parsed = json.loads(text)
        return FallbackResult(
            answerable=parsed["answerable"], answer=parsed["answer"], provider_name=f"claude:{MODEL}"
        )
