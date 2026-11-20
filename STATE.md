# State (updated 2026-11-05)

Rules: read `agents/RULES.md` first.

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

## Groundedness check - done 2026-11-05
- grounding.py (no extra LLM call): every quote must be in the cited chunk (near-misses repaired to the
  exact source text, invented quotes dropped); every number in the answer must be in the cited sources
  (text + version/date/section) or the question. Number words handled (two, twenty-one).
- latest/version: on failure, one retry with the problems as feedback; best result kept.
  Model cited nothing -> the text is not shown, replaced by "I couldn't find an answer..." (not_checked).
- compare: citations come from the diff; a summary with a number not in the changed clauses is replaced
  by the exact before/after list.
- AskResponse.grounding {status grounded|partial|ungrounded|not_checked, issues, repaired, dropped, retried};
  UI shows a green check or a red warning with the issues; CLI prints [grounding: ...];
  Langfuse gets a categorical "grounding" score per trace (verified arriving).
- Tests: 60 fast pass (17 new grounding + 2 UI); e2e 15/15 now also require status grounded/not_checked.

## Postgres source - done 2026-11-05
- docs-db service in docker-compose.yml (postgres:17, port 127.0.0.1:5433, user/pass/db vrag/vrag/docs,
  shared_buffers 32MB, ~25 MB RAM). Schema in db/init/001_schema.sql (documents + document_versions:
  version_label, effective_from, filename, content bytea). Data is in a named volume = inside Docker's disk
  image, which is on E: (E:\Docker Disk image\DockerDesktopWSL). Nothing stored on C:.
- psycopg installed with --no-cache-dir and TMP on E:rag-tmp (empty folder left; removal was blocked).
- sources/db.py: DbSource(url, query). Any schema works via a query returning doc_id, version, filename,
  content, source_date, ref. Versions numbered per doc by (source_date, ref), so back-dated rows land right.
- sync now renumbers a version whose position changed without re-embedding (report.renumbered).
- Use 127.0.0.1, not localhost, in DOCS_DB_URL: localhost tried IPv6 first and hung forever (fixed, plus
  connect_timeout=10).
- Seed: `.venv/Scripts/python scripts/seed_db.py` (travel-expense-policy v1.0/v1.1/v2.0, DB only), then
  `.venv/Scripts/vrag sync db`. Golden changes added; eval_diff 8/8, router eval 22/22.
- Tests: 66 fast pass; e2e 22/22 (5 new travel questions + 2 live DB tests).

## Git --url option - done 2026-11-06
- src/vrag/sources/remote.py: parse_link (GitHub repo/tree/blob/raw links, any git URL or path),
  ensure_clone (clone to data/repos/<name>, or fetch --tags + pull --ff-only if cloned; refuses a folder
  cloned from a different origin), suggest_files (doc files, shallowest first).
- CLI: `vrag sync git --url https://github.com/semver/semver/blob/master/semver.md` (file link sets --file).
  Repo link without --file prints candidate files and exits 1.
- tests/test_remote.py (14, offline: local upstream repo). Fast suite 80 pass.

## Add-a-repo sidebar in the UI - done 2026-11-06
- API: POST /sources/git/inspect {url} (clone/pull, returns candidates, file from link, tag count);
  POST /sources/git/sync {url, files, by} -> 202 job, runs sync in a background thread, one at a time (409);
  GET /jobs/{id} (status, current version, done_count, indexed/skipped/renumbered/empty, error).
- sync(source, on_progress=callback) reports each version label.
- UI sidebar: link -> Check link -> pick files + tags/commits -> Load versions; st.fragment(run_every=2) polls the job.
- Tests: tests/test_api_git.py (7), 4 sidebar tests in tests/test_ui.py. Fast suite 91 pass.
- Note: two tags on the same commit count as one version (first tag name wins).

## Document picker, router doc check, stale-version cleanup - done 2026-11-06
- Router: a doc is accepted only if the question names it (named_in: distinctive id/title words),
  or it is the only doc with the mentioned versions, or LLM and search agree. Else unclear + list of docs.
  (Was: 'what has changed in forbidding agents' guessed semver/leave-policy.) Router eval 25/25.
