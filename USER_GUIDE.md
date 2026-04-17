Loc-AI-Storage — User Guide
===========================

Overview
--------
This repository captures intercepted HTTP(S) flows, compresses and stores tokens/messages, supports chunked content-addressed storage, computes deterministic embeddings, and enables semantic search with a FAISS-backed index. It runs locally (venv) or in Docker and includes a simple CLI and background persister for FAISS.

Quick links
-----------
- CLI entrypoint: [interceptor/cli.py](interceptor/cli.py)
- FAISS index implementation: [interceptor/faiss_index.py](interceptor/faiss_index.py)
- DB schema & helpers: [interceptor/db.py](interceptor/db.py)
- Token compaction: [interceptor/token_compact.py](interceptor/token_compact.py)
- Chunker & migration: [interceptor/chunker.py](interceptor/chunker.py)
- Workers: [interceptor/tokenize_worker.py](interceptor/tokenize_worker.py), [interceptor/ingest_worker.py](interceptor/ingest_worker.py)
- Search: [interceptor/search.py](interceptor/search.py)

Prerequisites
-------------
- Python 3.10+ (virtualenv recommended).
- Optional for best performance: `faiss-cpu`, `brotli`, `lz4`, `tiktoken`, and `cryptography`. The code provides fallbacks where possible but installing optional packages improves performance and features.

Local setup (recommended)
-------------------------
1. Create and activate virtualenv:

```bash
python -m venv .venv
source .venv/Scripts/activate   # Windows (Git Bash) or
.venv/bin/activate              # macOS / Linux
```

2. Install dependencies (adjust to your environment):

```bash
pip install -r requirements.txt
# Optional (faiss, fast compressors):
pip install faiss-cpu brotli lz4 tiktoken cryptography
```

3. Run tests to verify setup:

```bash
python -m pytest -q
```

Docker
------
A Dockerfile and docker-compose are included for convenience (see repository root). Build and run according to the included README/docker-compose definitions.

How it works (high level)
-------------------------
- mitmproxy addon intercepts flows and stores messages to the SQLite DB via `DB.store_message` (see [interceptor/mitm_addon.py](interceptor/mitm_addon.py)).
- Messages can be chunked (content-addressed chunks) with `chunk_bytes()` and stored in the `chunks` table; messages reference chunks via `message_chunks`.
- Tokenization and compaction reduce space; deterministic embeddings are computed for chunk-level vectors which are mapped into FAISS for semantic search.
- FAISS persistence supports atomic writes and an optional background persister that periodically flushes in-memory changes to disk.

CLI (commands)
---------------
The CLI module is `interceptor/cli.py`. Run it as a module:

```bash
python -m interceptor.cli <command> [flags]
```

Common commands:

- `list` — List captured messages (metadata only)
  - Example: `python -m interceptor.cli list`

- `show <id>` — Show message content and headers
  - Example: `python -m interceptor.cli show 1`

- `migrate-chunks` — Migrate existing messages into the chunk store
  - Flags: `--limit` (max messages), `--chunk-size` (bytes)
  - Example: `python -m interceptor.cli migrate-chunks --limit 100 --chunk-size 4096`

- `tokenize` — Run tokenize worker once
  - Flags: `--limit`, `--encoding` (default gpt2)
  - Example: `python -m interceptor.cli tokenize --limit 200`

- `ingest` — Run ingest worker once (computes embeddings & indexes into FAISS mapping)
  - Example: `python -m interceptor.cli ingest --limit 200`

- `search <query>` — Semantic search; returns chunks -> messages mapping with snippets
  - Example: `python -m interceptor.cli search "fix memory leak"`

FAISS-specific
--------------
- `faiss-rebuild` — Rebuild the FAISS index from stored chunk embeddings.
  - Flags:
    - `--dim` — embedding dimension override
    - `--background-persist` — enable the background persister (daemon thread)
    - `--persist-interval` — background interval in seconds (default 60)
  - Example (rebuild and persist in background):

```bash
python -m interceptor.cli faiss-rebuild --background-persist --persist-interval 30
```

- `faiss-status` — Show FAISS index status and last save/rebuild meta.
  - Also accepts `--background-persist` and `--persist-interval` to enable the persister when inspecting status.
  - Example:

```bash
python -m interceptor.cli faiss-status --background-persist
```

Background persister behavior
-----------------------------
- When `background_persist=True` is passed to `FaissIndex`, a daemon thread is started that periodically checks for changes (`_dirty`) and calls the same atomic `_save()` used by the immediate saver.
- On process exit, `atexit` triggers `FaissIndex.stop()` which signals the worker to stop, joins the thread, and performs a final flush.
- Use `--persist-interval` to control how frequently the persister flushes changes.

Recommended usage patterns
--------------------------
- Small setups: prefer immediate saves (background persister off). Run `faiss-rebuild` when needed.
- Long-running capture & ingest: enable `--background-persist` with a reasonable interval (30–120s) to reduce disk churn while still keeping persisted state.
- For graceful shutdowns: ensure long-running processes call `FaissIndex.stop()` or let the atexit hook run (the CLI commands use module-level invocation which triggers atexit).

Database & file locations
-------------------------
- SQLite DB path: defaults to `./interceptor_storage.db` (can be overridden by env/DB helper as needed).
- FAISS index file: defaults to `./interceptor/faiss.index`; mapping defaults to `./interceptor/faiss_map.json`; meta file is `./interceptor/faiss_map_meta.json`.

Troubleshooting
---------------
- Tests failing: run `python -m pytest -q` and inspect failing test. Many features have fallbacks if optional libraries are missing.
- FAISS not installed: the code attempts to import `faiss`; if not available, a numpy-based fallback index will be used but with reduced performance.
- Native wheel build issues on Windows (faiss/tiktoken): install prebuilt wheels where possible or run in Docker.
- Background persister not flushing: ensure `--background-persist` is set when constructing `FaissIndex` (CLI flags call the constructor accordingly). Long-running workers that instantiate `FaissIndex` should pass the same flags or call `stop()` on shutdown.

Extending and development
-------------------------
- Schema and DB helpers live in [interceptor/db.py](interceptor/db.py). Add or migrate tables here.
- Chunking is implemented in [interceptor/chunker.py](interceptor/chunker.py).
- Add more robust embedding providers in [interceptor/embeddings.py]. The repository currently uses deterministic local embeddings to avoid external API keys.
- For larger corpora, consider moving FAISS out-of-process or using more advanced FAISS indices (IVF, PQ) for scaling.

Contributing
------------
- Fork, create a feature branch, add tests, and open a PR.
- Keep changes minimal and include unit tests for new behavior. Run `pytest` before submitting.

FAQ
---
Q: How do I enable encryption at rest?
A: The repository includes optional encryption hooks; add a Fernet key to the environment and update the DB write/read helpers to call encryption/decryption as needed.

Q: Can I index messages instead of chunks?
A: Yes — `FaissIndex.rebuild_from_db` will build an index from message-level embeddings (if available). Chunk-level embeddings reduce duplication and increase reuse.

Contact
-------
For help, open an issue in the repository or ping the maintainer in the project metadata.


