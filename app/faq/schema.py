"""YAML schema for merchant-defined FAQs. Human-editable, mirrors
app/flows/schema.py's shape. One `question` per entry (matching is
cross-lingual - see retrieval.py - so it only needs to be authored once,
in whichever language the merchant prefers), but `responses` is
per-language since the reply shown to the customer should be in their own
language.
"""

from pydantic import BaseModel


class FaqDefinition(BaseModel):
    question: str
    responses: dict[str, str]


class FaqFile(BaseModel):
    faqs: list[FaqDefinition]
