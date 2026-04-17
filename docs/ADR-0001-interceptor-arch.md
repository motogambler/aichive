# ADR 0001 — `loc-ai-storage`: Local AI Traffic Capture, Compression & Semantic Search

Status: **proposed → active**  
Supersedes: *(none)*  
Date: 2026-04-15

---

## 1. Summary

`loc-ai-storage` is a local-first CLI tool that sits between you and your AI provider as a **compressed-token proxy**. It intercepts outbound prompts and inbound completions, applies lossless compression on the wire so you transmit (and pay for) fewer tokens, then decompresses locally so your tools see the full original. Everything flows through a pipeline that also stores, indexes, and **compacts** captured traffic — merging redundant context into denser representations over time — and provides hybrid search (vector + full-text + metadata), **cross-session memory injection**, and token-savings analytics. All local, all pip-installable, with optional Docker packaging.

---

## 2. Problem Statement

AI coding agents generate enormous volumes of prompt/completion traffic that is:

- **Ephemeral** — gone after the session ends; no recall of prior context, decisions, or tool outputs.
- **Expensive** — redundant context re-injected every turn inflates token costs 2-10×. No way to compress what goes over the wire.
- **Redundant** — the same facts, code snippets, and tool outputs are re-sent verbatim across turns and sessions with no compaction.
- **Opaque** — no visibility into what was sent/received, no audit trail, no search.

Existing tools solve pieces of this problem, but none provide interception, lossless local decompression, compacted cross-session memory, hybrid semantic + lexical search, and token-savings analytics together in a single, pip-installable package.

**`loc-ai-storage` fills the gap**: compressed-token proxy (save money on every request) → lossless local decompression → compaction of stored history → hybrid semantic index → cross-session memory injection → token-savings analytics. All local, all pip-installable.

---

## 3. Decision

### 3.1 Architecture Overview

