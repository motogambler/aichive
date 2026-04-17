# CI / Releases
![CI](https://github.com/<OWNER>/<REPO>/actions/workflows/ci.yml/badge.svg)
![Release](https://img.shields.io/pypi/v/loc-ai-storage.svg)
![License](https://img.shields.io/badge/license-MIT-green.svg)

# Locally intercept GitHub Copilot traffic (proof-of-concept)

This repository provides a mitmproxy-based interceptor that can capture inbound/outbound HTTP(S) traffic, compress bodies losslessly, store them in a SQLite DB, and provide a simple text search CLI. Optional FAISS/vector search and embeddings can be added.

Quick start (Docker):

1. Build and run:

```sh
docker compose up --build
```

2. Configure your system or application to use the HTTP proxy at `http://localhost:8080` (mitmproxy). For TLS interception you must install mitmproxy's CA into your OS/trusted store — follow mitmproxy docs: https://docs.mitmproxy.org

3. Interact with Copilot (or other client) and captured requests/responses are saved to `interceptor_storage.db` in the running container (and mounted into the project when using compose).

Local run (no Docker):

1. Create a virtualenv and install deps:

```sh
python -m venv .venv
source .venv/bin/activate
pip install -r interceptor/requirements.txt
```

2. Run mitmdump with the addon:

```sh
mitmdump -s interceptor/mitm_addon.py --listen-port 8080
```

Search examples:

```sh
python interceptor/search.py text-search "my prompt"
```

Security & notes:
- Intercepting Copilot traffic requires configuring the client to use a proxy and trusting a generated certificate; be careful and do this only on machines you control.
- This is a proof-of-concept; if you want real-time compression, vector search (FAISS), or embedding generation (OpenAI/local), I can add an optional embedding worker and FAISS index.

## Release

To create a release and publish to PyPI (the release workflow is triggered by tagging):

```bash
# Bump version in pyproject.toml, then:
git tag v0.1.0
git push --tags origin main
```

The repository contains a `.github/workflows/release.yml` workflow that will build and publish the package on a push to a `v*` tag; configure `PYPI_API_TOKEN` in repo secrets to enable publishing.

Tokenization note:
- `tiktoken` is optional and not required to run the basic tokenizer; if `tiktoken` is installed it will be used for GPT-style tokenization. Otherwise the project falls back to a lossless UTF-8 byte-packing tokenization so tokenization and compression still work without Rust or native build tools.

TLS / CA and key management
- To intercept TLS you must install mitmproxy's root CA into your OS/browser trust store. Follow mitmproxy docs: https://docs.mitmproxy.org. Do this only on machines you control and remove the CA when finished.
- Encryption at rest: set `INTERCEPTOR_KEY` env var to a Fernet key (base64) to enable encryption of stored bodies. Generate one with:

```sh
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

- Store `INTERCEPTOR_KEY` securely (OS secret manager or environment in systemd unit). Do not commit the key.

Scheduling & retention
- See `docs/systemd-and-cron.md` for systemd and cron examples to run the ingest worker and retention daily.

## Local development & quickstart

A compact set of commands to build, run, and verify the project locally.

Setup (repo root):

```bash
python -m venv .venv
source .venv/Scripts/activate    # PowerShell: .venv\Scripts\Activate.ps1
pip install -U pip setuptools wheel
pip install -e ".[dev]"         # editable install with dev extras
```

Run tests and lint:

```bash
pytest -q
ruff .
```

Verify package import:

```bash
python -c "import interceptor; print(interceptor.__file__)"
```

Start the minimal MCP test server (in one shell):

```bash
python -m interceptor.mcp_server --port 0
# note the printed bound address (host, port)
```

Export captured messages to the MCP test server (in another shell):

```bash
# use the address printed by the test server; example: http://127.0.0.1:56789/ingest
python -m interceptor.export --mcp http://127.0.0.1:56789
```

Run the production FastAPI MCP app (requires extras `fastapi,uvicorn`):

```bash
python -m interceptor.mcp_app --port 8080
# optionally set INTERCEPTOR_MCP_TOKEN to enable Bearer token auth
```

Test redaction and policies:

```bash
python -m interceptor.redact_cli test "contact me at alice@example.com"
python -m interceptor.redact_cli add "password:\\s*\\S+" --replace "<REDACTED-PASSWORD>"
python -m interceptor.redact_cli list
```

Parquet export (falls back to JSONL if `pyarrow` not installed):

```bash
python -m interceptor.export_parquet --out out.parquet
```

Run mitmproxy with the addon (manual integration):

```bash
mitmdump -s interceptor/mitm_addon.py --listen-port 8080
```

If you want, I can add a runnable example script `examples/quick_integration.py` that starts the test server, seeds fake messages, runs `export_to_mcp`, and asserts receipt. Want me to add that? 
