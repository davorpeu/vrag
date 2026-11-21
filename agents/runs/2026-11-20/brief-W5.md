You are worker W5 (compare answer fix + search fusion) in project D:/projects/versioned-rag. Read in order: agents/RULES.md, STATE.md, agents/runs/2026-11-20/RULES.md, agents/runs/2026-11-20/BOARD.md (wave 2 table). We are on branch run-2026-11-20; never commit or switch branches. Edit only the files BOARD.md gives your task id; anything else goes under "Requests to lead". When done, save the final report to agents/runs/2026-11-20/reports/<task id>.md in the run RULES format, print it, and stop.

Task W5, two parts:
1. answer.py mentions_change (failure compare-retention-labels in agents/runs/2026-11-20/reports/W3.md section 4):
   a change whose words overlap an earlier change counts as covered, so "Also changed:" misses it. Match on clause
   identity too (section title, old/new values). Unit test in a new tests/test_answer_compare.py.
2. Fusion: W2 found kNN-only beats hybrid (reports/W2.md: R@6 0.978 vs 0.899, MRR 0.898 vs 0.803). Add weighted RRF
   in store.py (weights in config.py + .env.example), sweep weights with scripts/eval_retrieval.py on vrag-eval,
   pick the default from the numbers (also check rerank on top of the new fusion). Avoid overfitting: report per-category numbers.
Acceptance: fast suite passes; retrieval numbers table before/after; under the LLM lock a full scripts/eval_answers.py run
with the new default (baseline 33/37; W4 is fixing router cases at the same time, so judge only answer/retrieval-related cases).

Also (request from W6): in config.py add `activity_index: str = "vrag-activity"` to Settings next to es_index, add
`ACTIVITY_INDEX=vrag-activity` to .env.example, and in src/vrag/activity.py index_name() replace the
os.getenv("ACTIVITY_INDEX", ...) with get_settings().activity_index (you may edit that one line of activity.py).
Your previous run crashed (PC out of RAM) mid-task: your partial edits are in the tree (git diff). Continue them.