```
AI Tool (Copilot / Claude Code / Codex / Cursor / etc.)
     │
     │  outbound prompt (full-size)
     ▼
  ┌───────────────────────────────────────────────┐
  │  loc-ai-storage proxy  (mitmproxy addon)      │
  │                                               │
  │  1. Capture ──► store raw in SQLite            │
  │  2. Compress ──► ContentRouter                 │
  │       ├─ JSON  → structural dedup + minify     │
  │       ├─ Code  → AST-aware / signature-only    │
  │       └─ Text  → zlib / brotli / lz4           │
  │  3. Forward compressed payload to provider ──► │──► LLM Provider
  │                                               │
  │  4. Receive compressed completion ◄────────── │◄── LLM Provider
  │  5. Decompress locally (lossless)              │
  │  6. Return full response to AI tool            │
  └───────────────────────────────────────────────┘
     │
     │  full-size response back to tool
     ▼
  AI Tool (sees original, uncompressed content)

  ════════════════════════════════════════════════
  Background pipeline (async)
  ════════════════════════════════════════════════

  SQLite store (raw + compressed bodies, Fernet-encrypted at rest)
     │
     ├─► Ingest Worker         compute embeddings + index
     │     ├─ Embeddings: local sentence-transformers  -or-  OpenAI API
     │     ├─ FAISS index (semantic / dense)
     │     ├─ SQLite FTS5 (lexical / sparse)
     │     └─ Metadata index (timestamps, hosts, status codes)
     │
     ├─► Compaction Worker     reduce redundancy over time
     │     ├─ Online Semantic Synthesis (merge related facts)
     │     ├─ Dedup identical / near-identical payloads
     │     └─ Decay + prune low-value entries
     │
     ├─► Memory Manager        cross-session context injection
     │     ├─ Extract observations on session close
     │     ├─ Token-budgeted context bundle on session start
     │     └─ Provenance links to source request/response IDs
     │
     └─► Analytics             token-savings tracking
           └─ raw vs. compressed vs. compacted, per-host, per-session

CLI / Python API
**Phase 1 — Core (done)**
- [x] mitmproxy addon with SQLite storage
- [x] Compression (zlib/brotli/lz4)
- [x] Optional Fernet encryption at rest
- [x] FAISS semantic search
- [x] Background ingest worker

**Phase 2 — Compressed transit + compaction (completed)**
- [x] Wire compression proxy (prompt restructuring; Content-Encoding path implemented with provider negotiation and adaptive probe loop)
- [x] Compaction worker (dedup, merge, decay/prune) — `interceptor/compaction_worker.py`
- [x] `compact` CLI command — `interceptor/compact.py`
- [x] Two-stage compression (wire + storage)

**Phase 3 — Memory + search (mostly completed)**
- [x] Cross-session memory extraction + `recall` — `interceptor/recall.py`
- [x] Hybrid search (FTS5 + FAISS + metadata fusion) — `interceptor/search.py`
- [x] Local embeddings — `sentence-transformers` optional; deterministic hash fallback in `interceptor/embeddings.py`
- [x] `gain` analytics & CLI — `interceptor/gain.py` (can be further polished)

**Phase 4 — Packaging & integrations (partial)**
- [x] Packaging metadata: `pyproject.toml` with console entry points and optional extras
- [x] MCP test server & export stubs — `interceptor/mcp_server.py`, `interceptor/export.py`
- [x] Parquet export with JSONL fallback — `interceptor/export_parquet.py`
- [x] AST-aware code compression (basic Python tokenizer-based compressor) — `interceptor/code_compress.py`
- [x] Auto-redaction / PII scrubbing — `interceptor/redact.py` (wired into addon)
 - [x] Production MCP server (auth, hardened batching/retries) — `interceptor/mcp_app.py` (FastAPI + optional Bearer token)

 - [x] Auto-redaction policy engine & CLI — `interceptor/redact_policy.py` + `interceptor/redact_cli.py`
2. **Storage compression** (at rest) — applied when writing to SQLite. Goal: minimize disk usage.

#### Embeddings — local-first, API-optional
| Priority | Backend | pip package | Notes |
|----------|---------|-------------|-------|
| 1 (preferred) | `sentence-transformers` local model | [`sentence-transformers`](https://pypi.org/project/sentence-transformers/) | `all-MiniLM-L6-v2` default; runs on CPU; no API key needed |
| 2 (optional) | OpenAI Embeddings API | [`openai`](https://pypi.org/project/openai/) | `text-embedding-3-small`; requires `OPENAI_API_KEY` |
| 3 (fallback) | Deterministic hash embeddings | *(stdlib `hashlib`)* | Zero deps; works offline; quality-limited |

> **No conda.** All dependencies are pip-installable. PyTorch (needed by `sentence-transformers`) ships pip wheels for CPU on all platforms. Only resort to conda if a user's platform has no pip wheel for `faiss-cpu` (extremely rare — [faiss-cpu](https://pypi.org/project/faiss-cpu/) publishes manylinux + macOS + Windows wheels).

#### Search — hybrid multi-view retrieval
Inspired by SimpleMem's three-index approach:

| View | Index | Use case |
|------|-------|----------|
| **Semantic** (dense) | FAISS (`faiss-cpu`) | "Find conversations about database migrations" |
| **Lexical** (sparse) | SQLite FTS5 | Exact keyword / regex matches |
| **Symbolic** (metadata) | SQLite indexed columns | Filter by host, timestamp range, HTTP status, session ID |

Results are merged via ID-based deduplication and reciprocal-rank fusion (RRF).

#### Compaction — online semantic synthesis
Inspired by SimpleMem's Online Semantic Synthesis:

Over time, stored traffic accumulates redundant facts. The **Compaction Worker** periodically (or on-demand via `compact`) reduces this:

| Step | What it does | Example |
|------|--------------|---------|
| **Dedup** | Collapse identical or near-identical payloads (cosine similarity > 0.95) | 5 copies of the same file-read response → 1 entry + count |
| **Merge** | Fuse related atomic facts into consolidated entries | "user wants X" + "user prefers Y" + "user likes Z" → single merged fact |
| **Decay** | Score entries by recency × access-frequency; prune below threshold | Old, never-retrieved tool outputs get pruned after retention window |
| **Re-index** | Rebuild FAISS + FTS5 over the compacted dataset | Smaller index = faster search |

Compaction is **lossless in spirit** — the raw originals stay in the DB (gated by retention policy) while the compacted view is what `recall` and `search` operate on. Think of it as a write-ahead log vs. a materialized view.

```
$ loc-ai-storage compact

Compaction pass (session: 2026-04-15)
  Entries before:     12,847
  Deduped:             3,201  (24.9%)
  Merged:                894  (7.0%)
  Decayed/pruned:        412  (3.2%)
  Entries after:       8,340  (35.1% reduction)
  Index rebuilt in:      1.2s
