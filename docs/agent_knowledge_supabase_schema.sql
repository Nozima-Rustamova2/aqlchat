-- Agentlar knowledge-base vector store (Supabase side).
--
-- This is DDL, which the app's SUPABASE_KEY (a PostgREST service-role
-- key) cannot execute - PostgREST does DML + RPC calls against objects
-- that already exist, not CREATE TABLE/CREATE EXTENSION/CREATE FUNCTION.
-- Paste this whole file into the Supabase project's SQL Editor and run
-- it ONCE, by hand. See the aqlchat-agents-knowledge-base-supabase memory
-- for the full design context.
--
-- agent_id/merchant_id here are NOT foreign keys into this project's own
-- Postgres (they live in a different database) - they're plain uuid
-- columns copied over at write time, scoping rows the same way the app's
-- own tables scope by merchant_id, just without a DB-level FK to enforce it.

create extension if not exists vector;

create table if not exists agent_knowledge_chunks (
    id uuid primary key default gen_random_uuid(),
    agent_id uuid not null,
    merchant_id uuid not null,
    chunk_index int not null,
    content text not null,
    embedding vector(768) not null,
    created_at timestamptz not null default now()
);

create index if not exists agent_knowledge_chunks_agent_id_idx on agent_knowledge_chunks (agent_id);

create index if not exists agent_knowledge_chunks_embedding_idx
    on agent_knowledge_chunks using hnsw (embedding vector_cosine_ops);

-- Cosine similarity search scoped to one agent, called via PostgREST's
-- rpc/match_agent_knowledge from app/agents/knowledge.py.
create or replace function match_agent_knowledge(
    query_embedding vector(768),
    match_agent_id uuid,
    match_count int default 5
)
returns table (id uuid, content text, similarity float)
language sql stable
as $$
    select id, content, 1 - (embedding <=> query_embedding) as similarity
    from agent_knowledge_chunks
    where agent_id = match_agent_id
    order by embedding <=> query_embedding
    limit match_count;
$$;
