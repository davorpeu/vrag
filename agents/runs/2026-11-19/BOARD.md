# Board: run 2026-11-19 (only the lead edits this file)

## Infra
- Docker stack: elasticsearch + docs-db up. Langfuse stack STOPPED (RAM; ~6.5 s flush stall per trace
  while it is down: use LANGFUSE_TRACING_ENABLED=false). Kibana stopped.
- vrag-eval: ready (13:49). data-retention, leave-policy, refund-policy v1-v3; semver/semver v1.0.0-beta,
  v1.0.0, v1.0.0-rc.1, v2.0.0 (commit-date order: rc.1's commit is newer than 1.0.0's); travel-expense-policy
  v1.0, v1.1, v2.0.
- Library (vrag-chunks) found: react/CHANGELOG 217 versions / 37,297 chunks with NO latest flag (the load
  stopped before refresh_latest), react/CLAUDE 7 versions (mixed commits + tags), plus the eval docs.
- Lead edits made before wave 1: config.py (docs_s3_* settings), pyproject.toml (minio), .env/.env.example
  (DOCS_S3_*), minio 7.2.20 installed; eval scripts + e2e conftest default to ES_INDEX=vrag-eval;
  new scripts/build_eval_index.py. .gitignore: data/evals/, data/models/, agents/runs/*/llm.lock (done, no need to ask).

## Running
| id | task | owner | files owned |
|----|------|-------|-------------|
| L1 | stack up, vrag-eval, baseline fast + e2e on a clean HEAD worktree | lead | scripts/build_eval_index.py, tests/e2e/conftest.py, scripts/eval_router.py, scripts/eval_diff.py, .gitignore, STATE.md, agents/** |
| W1 | S3 / MinIO version source | worker s3 | src/vrag/sources/s3.py (new), src/vrag/cli.py, docker-compose.yml, scripts/seed_minio.py (new), tests/test_s3_source.py (new), tests/e2e/test_s3_source_live.py (new), data/golden/changes.json, data/golden/router_cases.json |
| W2 | retrieval eval + reranker | worker rerank | src/vrag/rerank.py (new), src/vrag/store.py, src/vrag/answer.py, src/vrag/config.py, pyproject.toml, .env.example, scripts/eval_retrieval.py (new), data/golden/retrieval_cases.json (new), tests/test_rerank.py (new), tests/test_store.py (new) |
| W3 | offline answer eval set + runner (report-only for app code) | worker evals | src/vrag/evals.py (new), scripts/eval_answers.py (new), data/golden/answer_cases.json (new), tests/e2e/test_answers.py, tests/test_evals.py (new) |

Not owned by anyone right now (ask the lead): router.py, diff.py, grounding.py, chunking.py, extract.py,
sync.py, models.py, api.py, sources/{base,folder,git,db,remote}.py, ui/**, other tests, scripts/seed_db.py,
scripts/make_synthetic_docs.py, .env.

## Queue (next first) - FROZEN: the user chose to stop after W1-W3 (13:59). For a later run.
1. Q1 Code review of HEAD (correctness + security), report only -> reports/Q1-review.md.
2. Q2 LLM context: set num_ctx explicitly; compact router catalog (react/CHANGELOG has 226 versions, the
   full catalog goes into the router prompt and can overflow Ollama's default context). Files: router.py,
   answer.py (after W2).
3. Q3 S3 source in API + UI ("add from bucket"), after W1. Files: api.py, ui/streamlit_app.py, tests/test_api_*.py, tests/test_ui.py.
4. Q4 Fixes for failures found by W3's eval (split by file).
5. Q5 "History" questions: "when did X change?" across all versions (router + answer + diff).
6. Q6 Upload a new version of a document from the UI (after Q3; same files).
7. Q7 README.md: setup, architecture, sources, evals (late in the run).
8. L2 (lead, when Ollama is idle) finish the react/CHANGELOG load in vrag-chunks (stopped at 59/226 on
   2026-11-06; is_latest never set) and re-sync react/CLAUDE by tags (mixed versions).
9. L3 (lead) API + UI running in herdr panes for the user's router check (STATE "Left 1").

## Done
(none yet)
