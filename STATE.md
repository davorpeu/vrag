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

## Milestone 3 (query router) - done 2026-11-05
- router.py: LLM classify (intent latest|version|compare|unclear, document, versions, as_of) with the
  catalog of indexed docs/versions in the prompt; then resolve() in plain code checks it against the catalog.
  Version mentions must appear in the question text (the LLM copied "2.0.0" from the semver title);
  explicit unknown versions ("version 7") -> unclear + list of real versions; dates pick the version in
  effect; "previous/old" -> second-newest; one version in a compare -> compared with the one before it.
- diff.py: section pairing by text similarity, then clause-level changes; version-label/date-only lines ignored.
- answer.py pipelines: latest | version (search filtered to doc+version; "previous/old" in the question is
  rewritten to the version label for the LLM) | compare (citations built from the diff, LLM only summarises;
  general questions include all changes; any change the summary skips is appended as "Also changed:";
  a summary that stops at ":" gets the before/after list) | unclear (no LLM answer).
- UI shows a route banner (type, doc, versions, note). CLI prints [route: ...].
- Evals: scripts/eval_router.py (16/16 on data/golden/router_cases.json, ~1.5 min, uses the LLM) and
  scripts/eval_diff.py (6/6 version pairs vs data/golden/changes.json, no LLM). 41 unit tests pass.

## Automated tests - added 2026-11-05
- Fast (default `pytest`, ~10 s, no services): 41 unit tests + tests/test_ui.py (Streamlit AppTest with a
  fake API: route banner, answer, citations, unclear, API error).
- End-to-end (`.venv/Scripts/python -m pytest -m e2e`, ~4 min, needs ES + Ollama + synced corpus and semver):
  tests/e2e/test_answers.py, 15 questions through the real API. Checks route, versions, facts in the answer,
  citations only from the routed versions, both sides cited in comparisons, and every quote found verbatim
  in the cited chunk. 15/15 pass.
- Fixes found by the e2e run: off-topic questions were routed "unclear" (router prompt examples), and the
  model's insufficient_context flag was unreliable (now forced true when there is no valid citation).
- Router eval now has 17 cases (17/17).

Left:
1. User check of the router in Streamlit (API + UI must be started; run them in your own terminals so
   Claude Code's low-memory cleanup doesn't stop them).
2. Groundedness check (quotes must really appear in the cited chunk) + answer-level eval set.
3. More sources: DB (Postgres), then MinIO.
