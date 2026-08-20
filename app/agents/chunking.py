"""Splits an Agent's knowledge_text into overlapping fixed-size chunks
before embedding (app/agents/knowledge.py) - see the
aqlchat-agents-knowledge-base-supabase memory for why this exists at all
(handwritten knowledge_text, re-chunked/re-embedded into Supabase on every
save). No sentence/paragraph awareness; simplest thing that works for the
short shop-profile text this field holds today. Revisit if retrieval
quality suffers on much longer merchant-authored text.
"""

CHUNK_SIZE = 1000
CHUNK_OVERLAP = 150


def chunk_text(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    stripped = text.strip()
    if not stripped:
        return []

    step = chunk_size - overlap
    chunks = []
    start = 0
    while start < len(stripped):
        chunk = stripped[start : start + chunk_size].strip()
        if chunk:
            chunks.append(chunk)
        start += step
    return chunks
