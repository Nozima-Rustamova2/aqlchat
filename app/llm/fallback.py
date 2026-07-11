"""Swappable LLM fallback interface - the last layer in the pipeline
(rule -> FAQ -> intent -> LLM -> human handoff), reached only when
everything above it misses.

Locked-in decision (2026-07-11): ship one provider (Claude, see
app/llm/claude_provider.py) behind this interface now rather than running
a Claude/OpenAI/YandexGPT bake-off first - there's no real fallback
traffic to bake off against before pilot. Every fallback call is logged
with its full context (app/llm/logging.py) specifically so the provider
comparison can be run later as a replay against real misses, instead of
a synthetic test set.

`answerable` is a first-class field, not something inferred from response
text: the fallback's job is "answer only if grounded in this merchant's
own data," not "always produce an answer." See app/llm/context.py for
what's in scope (FAQs + products only - no outside knowledge) and
app/llm/service.py for what happens when answerable is False (falls
through to layer 5 human handoff, same as a budget-exceeded or cache-miss
non-answer).
"""

from dataclasses import dataclass
from typing import Protocol


@dataclass
class FallbackContext:
    query: str
    detected_language: str
    faqs: list[dict]
    products: list[dict]


@dataclass
class FallbackResult:
    answerable: bool
    answer: str | None
    provider_name: str


class LLMProvider(Protocol):
    def generate(self, context: FallbackContext) -> FallbackResult: ...