```

#### Cross-session memory (context injection)
Inspired by SimpleMem-Cross:
- Each proxy session gets a `session_id`.
- **On session close:** extract observations (decisions, errors, tool outputs) using an LLM or heuristic summarizer. Store as compacted memory entries.
- **On session start:** the `recall` command assembles a token-budgeted context bundle from compacted memory, ranked by relevance to the current task, and outputs it for injection into the agent.
- **Provenance tracking:** every memory entry links to source request/response IDs.
- **Continuous compaction:** as new sessions add observations, the compaction worker merges them with existing memory — so recall gets *denser and more useful* over time, not just bigger.

```
$ loc-ai-storage recall --project my-api --budget 2048

# Cross-session context (2,041 tokens, 14 entries, 6 sessions)
## Key decisions
- Chose PostgreSQL over DynamoDB for ACID guarantees (session 2026-04-10)
- JWT auth with RS256, 15-min expiry (session 2026-04-12)
## Known issues
- Rate limiter race condition under >100 rps (session 2026-04-14)
## Active patterns
- All endpoints return {data, error, meta} envelope
- Migrations use alembic with --autogenerate
```

The output is plain text (or JSON with `--format json`) that can be piped directly into an agent's system prompt or context file.

#### Token-savings analytics
Inspired by RTK's `rtk gain`, extended with compaction metrics:

```
$ loc-ai-storage gain

Session: 2026-04-15 (3h 12m)
  Requests captured:       847
  Raw tokens (est.):       312,400
  Wire-compressed tokens:   48,200  (84.6% wire savings)
  Compacted memory entries:  8,340  (vs. 12,847 raw — 35% compaction)
  Embedding cost (est.):     $0.02

Last 30 days:
  Wire savings:            ~2.1M tokens  ($6.30 saved at GPT-4o rates)
  Compaction savings:      ~890K tokens removed from recall index
  Memory entries:          34,210 raw → 18,400 compacted
```

---

## 4. Installation

```sh
# Core (interception + storage + search)
pip install loc-ai-storage

# With local embeddings (recommended — no API key needed)
pip install "loc-ai-storage[embeddings]"

# With all optional extras
pip install "loc-ai-storage[embeddings,code,export]"

# Editable / dev install from source
git clone https://github.com/<org>/loc-ai-storage.git && cd loc-ai-storage
pip install -e ".[dev]"
```

### pip extras

| Extra | Packages added | Purpose |
|-------|---------------|---------|
| *(core)* | `mitmproxy`, `faiss-cpu`, `numpy`, `cryptography`, `python-dotenv`, `rich`, `lz4`, `brotli`, `tqdm` | Interception, compression, storage |
| `[embeddings]` | `sentence-transformers`, `torch` (CPU) | Local embedding computation |
| `[code]` | `tree-sitter`, `tree-sitter-python`, `tree-sitter-javascript` | AST-aware code compression |
| `[export]` | `pyarrow`, `fastapi`, `uvicorn` | Parquet export, MCP/HTTP server |
| `[dev]` | `pytest`, `ruff`, `mypy`, `pre-commit` | Development and testing |

> **conda note:** All packages above have pip wheels. Only fall back to conda if you are on an exotic platform where `faiss-cpu` or `torch` wheels are unavailable (e.g., Alpine musl, POWER9). Even then, prefer `pip install faiss-cpu --extra-index-url ...` before reaching for conda.

---

## 5. CLI Interface

```
loc-ai-storage <command> [options]

Commands:
  proxy        Start the compressed-token proxy (default port 8080)
  search       Hybrid search over captured traffic
  recall       Inject compacted cross-session memory into agent context
  compact      Run compaction pass (dedup, merge, decay) on stored history
  gain         Token-savings + compaction analytics dashboard
  retain       Run retention/purge policy
  export       Export to JSON-lines or Parquet
  serve        Start MCP / HTTP API server
  config       Show / edit configuration
  version      Print version and provenance info
```

---

## 6. Configuration

`interceptor/config.yaml` — single config file:

```yaml
proxy:
  listen_port: 8080
  include_hosts: ['api.github.com', 'copilot-proxy.*', 'api.anthropic.com']
  exclude_hosts: ['avatars.githubusercontent.com']
  max_body_bytes: 1_048_576  # 1 MB

