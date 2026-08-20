"""Grounds Agentlar test-chat replies in a merchant's handwritten
knowledge_text via Supabase - a SEPARATE pgvector store + Gemini
embedding space from this app's own Postgres/BGE-M3 pipeline
(app/nlp/embeddings.py, Faq.embedding/Product.embedding). See the
aqlchat-agents-knowledge-base-supabase memory for the full design
context and why this is Supabase-backed rather than reusing the local
embeddings pipeline.

Supabase's own schema (agent_knowledge_chunks table + match_agent_knowledge
RPC) is DDL, which the service-role key here (PostgREST) can't execute -
it lives in docs/agent_knowledge_supabase_schema.sql and must be applied
by hand once via the Supabase SQL Editor.

Talks to Supabase over plain httpx (PostgREST + rpc()), mirroring
app/instagram/oauth.py's established external-API pattern, rather than
adding the `supabase` python client dependency - this needs exactly three
call shapes (insert, delete, rpc), none of which need Auth/Realtime.
"""

import uuid

import httpx
from google import genai
from google.genai import types

from app.agents.chunking import chunk_text
from app.config import settings
from app.db.models import Agent
from app.llm.gemini_provider import MODEL as _CHAT_MODEL

_client: genai.Client | None = None


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        _client = genai.Client(api_key=settings.gemini_api_key)
    return _client


def embed_texts(texts: list[str], task_type: str = "RETRIEVAL_DOCUMENT") -> list[list[float]]:
    """Batches every text into a single embed_content call - confirmed
    2026-07-20 live against the configured key that the SDK accepts a
    list[str] for `contents` and returns one embedding per input, in
    order. Matters because it's unconfirmed whether embed_content shares
    the chat model's 20-req/day free-tier quota (see the
    feedback-gemini-quota memory) - one call per save, not one per chunk.

    task_type is asymmetric by design (Gemini embeds a stored chunk and a
    search query differently to optimize retrieval) - defaults to
    RETRIEVAL_DOCUMENT for replace_chunks_for_agent's chunk embeddings;
    generate_grounded_reply passes RETRIEVAL_QUERY for the customer's
    message instead."""
    if not texts:
        return []
    response = _get_client().models.embed_content(
        model=settings.gemini_embedding_model,
        contents=texts,
        config=types.EmbedContentConfig(
            task_type=task_type,
            output_dimensionality=settings.gemini_embedding_dimension,
        ),
    )
    return [e.values for e in response.embeddings]


def embed_text(text: str, task_type: str = "RETRIEVAL_DOCUMENT") -> list[float]:
    return embed_texts([text], task_type=task_type)[0]


def _headers() -> dict[str, str]:
    return {
        "apikey": settings.supabase_key,
        "Authorization": f"Bearer {settings.supabase_key}",
        "Content-Type": "application/json",
    }


def _rest_url(path: str) -> str:
    return f"{settings.supabase_url}/rest/v1/{path}"


def delete_chunks_for_agent(agent_id: uuid.UUID) -> None:
    response = httpx.delete(
        _rest_url("agent_knowledge_chunks"),
        params={"agent_id": f"eq.{agent_id}"},
        headers=_headers(),
        timeout=15,
    )
    response.raise_for_status()


def replace_chunks_for_agent(agent_id: uuid.UUID, merchant_id: uuid.UUID, knowledge_text: str) -> None:
    """Re-chunks + re-embeds knowledge_text and replaces this agent's
    entire chunk set in Supabase - called from app/agents/router.py
    whenever knowledge_text is created or changed. Deletes the old set
    first even when the new text is empty, so clearing the field also
    clears retrieval rather than leaving stale chunks behind."""
    delete_chunks_for_agent(agent_id)

    chunks = chunk_text(knowledge_text)
    if not chunks:
        return

    embeddings = embed_texts(chunks)
    rows = [
        {
            "agent_id": str(agent_id),
            "merchant_id": str(merchant_id),
            "chunk_index": i,
            "content": chunk,
            "embedding": embedding,
        }
        for i, (chunk, embedding) in enumerate(zip(chunks, embeddings))
    ]
    response = httpx.post(_rest_url("agent_knowledge_chunks"), json=rows, headers=_headers(), timeout=15)
    response.raise_for_status()


def search_relevant_chunks(agent_id: uuid.UUID, query_embedding: list[float], limit: int = 5) -> list[str]:
    response = httpx.post(
        _rest_url("rpc/match_agent_knowledge"),
        json={"query_embedding": query_embedding, "match_agent_id": str(agent_id), "match_count": limit},
        headers=_headers(),
        timeout=15,
    )
    response.raise_for_status()
    return [row["content"] for row in response.json()]


_TONE_INSTRUCTIONS = {
    "polite": "Warm, friendly, conversational.",
    "formal": "Concise, precise, professional.",
}

_SYSTEM_PROMPT_TEMPLATE = (
    "You are a customer-support assistant for a small Uzbek business. Reply in "
    "whichever language the customer wrote in.\n"
    "Tone: {tone_instruction}\n\n"
    "Ground your reply ONLY in the shop information below. If it doesn't answer "
    "the question, say briefly that you'll check and get back to them - never "
    "invent details.\n\n"
    "Shop information:\n{context}"
)


def generate_grounded_reply(agent: Agent, message: str) -> str:
    """Replaces test_chat's canned-reply stub (app/agents/router.py) -
    embeds the customer's message, retrieves the agent's most relevant
    knowledge_text chunks from Supabase, and generates a real Gemini
    reply grounded in just those chunks (not the full knowledge_text,
    which is what they were chunked from in the first place)."""
    query_embedding = embed_text(message, task_type="RETRIEVAL_QUERY")
    chunks = search_relevant_chunks(agent.id, query_embedding)
    context = "\n\n".join(chunks) if chunks else "(no information configured yet)"

    system_prompt = _SYSTEM_PROMPT_TEMPLATE.format(
        tone_instruction=_TONE_INSTRUCTIONS.get(agent.tone, _TONE_INSTRUCTIONS["polite"]),
        context=context,
    )
    response = _get_client().models.generate_content(
        model=_CHAT_MODEL,
        contents=message,
        config=types.GenerateContentConfig(
            system_instruction=system_prompt,
            http_options=types.HttpOptions(timeout=15000),
        ),
    )
    return response.text
