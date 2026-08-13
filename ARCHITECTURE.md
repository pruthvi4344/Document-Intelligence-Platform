# Document Intelligence Platform — Technical Specification & Build Plan

**Author:** Pruthviraj
**Repo:** Document-Intelligence-Platform
**Status:** Design → Build (Claude Code ready)
**Target:** Production-grade, resume-defensible, portfolio project for Winter 2027 co-op cycle

---

## 1. Product Summary

A full-stack **RAG (Retrieval-Augmented Generation) document intelligence platform** where users upload PDFs, DOCX, and TXT files and can:

- Search across their documents semantically (not just keyword match)
- Ask natural-language questions and get AI-generated answers **with citations** back to the exact source passage
- Get auto-generated summaries of individual documents or whole collections
- Run structured "workflows" (e.g., contract clause extraction, meeting-notes digest, resume-vs-JD comparison) — this is where the "6+ workflows" line comes from
- Manage documents, collections/folders, and share access

This spec is written so each section can be hand a Claude Code session directly (backend session, frontend session, infra session) without re-explaining context.

---

## 2. Confirmed Tech Stack (from your resume line — locking these in)

| Layer | Choice | Notes |
|---|---|---|
| Frontend | Next.js 14 (App Router) + React 18 + TypeScript | SSR for landing/marketing, CSR for app shell |
| UI | Tailwind CSS + shadcn/ui | Gives you the "25+ reusable components" claim honestly |
| Motion | Framer Motion | Page-load and scroll-triggered animation for the signature moments in Section 16 |
| State/data fetching | TanStack Query + Zustand | Query for server state, Zustand for UI state (upload progress, active doc) |
| Backend | Python 3.11 + FastAPI | Async, OpenAPI docs auto-generated |
| Orchestration | LangChain (LCEL) | Chains for ingestion, retrieval, RAG, summarization |
| LLM | **Groq (Llama 3.3 70B) — free tier, default.** Provider interface also supports OpenAI as an optional paid upgrade | Zero-cost by default; swap to `gpt-4o-mini` later only if you want the OpenAI name on the resume line specifically |
| Embeddings | **`sentence-transformers` (`all-MiniLM-L6-v2`), run locally in the Celery worker — free, default.** Optional swap: Gemini/OpenAI embeddings | 384-dim, no external API cost for ingestion at all; documented upgrade path to OpenAI `text-embedding-3-small` (1536-dim) if you want that name in the stack list |
| Vector store | PostgreSQL + pgvector | One DB instead of Postgres + separate vector DB — simpler ops story, good interview talking point |
| Relational data | Same PostgreSQL instance | Users, docs, chunks, chat sessions, workflow runs |
| Cache/queue broker | Redis | Response cache, rate limiting, Celery broker |
| Background jobs | Celery + Redis | Document parsing/embedding is async, not request-blocking |
| Auth | JWT (access + refresh) via FastAPI + `python-jose`, `passlib[bcrypt]` | Matches your resume line exactly |
| File storage | AWS S3 (or S3-compatible MinIO for local dev) | Never store raw files on app server disk |
| Containerization | Docker + docker-compose (local), same images to ECS/Fargate (prod) | |
| Cloud | AWS: S3, ECS Fargate or EC2, RDS (Postgres+pgvector), ElastiCache (Redis), CloudFront | |
| CI/CD | GitHub Actions → build/test → push to ECR → deploy | |
| Observability | Structured logging (structlog), Sentry for errors, basic Prometheus/Grafana optional stretch | |

---

## 3. High-Level Architecture

```
┌──────────────────┐        ┌───────────────────────┐        ┌─────────────────┐
│   Next.js App     │──────▶│   FastAPI (REST API)   │──────▶│   PostgreSQL     │
│  (React, TS,      │  HTTPS│  - Auth                │        │   + pgvector     │
│   Tailwind)        │◀──────│  - Documents            │◀──────│   (docs, chunks, │
└──────────────────┘  JWT   │  - Search/RAG           │        │    users, chats) │
                             │  - Workflows            │        └─────────────────┘
                             │  - Chat                 │
                             └─────────┬──────┬────────┘
                                       │      │
                          enqueue job  │      │ cache / rate limit
                                       ▼      ▼
                             ┌──────────────┐ ┌────────────┐
                             │ Celery Worker│ │   Redis    │
                             │ (parse+embed)│ └────────────┘
                             └──────┬───────┘
                                    │ store file
                                    ▼
                             ┌──────────────┐        ┌──────────────┐
                             │   AWS S3     │        │ Groq / local │
                             │ (raw files)  │        │ (embed + LLM)│
                             └──────────────┘        └──────────────┘
```

