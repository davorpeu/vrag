# State (updated 2026-11-04)

## Decisions
- Python 3.10 venv (.venv), LangChain + Ollama (qwen2.5:7b answers, nomic-embed-text embeddings), no paid APIs.
- Elasticsearch hybrid BM25 + kNN, RRF fused in Python (works on Basic license). Reranker later.
- Version sources via `VersionSource` adapter, build order: folder -> git -> DB (Postgres) -> MinIO.
  Each adapter emits a stable doc_id. Effective date from document text, falling back to the source date.
- Sync: CLI, incremental, idempotent by content hash.
- Query router (latest / specific version / compare / unclear) with fixed pipelines: later milestone.
- Validation: Pydantic schema + citation check (done, basic); groundedness check + offline eval set later.
- FastAPI + Streamlit UI. Langfuse self-hosted, keys pre-seeded (pk-lf-local-dev / sk-lf-local-dev).

## Milestone 1 (folder source, latest-only Q&A with citations, traced)
Done:
- All code written (src/vrag, ui/, scripts/, tests/), 3 unit tests pass.
- Synthetic corpus generated: 3 docs x 3 versions in data/corpus, ground truth in data/golden/changes.json.
- Extraction and chunking checked on all 9 files (PDF + DOCX).
- docker compose stack started (switched MinIO to cgr.dev/chainguard/minio; minio/minio is gone from Docker Hub).

Done 2026-11-01:
- Docker Desktop must be running first (`docker compose up -d`). Langfuse healthy at :3000.
- `vrag sync folder` indexed 9 versions; a second run skips all 9 (idempotent).
- Fixed for langfuse 4.x: `update_current_trace` is gone, so tags now go through
  `propagate_attributes` (answer.py). pyproject pins langfuse>=4,<5.
- Fixed empty citations from qwen2.5:7b: `LLMAnswer.citations` is now required and comes before `answer`.
- CLI test questions (refund 21d, deletion 30d, leave 2 weeks) cite v3. An off-topic question is refused.
- The API at :8000 and Streamlit at :8501 work; the trace in Langfuse has the ask/retrieve/answer-latest spans and the pipeline:latest tag.

Left:
1. User check: ask 3-4 questions at http://localhost:8501, see the v3 citations, and click the trace link
   (Langfuse login dev@localhost.dev / devpassword).
2. First commit (nothing committed yet).
3. Next milestone: git VersionSource adapter.

2026-11-04: stack, API and UI restarted and smoke-tested (refund question cites v3). Waiting for the user's check.
After a restart: start Docker Desktop, run `docker compose up -d`, wait for :3000/api/public/health to return 200, then
start commands: `.venv/Scripts/uvicorn vrag.api:app --port 8000` and
`.venv/Scripts/streamlit run ui/streamlit_app.py`
