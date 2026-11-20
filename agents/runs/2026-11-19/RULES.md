# Run 2026-11-19: lead loop until the weekly usage reset

The user asked for a loop that works as hard as possible until their weekly usage resets, with
max-effort Opus subagents. The lead is the Claude session in herdr pane w2J:p1. It assigns work
in BOARD.md, reads each worker's final report, verifies, integrates, and updates STATE.md.

STOP: the weekly usage reset was ~14:00. The user chose (13:59): W1, W2 and W3 finish their current
tasks, the lead integrates and verifies them, then the loop stops. No new tasks start (the queue in
BOARD.md is for a later run). Workers: deliver what your brief's acceptance asks for, no extra scope.
Commits: only the lead, at the end, on a work branch (user approved). Workers never commit.

## Everyone
- Read first: `agents/RULES.md` (project rules: no human-time estimates), `STATE.md`, this file, `BOARD.md`.
- Edit only the files BOARD.md gives you. You need a change in a file you don't own? Don't make it:
  write the exact change under "Requests to lead" in your final report. New files you create are
  yours; list them in the report.
- The working tree is shared by everyone at once. No git commit, stash, reset, checkout, clean,
  branch switch or rebase. Read-only git (status, diff, log, show) is fine.
- Python: `.venv/Scripts/python` (3.10). Fast tests: `.venv/Scripts/python -m pytest -q` (~10 s, no
  services). Other workers are editing other files right now: a failure in a file you don't own
  is not yours to fix. Note it in your report.
- Installing a package: dry-run first (`pip install --dry-run ...`). If it would upgrade or downgrade an
  installed package, don't install; ask the lead in your report.
- Write all text (reports, docs, comments) in plain, simple English.

## Services (Docker Desktop; the lead starts the stack)
- Elasticsearch 127.0.0.1:9200
  - `vrag-chunks` = the user's library (UI/API). Never write to it, never delete it.
  - `vrag-eval` = the eval corpus (folder corpus, semver tags, docs-db), built by
    `scripts/build_eval_index.py`. Only the lead writes to it. Read it freely: e2e tests and
    `scripts/eval_*.py` use it by default.
  - Your scratch index: `vrag-dev-<your task id>` (set `ES_INDEX`). Delete it when you are done.
  - `ES_INDEX` from the environment wins over `.env`.
- Ollama 127.0.0.1:11434 (qwen2.5:7b + nomic-embed-text) is one local GPU model shared by everyone.
  An LLM-heavy run (e2e suite, eval_router, answer evals, more than about 10 questions) needs the lock:
  create `agents/runs/2026-11-19/llm.lock` holding your task id and the start time, and delete it when done.
  If the lock exists and is less than 45 minutes old, do other work and try later. Embedding-only
  work (sync, retrieval eval) does not need the lock.
- docs-db Postgres 127.0.0.1:5433 (seeded). docs-minio comes with task W1.
- Langfuse is STOPPED (RAM). Anything that calls answer.ask, `vrag ask` or the API must run with
  `LANGFUSE_TRACING_ENABLED=false`, otherwise every trace flush waits ~6.5 s and logs an export error.
  (tests/e2e/conftest.py sets it automatically when Langfuse is down.)
- RAM is tight (16 GB, about 0.5 GB free with ES running). Don't start Kibana or the Langfuse stack;
  ask the lead in your report if you need them. Stop any server you start. `docker compose up -d <service>`
  only for a service you own; never `docker compose down`, never `-v`.
- Other herdr workspaces belong to other projects: never touch them.

## Final report (your last message, also saved to `agents/runs/2026-11-19/reports/<task id>.md`)
1. What you did and what you decided (with reasons).
2. Files created / edited.
3. Tests and evals you ran, with the exact numbers.
4. What is unfinished or wrong, and known risks.
5. Requests to lead: exact edits for files you don't own.
6. Follow-up ideas, most valuable first.
