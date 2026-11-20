You are worker W3 (offline answer eval set + runner) in project D:/projects/versioned-rag. Read in order: agents/RULES.md, STATE.md, agents/runs/2026-11-20/RULES.md, agents/runs/2026-11-20/BOARD.md. Run 2026-11-19 was cut off by a usage limit; your task's partial files are already in the working tree (see git status / git diff). Continue them; do not start over. Edit only the files BOARD.md gives your task id. When done, save the final report to agents/runs/2026-11-20/reports/<task id>.md in the format from the run RULES.md, print it, and stop.

Task W3: an offline answer eval set and runner. Report-only for app code: do not change router/answer/grounding; list failures for the lead.
Partial already: src/vrag/evals.py, scripts/eval_answers.py.
Acceptance:
- data/golden/answer_cases.json: cases over the vrag-eval corpus (latest / version / compare / unclear / off-topic, all 5 docs) with expected route, versions, required facts, forbidden facts.
- eval_answers.py runs them through answer.ask (LANGFUSE_TRACING_ENABLED=false), scores route, versions, facts, citations, grounding, writes a JSON + short text summary to data/evals/ (gitignored). Holds the LLM lock while running.
- tests/test_evals.py (offline scoring logic) passes; tests/e2e/test_answers.py reuses evals.py where that removes duplication, still passes.
- Report: the full run numbers and each failing case with the likely cause and the file that should change (this becomes task Q4).