- Focus: /ask {question, doc_id}; resolve(focus=...) forces that doc; unclear -> latest of that doc;
  answer_latest filters by route.doc_id. UI: 'Document' selectbox (GET /documents), switches to a repo after loading.
- sync removes indexed versions the source no longer produces (report.removed), only after a complete pass.
  Fixes react/CLAUDE mixing commit-SHA versions and tag versions after loading it both ways.
- POST /sources/git/preview -> versions + size per file; UI warns over 30 versions or 100 KB and needs a checkbox.
- Fast suite 111 pass; e2e 22/22 (run before the focus change).
- LIVE: react/CHANGELOG (commits mode, 226 versions, 290 KB) was loading in the API at 59/226 on 2026-11-06.
  API restart needed for prune + preview; restarting kills that load. react/CLAUDE still mixed until re-synced by tags.

## UI redesign - done 2026-11-06
- Sidebar = document library (st.radio styled as a nav list; selection = what questions are about) +
  'Add from GitHub' expander: one link field + Add. File link adds directly; repo link asks 'Which file?';
  tags if the file has >=2 tagged versions else commits (automatic); >30 versions or >100 KB asks 'Add anyway'.
- Main = chat (st.chat_input, history in session). Answer: context line, version timeline (used versions filled,
  compare old=amber/new=blue, long histories shortened), answer, one-line check, 'Sources (n)' expander with serif quotes.
- Theme in .streamlit/config.toml (light, IBM Plex Sans; Plex Serif for quotes). Restart Streamlit after theme edits.
- Screenshots: .shots/shoot.py (gitignored; Playwright in venv using installed Chrome).
  Args: '@Doc title' picks a doc, '!<link>' tries Add, anything else is asked.
- tests/test_ui.py rewritten (14). Fast suite 111 pass.

Left:
1. User check of the router in Streamlit (API + UI must be started; run them in your own terminals so
   Claude Code's low-memory cleanup doesn't stop them).
2. More sources: MinIO.

## Run 2026-11-20 (branch run-2026-11-20) - W1-W3 finished and verified
- Eval index `vrag-eval` (scripts/build_eval_index.py): folder corpus, semver tags, docs-db, S3. e2e tests and
  scripts/eval_*.py use it by default; the library `vrag-chunks` is never written by evals.
- S3 source (sources/s3.py): object versions = document versions (or `--layout folders`). docs-minio on
  127.0.0.1:9100. `scripts/seed_minio.py` then `vrag sync s3`. Seeded doc it-security-policy v1.0/v1.1/v2.0.
- Reranker (rerank.py, fastembed cross-encoder, model in data/models): RERANK=false by default (+0.8 GB RAM,
  ~1.5 s/search). scripts/eval_retrieval.py, 89 cases: hybrid MRR 0.803, +rerank 0.927, kNN-only 0.898.
- Answer eval (evals.py, scripts/eval_answers.py, data/golden/answer_cases.json, 37 cases): 33/37.
- Checks: fast suite 168 pass; eval_diff all pairs OK (incl. it-security-policy); eval_router 27/28
  (the fail is a react/CLAUDE case: that doc is not in vrag-eval -> fix the case); S3 live e2e 2 pass 1 skip.

Next (queue, see agents/runs/2026-11-20/BOARD.md):
1. Q4 fixes from the answer eval: router.py (several docs have the version -> ask; general question with no doc
   -> latest), answer.py mentions_change (missed "Also changed"). Re-run with --failed-from then --repeat 3.
2. Q8 fusion: kNN-only beats hybrid on retrieval (R@6 0.978 vs 0.899): weighted RRF.
3. Fix/remove the react/CLAUDE router case. Q1 review, Q2 num_ctx + compact catalog, Q3 S3 in API+UI,
   Q5 history questions, Q6 upload version, Q7 README, L2 react fix in vrag-chunks, user router check in UI.