**Key design decision to be able to defend in an interview:** ingestion (parse → chunk → embed → store) is fully async via Celery. The API request that uploads a file returns immediately with `status: processing`; the frontend polls or listens via SSE/WebSocket for `status: ready`. This is what makes the platform feel production-grade instead of a notebook demo.

---

## 4. Database Schema (PostgreSQL + pgvector)

```sql
-- users
CREATE TABLE users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email TEXT UNIQUE NOT NULL,
    hashed_password TEXT NOT NULL,
    full_name TEXT,
    created_at TIMESTAMPTZ DEFAULT now()
);

-- collections (folders / projects that group documents)
CREATE TABLE collections (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    description TEXT,
    created_at TIMESTAMPTZ DEFAULT now()
);

-- documents
CREATE TABLE documents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    collection_id UUID REFERENCES collections(id) ON DELETE CASCADE,
    owner_id UUID REFERENCES users(id) ON DELETE CASCADE,
    filename TEXT NOT NULL,
    file_type TEXT CHECK (file_type IN ('pdf','docx','txt')),
    s3_key TEXT NOT NULL,
    file_size_bytes BIGINT,
    status TEXT CHECK (status IN ('uploaded','processing','ready','failed')) DEFAULT 'uploaded',
    page_count INT,
    summary TEXT,
    created_at TIMESTAMPTZ DEFAULT now(),
    processed_at TIMESTAMPTZ
);

-- chunks (the retrievable unit)
CREATE TABLE document_chunks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id UUID REFERENCES documents(id) ON DELETE CASCADE,
    chunk_index INT NOT NULL,
    content TEXT NOT NULL,
    page_number INT,
    token_count INT,
    embedding VECTOR(384),  -- 384 for local sentence-transformers default; change to 1536 if you swap in OpenAI embeddings
    created_at TIMESTAMPTZ DEFAULT now()
);

-- HNSW index for fast approximate nearest neighbor search
CREATE INDEX ON document_chunks USING hnsw (embedding vector_cosine_ops);

-- chat sessions (RAG conversation threads)
CREATE TABLE chat_sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id) ON DELETE CASCADE,
    collection_id UUID REFERENCES collections(id),
    title TEXT,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE chat_messages (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID REFERENCES chat_sessions(id) ON DELETE CASCADE,
    role TEXT CHECK (role IN ('user','assistant')),
    content TEXT NOT NULL,
    citations JSONB,          -- [{chunk_id, document_id, page, snippet}]
    created_at TIMESTAMPTZ DEFAULT now()
);

-- workflow runs (the "6+ workflows" feature)
CREATE TABLE workflow_runs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id) ON DELETE CASCADE,
    workflow_type TEXT NOT NULL,   -- e.g. 'contract_clause_extraction'
    input_document_ids UUID[] NOT NULL,
    status TEXT CHECK (status IN ('queued','running','completed','failed')) DEFAULT 'queued',
    result JSONB,
    created_at TIMESTAMPTZ DEFAULT now(),
    completed_at TIMESTAMPTZ
);
```

This schema alone gives you a real story for the "18+ RESTful APIs" and "pgvector" resume lines — every table maps to at least 3-4 CRUD-plus-action endpoints below.

---

## 5. API Design (target: 18–22 endpoints)

### Auth
- `POST /api/auth/register`
- `POST /api/auth/login` → access + refresh JWT
- `POST /api/auth/refresh`
- `GET /api/auth/me`

### Collections
- `GET /api/collections`
- `POST /api/collections`
- `DELETE /api/collections/{id}`

### Documents
- `POST /api/documents/upload` (multipart, returns `document_id`, kicks off Celery job)
- `GET /api/documents` (filter by collection, status)
- `GET /api/documents/{id}`
- `GET /api/documents/{id}/status` (poll while `processing`)
- `DELETE /api/documents/{id}`
- `GET /api/documents/{id}/summary`

### Search / RAG
- `POST /api/search/semantic` — body: `{query, collection_id?, top_k}` → ranked chunks
- `POST /api/chat/sessions` — create session
- `POST /api/chat/sessions/{id}/messages` — send message → streamed RAG answer with citations (SSE)
- `GET /api/chat/sessions/{id}/messages`

