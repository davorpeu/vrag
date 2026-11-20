You are worker W2 (retrieval eval + reranker) in project D:/projects/versioned-rag. Read in order: agents/RULES.md, STATE.md, agents/runs/2026-11-20/RULES.md, agents/runs/2026-11-20/BOARD.md. Run 2026-11-19 was cut off by a usage limit; your task's partial files are already in the working tree (see git status / git diff). Continue them; do not start over. Edit only the files BOARD.md gives your task id. When done, save the final report to agents/runs/2026-11-20/reports/<task id>.md in the format from the run RULES.md, print it, and stop.

Task W2: measure retrieval and add an optional reranker.
Partial already: src/vrag/rerank.py, scripts/eval_retrieval.py, data/golden/retrieval_cases.json, store.py changes.
Acceptance:
- eval_retrieval.py reports recall@k and MRR on vrag-eval for: hybrid (current) vs hybrid + rerank. Exact numbers in the report.
- Reranker is local (no paid API), off or on by a setting in config.py/.env.example; install packages only per the run rules (dry-run first, no upgrades/downgrades). Model files go in data/models/ (gitignored).
- Wire it into answer.py retrieval only behind the setting. Default = whichever wins on the eval, but say what the RAM/latency cost is (the PC has ~0.5 GB free).
- tests/test_rerank.py and tests/test_store.py (offline) pass; full fast suite passes.
