# Lead notes, run 2026-11-20 (for the lead after a context reset)

Lead = herdr pane w2K:p1. Workers = claude (Sonnet 5.5, acceptEdits) in herdr workspaces, launched ~now:
- W1 S3 source: w2M:p1 (agent vrag-w1-s3), brief-W1.md
- W2 rerank: w2N:p1 (agent vrag-w2-rerank), brief-W2.md
- W3 answer evals: w2P:p1 (agent vrag-w3-evals), brief-W3.md
Watch: reports/<id>.md appearing; `herdr pane read <pane>` for permission prompts / stalls.
Docker: elasticsearch + docs-db started by lead. Ollama up.

Next:
1. On each report: review diff of its files, run fast suite + its claimed checks, apply Requests to lead, update BOARD/STATE.
2. W1 done: add S3Source to scripts/build_eval_index.py, seed + sync s3 into vrag-eval, run its live e2e.
3. W2 done: full e2e with its default. W3 done: failures -> Q4 tasks.
4. Commit on branch run-2026-11-19 (user approved 11-19), one commit per task (W1, W2, W3, lead infra).
   No Claude attribution. Don't commit CLAUDE.md / agents/RULES.md without asking.

Permission prompts: workers were switched to auto mode (seen 22:14); lead still checks any "blocked" pane.
(`herdr pane read <pane>` then `herdr pane send-keys <pane> Enter` = option 1 "Yes" only).
Refuse (Esc) and ask the user about: installs that change versions, deletes outside scratch, docker down/-v,
writes to vrag-chunks, git writes. Watcher: `sh agents/runs/2026-11-20/watch.sh` (background), exits on a
new report or a worker not "working".