storage:
  db_path: './data/interceptor.db'
  encryption: true               # requires INTERCEPTOR_KEY env var
  retention_days: 90
  max_db_size_mb: 2048

compression:
  wire:
    enabled: true                  # compress outbound prompts on the wire
    strategy: 'restructure'        # restructure | content-encoding | none
    # restructure: semantic dedup of prompt content (always works)
    # content-encoding: set Content-Encoding header (provider must support)
  storage:
    json: 'structural'             # structural | zlib | brotli | lz4
    code: 'ast'                    # ast | zlib | none  (ast requires [code] extra)
    text: 'zlib'                   # zlib | brotli | lz4
    fallback: 'zlib'

embeddings:
  backend: 'local'               # local | openai | hash
  model: 'all-MiniLM-L6-v2'     # sentence-transformers model name
  dim: 384
  batch_size: 64

search:
  default_top_k: 20
  rrf_k: 60                      # reciprocal-rank fusion constant
  enable_fts: true

compaction:
  auto: true                       # run compaction after each session close
  dedup_threshold: 0.95            # cosine similarity threshold for dedup
  decay_halflife_days: 30          # entries lose relevance over this period
  min_access_count: 0              # entries below this + past decay get pruned
  merge_related: true              # fuse related atomic facts

# Environment knobs for LLM-backed compaction summarization and scheduling
#
# `INTERCEPTOR_COMPACTION_INTERVAL` (seconds): when set (or `compaction_interval` in
# `compaction` config), the proxy starts a periodic, non-destructive compaction pass
# in the background. Default: disabled (0). The periodic run is best-effort and
# non-destructive by default (does not `prune` rows).
#
# LLM summarization env vars (optional, require `openai` extra):
# - `OPENAI_API_KEY`: required to enable LLM summarization
# - `OPENAI_SUMMARY_MODEL`: model to use (default: gpt-3.5-turbo)
# - `OPENAI_SUMMARY_MAX_TOKENS`: integer max tokens for the summary (default: 256)
# - `OPENAI_SUMMARY_TONE`: tone hint for the summarizer (default: concise)
# - `OPENAI_SUMMARY_TEMPERATURE`: float temperature for the LLM (default: 0.0)

memory:
  enabled: true
  max_context_tokens: 4096         # budget for cross-session recall
  summarizer: 'heuristic'          # heuristic | llm
  inject_on_start: true            # auto-generate recall on proxy session start
  project_scope: 'git'             # git | directory | manual
