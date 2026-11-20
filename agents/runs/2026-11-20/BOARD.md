# Board: run 2026-11-20 (only the lead edits this file)

## Infra
- Docker: elasticsearch + docs-db up (lead, 2026-11-20). Langfuse stack and Kibana STOPPED.
- Ollama up (qwen2.5:7b + nomic-embed-text). LLM lock: agents/runs/2026-11-20/llm.lock.
- Indexes: vrag-eval (lead only writes), vrag-chunks (user's library: never write).
- Lead edits already in the tree from run 2026-11-19: config.py (docs_s3_*), pyproject.toml (minio),
  .env/.env.example (DOCS_S3_*), eval scripts + e2e conftest default to vrag-eval, scripts/build_eval_index.py.

## Running (herdr workspaces, claude on Sonnet)
| id | task | files owned |
|----|------|-------------|
| W1 | S3 / MinIO version source | src/vrag/sources/s3.py, src/vrag/cli.py, docker-compose.yml, scripts/seed_minio.py, tests/test_s3_source.py (new), tests/e2e/test_s3_source_live.py (new), data/golden/changes.json, data/golden/router_cases.json |
| W2 | retrieval eval + reranker | src/vrag/rerank.py, src/vrag/store.py, src/vrag/answer.py, src/vrag/config.py, pyproject.toml, .env.example, scripts/eval_retrieval.py, data/golden/retrieval_cases.json, tests/test_rerank.py (new), tests/test_store.py (new) |
| W3 | offline answer eval set + runner | src/vrag/evals.py, scripts/eval_answers.py, data/golden/answer_cases.json (new), tests/e2e/test_answers.py, tests/test_evals.py (new) |

Not owned by anyone (ask the lead): router.py, diff.py, grounding.py, chunking.py, extract.py, sync.py,
models.py, api.py, sources/{base,folder,git,db,remote}.py, ui/**, other tests, scripts/build_eval_index.py,
scripts/seed_db.py, scripts/eval_router.py, scripts/eval_diff.py, tests/e2e/conftest.py, .env.

## Queue (later)
Q1 code review, Q2 num_ctx + compact router catalog, Q3 S3 in API+UI, Q4 fixes from W3 eval,
Q5 history questions, Q6 upload version from UI, Q7 README, L2 react fix in vrag-chunks, L3 user router check.

## Done
- W1 S3 source (report reports/W1.md). Lead verified: test_s3_source 14 pass. Lead applied request:
  S3Source added to scripts/build_eval_index.py. PENDING (after W2+W3 finish, they read vrag-eval now):
  `docker compose up -d docs-minio`, seed_minio.py, build_eval_index.py, eval_diff (expect 10/10),
  eval_router 28 cases under the LLM lock, e2e test_s3_source_live.
- W2 rerank (reports/W2.md). Lead verified: fast suite 168 pass. RERANK=false default (+0.8 GB RAM, ~1.5 s/search).
  Rerank: MRR 0.803 -> 0.927. Finding: kNN-only beats current hybrid (R@6 0.978 vs 0.899) -> new queue item
  Q8 weighted RRF / kNN-heavier fusion. answer.py changed during W3's eval run (behaviour same with rerank off).
