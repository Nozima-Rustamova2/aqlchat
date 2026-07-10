"""YAML schema for merchant-defined flows (rule-based triggers, the free/instant
layer that should absorb traffic before FAQ retrieval or the intent classifier
run - see aqlchat-phase1-plan memory for the layering rationale).

Deliberately just keyword-trigger, text-response for Phase 1. Not a visual
flow builder - a human-editable YAML file per merchant.
"""

from pydantic import BaseModel


class FlowResponse(BaseModel):
    type: str = "text"
    text: str


class FlowDefinition(BaseModel):
    name: str
    trigger_type: str = "keyword"
    keywords: list[str]
    response: FlowResponse


class FlowFile(BaseModel):
    flows: list[FlowDefinition]
