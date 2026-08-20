"""Merchant-facing CRUD + Telegram-connect + test-chat API for the
"Agentlar" (AI Agents) feature - design_handoff_dukan_ai_agents/README.md.

Each Agent is its own AI persona with its OWN Telegram bot connection,
deliberately separate from Merchant.telegram_bot_token/telegram_bot_id
(the already-shipped core onboarding Telegram service, app/onboarding/).
See app/db/models.py's Agent docstring for the full rationale.

Auth is the real dashboard session (get_current_merchant), same trust
level as app/automations/router.py - every endpoint is scoped to the
authenticated merchant, and _get_owned_agent 404s (never 403s) on
cross-merchant access so a merchant can't distinguish "not mine" from
"doesn't exist".
"""

import logging
import uuid

import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_merchant
from app.agents import knowledge
from app.agents.schemas import (
    AgentOut,
    CreateAgentRequest,
    TelegramConnectRequest,
    TestChatRequest,
    TestChatResponse,
    UpdateAgentRequest,
)
from app.config import settings
from app.db.models import Agent, Merchant
from app.db.session import get_db

router = APIRouter(tags=["agents"])
logger = logging.getLogger(__name__)

_UPDATE_FIELDS = (
    "name",
    "tone",
    "daily_limit",
    "knowledge_text",
    "active",
    "split_messages",
    "operator_pause",
    "stop_keywords",
)


def _agent_out(agent: Agent) -> AgentOut:
    return AgentOut(
        id=str(agent.id),
        name=agent.name,
        tone=agent.tone,
        daily_limit=agent.daily_limit,
        knowledge_text=agent.knowledge_text,
        active=agent.active,
        split_messages=agent.split_messages,
        operator_pause=agent.operator_pause,
        stop_keywords=agent.stop_keywords,
        telegram_bot_id=agent.telegram_bot_id,
        telegram_connected=agent.telegram_bot_id is not None,
        created_at=agent.created_at.isoformat(),
        updated_at=agent.updated_at.isoformat(),
    )


def _get_owned_agent(db: Session, merchant: Merchant, agent_id: str) -> Agent:
    try:
        parsed_id = uuid.UUID(agent_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="not found")
    agent = db.scalar(select(Agent).where(Agent.id == parsed_id, Agent.merchant_id == merchant.id))
    if agent is None:
        raise HTTPException(status_code=404, detail="not found")
    return agent


def _sync_knowledge(agent: Agent) -> None:
    """Re-chunks + re-embeds agent.knowledge_text into Supabase - best
    effort, like app/products/ingestion.py's photo-embed step, since this
    is an external network call (and the Supabase schema in
    docs/agent_knowledge_supabase_schema.sql may not even be applied yet
    on a fresh deploy). A failure here shouldn't break agent create/update
    - it only means test_chat falls back to ungrounded replies until the
    next successful sync."""
    try:
        knowledge.replace_chunks_for_agent(agent.id, agent.merchant_id, agent.knowledge_text)
    except Exception:
        logger.exception("failed to sync knowledge chunks to Supabase for agent %s", agent.id)


@router.post("/agents")
def create_agent(
    body: CreateAgentRequest,
    db: Session = Depends(get_db),
    merchant: Merchant = Depends(get_current_merchant),
) -> AgentOut:
    agent = Agent(
        merchant_id=merchant.id,
        name=body.name,
        tone=body.tone,
        daily_limit=body.daily_limit,
        knowledge_text=body.knowledge_text,
    )
    db.add(agent)
    db.commit()
    db.refresh(agent)
    _sync_knowledge(agent)
    return _agent_out(agent)


@router.get("/agents")
def list_agents(
    db: Session = Depends(get_db),
    merchant: Merchant = Depends(get_current_merchant),
) -> list[AgentOut]:
    agents = db.scalars(
        select(Agent).where(Agent.merchant_id == merchant.id).order_by(Agent.created_at.desc())
    ).all()
    return [_agent_out(agent) for agent in agents]


@router.get("/agents/{agent_id}")
def get_agent(
    agent_id: str,
    db: Session = Depends(get_db),
    merchant: Merchant = Depends(get_current_merchant),
) -> AgentOut:
    agent = _get_owned_agent(db, merchant, agent_id)
    return _agent_out(agent)


@router.patch("/agents/{agent_id}")
def update_agent(
    agent_id: str,
    body: UpdateAgentRequest,
    db: Session = Depends(get_db),
    merchant: Merchant = Depends(get_current_merchant),
) -> AgentOut:
    agent = _get_owned_agent(db, merchant, agent_id)

    updates = body.model_dump(exclude_unset=True)
    for field in _UPDATE_FIELDS:
        if field in updates:
            setattr(agent, field, updates[field])

    db.add(agent)
    db.commit()
    db.refresh(agent)
    if "knowledge_text" in updates:
        _sync_knowledge(agent)
    return _agent_out(agent)


@router.delete("/agents/{agent_id}")
def delete_agent(
    agent_id: str,
    db: Session = Depends(get_db),
    merchant: Merchant = Depends(get_current_merchant),
) -> dict:
    agent = _get_owned_agent(db, merchant, agent_id)
    try:
        knowledge.delete_chunks_for_agent(agent.id)
    except Exception:
        logger.exception("failed to delete Supabase knowledge chunks for agent %s", agent.id)
    db.delete(agent)
    db.commit()
    return {"ok": True}


def _get_me(token: str) -> dict | None:
    """Real getMe validation, unlike scripts/seed_merchant.py's
    best-effort _try_get_me - a failure here means the token is never
    stored (the caller 400s), since this result directly gates
    AgentOut.telegram_connected."""
    try:
        response = httpx.get(f"{settings.telegram_api_base}/bot{token}/getMe", timeout=10)
        response.raise_for_status()
        payload = response.json()
        if not payload.get("ok"):
            return None
        return payload["result"]
    except Exception:
        return None


@router.post("/agents/{agent_id}/telegram/connect")
def connect_telegram(
    agent_id: str,
    body: TelegramConnectRequest,
    db: Session = Depends(get_db),
    merchant: Merchant = Depends(get_current_merchant),
) -> AgentOut:
    agent = _get_owned_agent(db, merchant, agent_id)

    bot_info = _get_me(body.token)
    if bot_info is None:
        raise HTTPException(status_code=400, detail="invalid telegram bot token")

    agent.telegram_bot_token = body.token
    agent.telegram_bot_id = bot_info["id"]
    db.add(agent)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=400, detail="this bot is already connected to another agent")
    db.refresh(agent)
    return _agent_out(agent)


@router.post("/agents/{agent_id}/test-chat")
def test_chat(
    agent_id: str,
    body: TestChatRequest,
    db: Session = Depends(get_db),
    merchant: Merchant = Depends(get_current_merchant),
) -> TestChatResponse:
    # Real Gemini call, grounded in this agent's Supabase-embedded
    # knowledge_text chunks (app/agents/knowledge.py). daily_limit
    # enforcement per end-customer is still real scope for a follow-up
    # task (this endpoint is only ever called by the merchant testing
    # their own agent, not a real customer) - see the
    # aqlchat-feedback-gemini-quota and aqlchat-agents-knowledge-base-supabase
    # memories.
    agent = _get_owned_agent(db, merchant, agent_id)
    reply = knowledge.generate_grounded_reply(agent, body.message)
    return TestChatResponse(reply=reply)
