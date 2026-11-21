You are worker W4 (router fixes from the answer eval) in project D:/projects/versioned-rag. Read in order: agents/RULES.md, STATE.md, agents/runs/2026-11-20/RULES.md, agents/runs/2026-11-20/BOARD.md (wave 2 table). We are on branch run-2026-11-20; never commit or switch branches. Edit only the files BOARD.md gives your task id; anything else goes under "Requests to lead". When done, save the final report to agents/runs/2026-11-20/reports/<task id>.md in the run RULES format, print it, and stop.

Task W4: fix the 3 router failures in agents/runs/2026-11-20/reports/W3.md section 4 (cases unclear-no-doc,
latest-semver-zeroes, latest-offtopic-password) in src/vrag/router.py.
- Several docs have the mentioned version and none is named in the question -> unclear + which-doc list (no search guess).
- A general question with no doc and no version -> latest (search picks the doc); off-topic stays refused by the answer step.
- Fix the react/CLAUDE case in data/golden/router_cases.json (that doc is not in vrag-eval): replace it with an equivalent case on an eval doc.
Acceptance: unit tests in tests/test_router.py for each fix; fast suite passes; under the LLM lock:
scripts/eval_router.py (expect 28/28) and `scripts/eval_answers.py --failed-from data/evals/answers-full-1.json`,
then a full eval_answers run. Report numbers. Don't regress other cases. Answer-step failure compare-retention-labels belongs to W5.