### Workflows
- `GET /api/workflows` — list available workflow types
- `POST /api/workflows/run` — body: `{workflow_type, document_ids}`
- `GET /api/workflows/runs/{id}` — poll status/result
- `GET /api/workflows/runs` — history

### System
- `GET /api/health`

That's ~19 endpoints — matches your "18+ RESTful APIs" line exactly, so keep this list as the actual contract, not just a target.

---

## 6. RAG Pipeline (the core engineering piece)

**Ingestion (Celery task, triggered on upload):**
1. Download file from S3
2. Parse text:
   - PDF → `pypdf` or `unstructured` (prefer `unstructured` for layout-aware parsing, falls back to `pypdf`)
   - DOCX → `python-docx`
   - TXT → direct read
3. Chunk text — LangChain `RecursiveCharacterTextSplitter`, ~800 tokens/chunk, 100-token overlap, chunk boundaries aware of paragraph breaks
4. Embed each chunk — batch through the local embedding model (`sentence-transformers`) in the Celery worker; no external API call, no cost
5. Store chunks + embeddings in `document_chunks`
6. Generate a document-level summary (map-reduce summarization over chunks using LangChain's `load_summarize_chain` or a custom LCEL chain)
7. Update `documents.status = 'ready'`

**Query time (RAG answer):**
1. Embed user query
2. `pgvector` cosine similarity search (HNSW index) — top-k (default 6) chunks, optionally filtered by `collection_id`
3. Optional: re-rank with a cheap cross-encoder or just MMR (maximal marginal relevance) for diversity
4. Build prompt: system instructions + retrieved chunks (with source tags) + chat history + user question
5. Stream LLM response via SSE to frontend
6. Parse citations back out (tag each chunk with an id in the prompt, ask the model to reference `[1]`, `[2]`, then map those back to `document_id`/`page_number` for the UI)

This citation-mapping step is what turns "we called an LLM API" into "we built citation-grounded RAG" — worth building carefully, it's your strongest interview talking point.

---

## 7. The 6 Workflows (pick these, they're realistic and demoable)

1. **Document Summarization** — long doc → structured summary (TL;DR + key points)
2. **Multi-document Q&A / Chat** — the core RAG chat feature
3. **Contract Clause Extraction** — pull out parties, dates, obligations, termination clauses into structured JSON
4. **Resume ↔ Job Description Match** — given a resume + JD, extract match score + gaps (this one you can build with real conviction since you built ResumeMaker already)
5. **Meeting Notes Digest** — action items, decisions, owners from a transcript/notes doc
6. **Comparative Analysis** — diff/compare 2+ documents on a set of dimensions (e.g., compare two vendor proposals)

Each workflow = one LangChain chain with a dedicated prompt + a Pydantic output schema (use LangChain's structured output / `with_structured_output` so results are reliably JSON, not free text you have to regex).

---

## 8. Backend Project Structure

```
backend/
├── app/
│   ├── main.py
│   ├── core/
│   │   ├── config.py          # pydantic-settings, env vars
│   │   ├── security.py        # JWT, password hashing
│   │   └── deps.py            # FastAPI dependencies (get_db, get_current_user)
│   ├── models/                # SQLAlchemy models
│   ├── schemas/                # Pydantic request/response schemas
│   ├── api/
│   │   ├── auth.py
│   │   ├── collections.py
│   │   ├── documents.py
│   │   ├── search.py
│   │   ├── chat.py
│   │   └── workflows.py
│   ├── services/
│   │   ├── ingestion.py       # parse/chunk logic
│   │   ├── embeddings.py
│   │   ├── rag.py             # retrieval + prompt building
│   │   ├── workflows/         # one module per workflow
│   │   └── s3.py
│   ├── workers/
│   │   ├── celery_app.py
│   │   └── tasks.py
│   └── db/
│       ├── session.py
│       └── migrations/        # Alembic
├── tests/
├── Dockerfile
├── docker-compose.yml
└── requirements.txt
```

## 9. Frontend Project Structure

```
frontend/
├── app/
│   ├── (marketing)/page.tsx           # landing page
│   ├── (app)/
│   │   ├── dashboard/page.tsx
│   │   ├── documents/[id]/page.tsx
│   │   ├── chat/[sessionId]/page.tsx
│   │   └── workflows/page.tsx
│   ├── login/page.tsx
│   └── register/page.tsx
├── components/
│   ├── ui/                    # shadcn primitives (button, dialog, etc.)
│   ├── documents/
│   │   ├── UploadDropzone.tsx
│   │   ├── DocumentCard.tsx
│   │   └── DocumentStatusBadge.tsx
│   ├── chat/
│   │   ├── ChatWindow.tsx
│   │   ├── MessageBubble.tsx
│   │   ├── CitationChip.tsx   # click → opens source doc at page
│   │   ├── CitationThread.tsx # animated line/thread from highlight → citation chip (Section 16)
│   │   └── StreamingIndicator.tsx
│   ├── workflows/
│   │   ├── WorkflowPicker.tsx
│   │   └── WorkflowResultView.tsx
│   └── marketing/
│       ├── DocumentStackHero.tsx  # signature hero element, Section 16
│       └── HighlightReveal.tsx    # reusable highlighter-stroke text animation
├── lib/
│   ├── api-client.ts          # typed fetch wrapper
│   ├── auth.ts
│   └── hooks/                 # useDocuments, useChatSession, etc.
├── stores/                    # zustand stores
└── types/
```

25+ components is very achievable across `ui/` (12-15 shadcn primitives) + the feature components above — keep an honest running count as you build so the resume number stays true.

---

## 10. Auth & Security

- Access token (15 min expiry) + refresh token (7 days, httpOnly cookie)
- Passwords hashed with bcrypt via `passlib`
- Rate limiting on `/api/chat/*` and `/api/search/*` via Redis (token bucket, e.g. 30 req/min/user)
- File upload validation: MIME type check + magic-byte check (don't trust extension), max 25MB
- S3 objects private, accessed via presigned URLs only
- CORS locked to frontend origin(s)

---

## 11. Performance Notes (to genuinely earn "reducing response latency by 50%")

Concrete, measurable levers — implement at least 2-3 and benchmark before/after so the number is real:
- Redis caching of embedding results for repeated/similar queries
- HNSW index (vs. brute-force cosine scan) on `document_chunks.embedding`
- Streaming LLM responses (perceived latency drop, first token in ~1s vs waiting for full response)
- Connection pooling (SQLAlchemy `pool_size`) + async DB driver (`asyncpg`)
- Parallel chunk embedding (batch requests instead of one call per chunk)

Benchmark script idea: log p50/p95 latency for `/api/chat/sessions/{id}/messages` before and after adding the HNSW index + cache, save the numbers — that's your evidence, not just a resume claim.

---

## 12. Deployment (AWS)

| Component | Service |
|---|---|
| Frontend | Vercel (simplest) or S3+CloudFront if you want to keep it all-AWS for the resume story |
| Backend API | ECS Fargate (2 tasks behind an ALB) |
| Celery workers | Separate ECS Fargate service, scaled independently |
| Database | RDS PostgreSQL 16 with pgvector extension enabled |
| Redis | ElastiCache |
| File storage | S3 |
| Secrets | AWS Secrets Manager → injected as env vars |
| CI/CD | GitHub Actions: on push to `main` → run tests → build Docker images → push to ECR → `aws ecs update-service --force-new-deployment` |

Local dev is `docker-compose up` with Postgres+pgvector, Redis, MinIO (S3-compatible), API, worker, frontend — one command, no AWS account needed to develop.

---

## 13. Build Plan / Milestones

| Phase | Scope | Est. time |
|---|---|---|
| 1. Foundation | Repo scaffold, docker-compose, Postgres+pgvector schema, Alembic migrations, FastAPI skeleton, JWT auth end-to-end | 1 week |
| 2. Ingestion | Upload endpoint → S3 → Celery parse/chunk/embed pipeline, status polling on frontend | 1 week |
| 3. RAG Core | Semantic search endpoint, chat endpoint with streaming + citations, chat UI | 1.5 weeks |
| 4. Workflows | Build all 6 workflow chains + structured outputs + workflow UI | 1.5 weeks |
| 5. Polish/Perf | Caching, HNSW tuning, latency benchmarking, error states, empty states, loading skeletons | 1 week |
| 6. Deploy | Dockerize, GitHub Actions CI/CD, deploy to AWS, custom domain, README + architecture diagram for GitHub | 3-4 days |

Total: ~6-7 weeks part-time, realistic alongside your co-op applications.

---

## 14. The $0 Stack (this is now the default — Section 2 already reflects it)

Section 2's tech stack table already defaults to the free options end to end (Groq for the LLM, local `sentence-transformers` for embeddings). This section covers the rest of the infrastructure — hosting, DB, cache, storage — so the whole platform, not just the AI calls, can run and stay live at $0/month. The AWS versions from Sections 2-12 are kept in the doc as the "designed for production AWS" story for your resume/README, since AWS is already on your resume line — but you don't need to pay for any of it to build and demo this.

| Layer | AWS/paid production version | Free default (what you'll actually run) | Why it works |
|---|---|---|---|
| Postgres + pgvector | AWS RDS (~$15-30/mo) | **Supabase** or **Neon** free tier | Both are managed Postgres with pgvector enabled out of the box — no self-hosting needed |
| Redis | AWS ElastiCache (~$10-15/mo) | **Upstash Redis** free tier | Serverless Redis, works as a Celery broker, generous free request quota |
| File storage | AWS S3 | **Cloudflare R2** free tier (10GB storage, no egress fees) or Supabase Storage free tier | R2's lack of egress fees matters if you demo it publicly |
| Backend hosting (API + Celery worker) | AWS ECS Fargate | **Render** or **Fly.io** free/hobby tier | Both support Docker deploys directly from your existing Dockerfile |
| Frontend hosting | S3 + CloudFront | **Vercel** free tier | Built for Next.js, zero config |
| Embeddings | OpenAI `text-embedding-3-small` | **`sentence-transformers` (`all-MiniLM-L6-v2`)**, run locally in the Celery worker — already the Section 2 default | Zero external API cost for ingestion, no rate limits, no billing surprises |
| LLM (chat + workflows) | OpenAI GPT-4o/4o-mini | **Groq** (Llama 3.3 70B) — already the Section 2 default | Free tier, and notably fast — helps your "reducing latency" story too |
| CI/CD | GitHub Actions → ECR → ECS | GitHub Actions → Render/Fly deploy hook | Free on public/small repos either way |

### Code-level implication: keep providers swappable anyway

Even running Groq + local embeddings by default, build an `LLMProvider` and `EmbeddingProvider` interface in `app/services/` from day one, so switching to OpenAI/Gemini/hosted embeddings later is a config change, not a rewrite — useful if a company you interview with specifically wants to see OpenAI integration, or if Groq's free tier ever gets too restrictive for a demo you're actively showing someone:

```python
# app/services/llm/base.py
class LLMProvider(Protocol):
    async def chat(self, messages: list[dict], stream: bool = True) -> AsyncIterator[str]: ...

class EmbeddingProvider(Protocol):
    async def embed(self, texts: list[str]) -> list[list[float]]: ...
```

Then `GroqProvider`, `GeminiProvider`, `OpenAIProvider`, and `LocalEmbeddingProvider` (wrapping `sentence-transformers`) each implement these, selected via an env var (`LLM_PROVIDER=groq`, default). This is also a legitimately strong interview point — providers can raise cost/vendor-lock-in questions, and "I designed it provider-agnostic and defaulted to the free option" is a real, honest answer.

### What still costs anything

Nothing, by default. Groq's and Supabase's/Upstash's/R2's free tiers comfortably cover portfolio-level traffic (a handful of demo users, not production scale). The only optional cost is if you later want to run a one-time benchmark against OpenAI specifically to compare latency/quality for Section 11's "50% latency reduction" write-up — that's a few dollars once, not an ongoing bill.

---

## 15. Visual Design Direction (make it look distinctive, not templated)

The subject here is documents becoming intelligence — text getting read, highlighted, and connected to answers. The design should look like that mechanic, not like a generic "AI SaaS" landing page. Concretely: **avoid** the cream-background-plus-terracotta look and the black-background-plus-neon-accent look that most AI tools default to — neither is grounded in what this product actually does.

### Design tokens

**Color** — inspired by ink, paper, and the literal act of highlighting a source passage:
- `--ink` `#14213D` — deep navy, primary background
- `--paper` `#FAF7F0` — warm off-white, card/surface background
- `--highlighter` `#FFC857` — amber highlighter — the signature accent, used *only* for citations/evidence, never as generic decoration
- `--stamp-teal` `#2E6E62` — secondary accent for success/ready states (like archive stamp ink)
- `--redline` `#C1440E` — sparing use for errors/deletions (editor's red pen)
- `--slate` `#3A3F4B` — body text on paper surfaces

**Type** — two roles, deliberately not the default AI pairing:
- Display: **Fraunces** (a warm, slightly irregular serif with real character) for headlines — feels like a well-set document, not a tech demo
- Body/UI: **IBM Plex Sans** for interface text — excellent legibility at small sizes
- Utility/data: **IBM Plex Mono** for page numbers, citation tags, doc IDs, timestamps — literally mimics the "stamped metadata" you'd see on a real filed document

**Layout concept**: the marketing hero is not a headline-plus-screenshot. It's a **stack of document cards, slightly fanned like real paper on a desk**, with one card mid-highlight. The product's actual mechanic — highlight → citation → answer — *is* the hero image, not a decoration next to it.

**Signature element**: **the highlight-to-citation thread.** A sentence in a source document gets an amber highlighter stroke drawn across it (not a hard color-swap — an actual stroke animation, like a highlighter pen moving), then a thin curved thread draws from that highlight to a citation chip next to the AI's answer. This single moment *is* the product's value proposition made visible, and it recurs everywhere: marketing hero, onboarding, and the real chat UI when it cites sources.

### Where animation earns its place (and where it doesn't)

Orchestrated, not scattered — a few deliberate moments, not motion on every element:
- **Page load**: the highlighter stroke draws once across the hero's key phrase (~600ms, eased), immediately establishing what the product does before any copy is read
- **Scroll into the "how it works" section**: document cards animate into a fanned stack, one at a time, each landing with a slight paper-drop settle (not a bounce — paper doesn't bounce)
- **Live chat UI**: when a real answer streams in, the citation thread animation from the hero plays for real, connecting the actual retrieved chunk to the actual citation chip — this reuses the marketing site's signature moment as a real product feature, which is the strongest kind of continuity
- **Hover on a document card**: a 2-3px lift + soft shadow increase, mimicking picking up a physical sheet of paper
- **What to skip**: no floating gradient blobs, no particle backgrounds, no auto-rotating carousels — none of that is grounded in "documents," and it's the kind of ambient decoration that makes a design read as AI-generated filler
- Respect `prefers-reduced-motion`: fall back to instant-state changes (highlight just appears, no draw animation)

### Signature component sketch (Framer Motion)

Drop this in as `components/marketing/HighlightReveal.tsx` — a reusable building block for the signature moment described above:

```tsx
"use client";
import { motion, useReducedMotion } from "framer-motion";

export function HighlightReveal({ children }: { children: React.ReactNode }) {
  const reduceMotion = useReducedMotion();

  return (
    <span className="relative inline-block">
      <span className="relative z-10">{children}</span>
      <motion.span
        className="absolute inset-x-0 bottom-1 -z-0 h-[0.55em] bg-[var(--highlighter)]"
        style={{ originX: 0 }}
        initial={{ scaleX: 0 }}
        animate={{ scaleX: 1 }}
        transition={reduceMotion ? { duration: 0 } : { duration: 0.6, ease: [0.65, 0, 0.35, 1] }}
      />
    </span>
  );
}
```

Use it inline in the hero headline (`Ask your <HighlightReveal>documents</HighlightReveal> anything`) and, more importantly, reuse the same stroke primitive inside the real `CitationChip`/`CitationThread` components so the "cool landing page trick" and the "actual product feature" are visibly the same mechanic — that consistency is what makes a design feel intentional instead of decorative.

### Before you build: sanity-check against the brief

This direction is grounded in what a document-intelligence/RAG tool literally does (highlighting a source, drawing a line to where it's used) rather than a generic "AI product" mood board. If anything above starts drifting toward decoration for its own sake while building — an animation added because it looks neat rather than because it demonstrates the mechanic — cut it. One well-executed signature moment beats five scattered effects.

---

## 16. Claude Code Handoff Notes

When you start building with Claude Code, feed it this doc plus, in order:
0. Everything defaults to the $0 stack (Groq, local embeddings, Supabase/Upstash/R2/Render/Vercel — Section 14). Only tell Claude Code to use the AWS/OpenAI versions from Sections 2-12 if you specifically want to pay for and deploy that version instead.
1. "Set up the backend project structure from Section 8, docker-compose from Section 12, and the DB schema from Section 4 with Alembic migrations."
2. "Implement Section 5 auth endpoints + JWT security from Section 10."
3. "Implement the ingestion pipeline (Section 6) as a Celery task, wired to the upload endpoint."
4. "Implement semantic search + RAG chat endpoint with SSE streaming and citation mapping (Section 6, query time)."
5. "Implement the 6 workflows from Section 7 as LangChain chains with structured outputs."
6. Then move to frontend, one route/feature at a time from Section 9, using the design tokens and signature component from Section 15 from the very first component — not retrofitted at the end.

Keep this file in the repo root as `ARCHITECTURE.md` — it also becomes your GitHub README's technical deep-dive section.
