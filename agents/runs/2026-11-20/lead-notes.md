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

## Wave 2 (started after commit 681e887, branch run-2026-11-20)
W4 router (w2M, vrag-w4-router), W5 answer+fusion (w2N, vrag-w5-fusion), W6 repo activity (w2P, vrag-w6-activity).
User goal: test the app with active GitHub repos, ask "what was done recently and in which files" -> W6.
When W6 is done: merge ACTIVITY_INDEX into config.py, restart API + UI in herdr panes for the user's test
(API :8000, Streamlit :8501), load 1-2 repos for them. Commit each task on run-2026-11-20.
User decision: restart W5 (pane w2N, partial files in tree) only AFTER W6 reports. Restart: in w2N run
`claude --model sonnet --permission-mode acceptEdits "You are worker W5. Read agents/runs/2026-11-20/brief-W5.md. Your previous run crashed (low RAM) mid-task: continue your partial files, don't start over."`
Lead watcher was killed by Claude Code for low memory; don't restart it unless the user asks.
App for the user's test (2026-11-21): herdr workspace vrag-app (w2Q). API in w2Q:p1 (uvicorn 127.0.0.1:8000,
tracing off), UI in w2Q:p2 (streamlit :8501, API_URL=http://127.0.0.1:8000). Both running from branch run-2026-11-20.
After W5 lands, restart the API (Ctrl+C in w2Q:p1, rerun) to pick up its changes.
