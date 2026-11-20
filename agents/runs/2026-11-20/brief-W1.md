You are worker W1 (S3 / MinIO version source) in project D:/projects/versioned-rag. Read in order: agents/RULES.md, STATE.md, agents/runs/2026-11-20/RULES.md, agents/runs/2026-11-20/BOARD.md. Run 2026-11-19 was cut off by a usage limit; your task's partial files are already in the working tree (see git status / git diff). Continue them; do not start over. Edit only the files BOARD.md gives your task id. When done, save the final report to agents/runs/2026-11-20/reports/<task id>.md in the format from the run RULES.md, print it, and stop.

Task W1: an S3 version source (MinIO in docker compose, service docs-minio) like sources/db.py.
Partial already: src/vrag/sources/s3.py, scripts/seed_minio.py, cli.py and docker-compose.yml changes. Settings docs_s3_* are in config.py (lead).
Acceptance:
- S3Source emits stable doc_ids and versions (from object versions or key naming: pick one, explain why), effective date like the other sources.
- `vrag sync s3` works; second run skips everything (idempotent).
- seed_minio.py seeds a small versioned doc set (at least one doc, 3 versions) with ground truth added to data/golden/changes.json, plus router cases in data/golden/router_cases.json.
- tests/test_s3_source.py (offline, fake client) and tests/e2e/test_s3_source_live.py (marked e2e, skips if MinIO is down) pass. Use a scratch index vrag-dev-W1 for live checks, delete it after.
- Fast suite passes for your files. In the report, give the exact commands for the lead to seed + sync s3 into vrag-eval and the line to add to scripts/build_eval_index.py.