```

---

## 7. Inspirations & Credit

This project draws on ideas from the broader community around context compression, memory systems for LLMs, and hybrid retrieval. Specific implementations and patterns were synthesized to produce a local-first, pip-installable design for interception, compaction, and recall.

---

## 8. Consequences

### Pros
- **Pay for fewer tokens** — wire compression and prompt restructuring reduce billed token counts; decompress locally for full fidelity.
- **Compaction** — redundant context is continuously merged and simplified; recall gets denser, not bigger.
- **Cross-session memory** — `recall` injects compacted prior context, budgeted to fit your token window.
- **Single `pip install`** — no conda, no Rust toolchain, no external services required.
- **Local-first** — data never leaves your machine; optional encryption at rest.
- **Hybrid search** — semantic + full-text + metadata; better recall than any single index.
- **Token-savings visibility** — `gain` command quantifies wire savings + compaction ROI.
- **Composable** — works alongside Headroom (compression proxy) or RTK (CLI filtering) for deeper savings.

### Cons
- Requires trusting mitmproxy root CA for TLS interception — only use on machines you control.
- Embedding cost if using OpenAI backend (local `sentence-transformers` is free but needs ~500 MB disk for model).
- PII/secrets in captured traffic require careful retention policies and access control.
- `mitmproxy` adds ~5-15 ms latency per request (negligible for AI API calls).
- Wire compression savings are provider-dependent — restructuring prompts works universally but `Content-Encoding` requires provider support.
- Compaction is lossy at the *view* level (merged/pruned entries) but raw originals are retained until retention policy expires.

### Risks & Mitigations

| Risk | Likelihood | Mitigation |
|------|-----------|------------|
| CA compromise | Low | Document CA lifecycle; remove CA when not actively intercepting |
| PII in stored data | Medium | Auto-redact patterns (API keys, tokens, emails); configurable scrub rules |
| DB grows unbounded | Medium | Retention policy with TTL + max-size; `retain` CLI command |
| Embedding model download on first run | Low | Pre-download in Docker image; document `--no-embeddings` flag |

---

## 9. Roadmap

**Phase 1 — Core (done)**
- [x] mitmproxy addon with SQLite storage
- [x] Compression (zlib/brotli/lz4)
- [x] Optional Fernet encryption at rest
- [x] FAISS semantic search
- [x] Background ingest worker

**Phase 2 — Compressed transit + compaction**
- [ ] Wire compression proxy (prompt restructuring, Content-Encoding)
- [ ] Compaction worker (dedup, merge, decay/prune)
- [ ] `compact` CLI command
- [ ] Two-stage compression (wire + storage)
**Phase 2 — Compressed transit + compaction (progress)**
- [x] Wire compression proxy — prompt restructuring implemented (Content-Encoding support is provider-dependent; remaining work: provider Content-Encoding negotiation)
- [x] Compaction worker — implemented (`interceptor/compaction_worker.py`) with LLM fallback summarization and deterministic fallback
- [x] `compact` CLI command — implemented (`interceptor/compact.py`) and wired as `loc-compact` entrypoint
- [x] Two-stage compression — storage compression implemented; wire restructuring implemented. Content-Encoding path is partially implemented (see notes above).

**Phase 3 — Memory + search**
- [ ] Cross-session memory extraction + `recall` command
- [ ] Hybrid search (FTS5 + FAISS + metadata fusion)
- [ ] Local `sentence-transformers` embeddings (no API key)
- [ ] `gain` analytics with wire-savings + compaction metrics
**Phase 3 — Memory + search (progress)**
- [x] Cross-session memory extraction + `recall` command — `interceptor/recall.py` provides token-budgeted bundles from compactions and recent messages
- [x] Hybrid search — basic hybrid views implemented (`interceptor/search.py`) with FTS-like text search and FAISS semantic search; fusion logic is available for further tuning
- [x] Local embeddings — deterministic fallback provided in `interceptor/embeddings.py`; `sentence-transformers` supported as an optional extra (install `[embeddings]` to enable local models)
- [ ] `gain` analytics — token-savings reporting is partially available via `interceptor/metrics.py`; a polished `gain` CLI/dashboard remains to be completed

**Phase 4 — Packaging + integrations**
- [ ] `pip install loc-ai-storage` with extras
- [ ] MCP server for agent integration
- [ ] AST-aware code compression (`[code]` extra)
- [ ] Parquet export for offline analysis
- [ ] Auto-redaction / PII scrubbing
 - [x] AST-aware code compression (`[code]` extra) — basic Python tokenizer-based compressor implemented in `interceptor/code_compress.py`
 - [x] Parquet export for offline analysis — implemented in `interceptor/export_parquet.py` (falls back to JSONL if PyArrow unavailable)
 - [x] Auto-redaction / PII scrubbing — implemented via `interceptor/redact.py` and wired into `interceptor/mitm_addon.py`
**Phase 4 — Packaging + integrations (progress)**
- [x] Packaging metadata: `pyproject.toml` with console entry points and optional extras is present (pip packaging ready; publish requires PyPI token and release tag)
- [ ] MCP server for agent integration
- [ ] AST-aware code compression (`[code]` extra)
- [ ] Parquet export for offline analysis
- [ ] Auto-redaction / PII scrubbing
 - [x] AST-aware code compression (`[code]` extra) — basic Python tokenizer-based compressor implemented in `interceptor/code_compress.py`
 - [x] Parquet export for offline analysis — implemented in `interceptor/export_parquet.py` (falls back to JSONL if PyArrow unavailable)
 - [x] Auto-redaction / PII scrubbing — implemented via `interceptor/redact.py` and wired into `interceptor/mitm_addon.py`




---

## 10. References

- mitmproxy: https://pypi.org/project/mitmproxy/
- faiss-cpu: https://pypi.org/project/faiss-cpu/
- sentence-transformers: https://pypi.org/project/sentence-transformers/
- LanceDB: https://pypi.org/project/lancedb/ *(considered; SQLite+FAISS chosen for fewer deps)*
- tree-sitter: https://pypi.org/project/tree-sitter/
