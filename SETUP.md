# Setting up vrag on a new machine

These instructions are for Claude Code (or a person) setting this project up from a fresh clone.
Do the steps in order and check the "Expect" line after each one. Stop and report if a check fails.
Then read `CLAUDE.md`, `agents/RULES.md` and `STATE.md` before doing any other work.

## What the app is
A local RAG app (retrieval-augmented generation: it searches the documents, then a model answers from them)
for documents that change over time. Every answer is tied to a specific document version, and comparisons
between versions are computed in code. A second feature, "Repo activity", loads a GitHub repo's recent
commits and answers "what was done recently, and in which files". Everything runs locally: Ollama (LLM +
embeddings), Elasticsearch, Postgres and MinIO in Docker. No paid APIs.

## 0. Requirements
- Git, Python 3.10 or newer, Docker Desktop (running), Ollama.
- RAM: 16 GB works but is tight. The stack + Ollama with qwen2.5:7b need about 7-8 GB.
  Don't start Kibana or the Langfuse stack unless needed (see step 9).
- Disk: about 10 GB (Docker images ~3 GB, Ollama models ~5 GB).
- Paths below use Windows venv paths (`.venv/Scripts/...`). On macOS/Linux use `.venv/bin/...`.

## 1. Clone
```
git clone <repo url> versioned-rag
cd versioned-rag
git checkout run-2026-11-20   # latest work; master is older until this branch is merged
```

## 2. Python environment
```
python -m venv .venv
.venv/Scripts/python -m pip install -U pip
.venv/Scripts/python -m pip install -e ".[dev]"
```
Expect: `.venv/Scripts/vrag --help` lists the `sync`, `ask` and `activity` commands.

## 3. Settings
```
cp .env.example .env
```
The defaults match docker-compose.yml. Keep `127.0.0.1` (not `localhost`) in `DOCS_DB_URL`: on Windows,
localhost tried IPv6 first and the connection hung.

## 4. Ollama models
```
ollama pull qwen2.5:7b
ollama pull nomic-embed-text
```
Expect: `curl http://127.0.0.1:11434/api/tags` lists both.

## 5. Docker services
```
docker compose up -d elasticsearch docs-db docs-minio
```
Expect: `curl http://127.0.0.1:9200` answers within about 30 s. docs-db (Postgres) listens on 5433,
docs-minio on 9100 (console 9101). The docs-db schema is created automatically from `db/init/`.
MinIO uses `cgr.dev/chainguard/minio` because `minio/minio` is no longer on Docker Hub.

## 6. Test data
```
git clone https://github.com/semver/semver data/repos/semver   # git source test repo (data/repos is gitignored)
.venv/Scripts/python scripts/seed_db.py                        # travel-expense-policy v1.0/v1.1/v2.0 in Postgres
.venv/Scripts/python scripts/seed_minio.py                     # it-security-policy v1.0/v1.1/v2.0 in MinIO
.venv/Scripts/python scripts/build_eval_index.py               # the eval index vrag-eval (all of the above)
```
Expect: build_eval_index prints 6 documents: data-retention, leave-policy, refund-policy (v1-v3),
semver/semver (4 tags), travel-expense-policy, it-security-policy.

## 7. The user's library (what the UI shows)
The UI and API use the index `vrag-chunks` (separate from `vrag-eval`). Fill it with the sample docs:
```
LANGFUSE_TRACING_ENABLED=false .venv/Scripts/vrag sync folder
LANGFUSE_TRACING_ENABLED=false .venv/Scripts/vrag sync git --repo data/repos/semver --file semver.md --by tags
LANGFUSE_TRACING_ENABLED=false .venv/Scripts/vrag sync db
LANGFUSE_TRACING_ENABLED=false .venv/Scripts/vrag sync s3
```
(PowerShell: set `$env:LANGFUSE_TRACING_ENABLED="false"` first.) More repos can be added from the UI.
The old machine's library (e.g. react) is not transferred; add it again from the UI if wanted.

## 8. Check that everything works
```
.venv/Scripts/python -m pytest -q                       # fast suite, no services: expect 239 passed
.venv/Scripts/python scripts/eval_diff.py               # no LLM: every version pair OK
LANGFUSE_TRACING_ENABLED=false .venv/Scripts/python scripts/eval_router.py   # LLM: expect 28/28
```
Optional, slower (about 10 min): `scripts/eval_answers.py` (expect about 36/37; the 7B model varies a little)
and `.venv/Scripts/python -m pytest -m e2e`.

## 9. Run the app
Two terminals (keep them open; they are long-running servers):
```
LANGFUSE_TRACING_ENABLED=false .venv/Scripts/python -m uvicorn vrag.api:app --host 127.0.0.1 --port 8000
API_URL=http://127.0.0.1:8000 .venv/Scripts/streamlit run ui/streamlit_app.py --server.port 8501
```
Open http://localhost:8501. Documents: pick one in the sidebar and ask. Repo activity: switch it on at the
top of the sidebar, paste a GitHub repo link, Load, then ask e.g. "Describe what has been done recently
and in which files". Use moderate repos (the load caps at 500 commits).

Tracing (optional): `docker compose up -d` starts everything, including Langfuse (http://localhost:3000,
keys pre-seeded: pk-lf-local-dev / sk-lf-local-dev) and Kibana. It needs several GB of extra RAM. With
Langfuse running, drop `LANGFUSE_TRACING_ENABLED=false`. Without it, keep the flag: otherwise every trace
waits about 6.5 s and logs an export error.

Optional reranker: `RERANK=true` in `.env` (+0.8 GB RAM, about 1.5 s per search; the model downloads once into
data/models).

## 10. Working on the project with Claude
- `CLAUDE.md` loads `agents/RULES.md` (project rules) and points to `STATE.md` (current state and next steps).
- `agents/runs/<date>/` holds the multi-agent runs: BOARD.md (who owns which files), briefs, worker reports.
  The workflow: one lead session assigns tasks with file ownership; each worker is a separate `claude`
  session (on the old machine, herdr workspaces). Workers never commit; the lead verifies and commits.
- Evals in `data/golden/` + `scripts/eval_*.py` are the way to check that a change helped. Run them against
  `vrag-eval`, never against the user's library `vrag-chunks`.
- Next tasks are listed at the end of `STATE.md`.
