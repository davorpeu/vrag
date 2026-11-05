# State (updated 2026-11-05)

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

User checked Milestone 1 in Streamlit on 2026-11-05: good. Committed (f1d7f90).

## Milestone 2 (git source) - done 2026-11-05
- src/vrag/sources/git.py: GitSource(repo, files, by="tags"|"commits"). doc_id = "<repo dir>/<path without ext>".
  tags: one version per tag containing the file, ordered by commit date. commits: every commit touching
  the file, follows renames, label = tag name or short SHA. source_date = git %cs (local day, not UTC).
- Markdown: extract .md/.txt; chunking.split_markdown splits on ATX/setext headings <= level 3 (title = 0),
  drops heading-only sections, splits long sections per numbered list item (numbered in order: "3.9").
- CLI: `vrag sync git --repo data/repos/semver --file semver.md --by tags`
- Test repo: semver/semver cloned to data/repos/semver (gitignored). 4 tags indexed (107 chunks); only v2.0.0 is latest.
- 8 tests pass. Semver Q&A cites v2.0.0 rules correctly; policy Q&A still correct.
- Memory: the PC is tight (1.8 GB free with the stack + Ollama). Kibana is left stopped (not needed).

Left:
1. Next milestone: query router (latest / specific version / compare / unclear). semver now gives
   real version questions ("what did 1.0.0 say about pre-releases?", "what changed from 1.0.0 to 2.0.0?").
2. Later sources: DB (Postgres), then MinIO.
