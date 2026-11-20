# Lead notes (for the lead after a context reset)

Workers (in-process subagents, model opus, effort max, background). Resume/message with SendMessage to the id.
- W1 S3 source: a51e5c4183cdee7fb (launched ~13:44)
- W2 retrieval eval + reranker: a928b2ef3e88cdcaf (launched ~13:44)
- W3 answer eval set: a822f95f504bb9c17 (launched ~13:44)

Loop: dynamic /loop; prompt starts "vrag lead loop: you are the lead." Fallback wakeup 1800 s; subagent
completion notifications are the main wake signal.

Open questions to the user (asked ~14:00): weekly reset time (= stop time), commits allowed?

## State at 13:55 (written for a context reset)
Done (lead): Docker up (elasticsearch, docs-db); Langfuse containers stopped (RAM 0.56 GB free); vrag-eval built
(16 versions, 5 docs); pre-edits for W1 (config.py docs_s3_*, pyproject minio, .env/.env.example, minio installed);
eval scripts + e2e conftest default to vrag-eval, conftest switches tracing off when Langfuse is down;
scripts/build_eval_index.py; .gitignore (data/evals/, data/models/, llm.lock); run RULES.md + BOARD.md.
Running: W1, W2, W3 (see ids above); baseline job = HEAD exported to /tmp/vrag-head, fast + e2e against
vrag-eval, holds agents/runs/2026-11-19/llm.lock and deletes it at the end.
Next (in order):
1. When the baseline job finishes: record fast/e2e numbers in BOARD.md (Done) and STATE.md.
2. L2 react fix in vrag-chunks (Ollama idle, no lock held): finish react/CHANGELOG with
   `LANGFUSE_TRACING_ENABLED=false .venv/Scripts/vrag sync git --repo data/repos/react --file CHANGELOG.md --by commits`
   (skips the 217 indexed, adds the rest, sets is_latest); re-sync react/CLAUDE by tags
   (`--file CLAUDE.md --by tags`; check the file path in the repo first).
3. On each worker report: read reports/<id>.md, review the diff of its files, run fast suite + the checks it
   claims, apply "Requests to lead", update BOARD/STATE, then launch the next queue item (Q1 review first).
4. W1 done -> add S3Source to scripts/build_eval_index.py, seed + sync s3 into vrag-eval and vrag-chunks
   (W1 report has the commands), run its live e2e test.
5. W2 done -> run full e2e with its default; W3 done -> turn its failure list into fix tasks (Q4).
User decision 13:59 (weekly reset ~14:00): let W1-W3 finish, integrate + verify them, then STOP the loop
(ScheduleWakeup stop: true). Start no new tasks; steps 2 (react fix) and the queue are for a later run.
User said yes (~14:02) to committing: at the end the LEAD creates a work branch (e.g. run-2026-11-19; we are on
master) and commits the verified results, one commit per task (W1 S3, W2 rerank, W3 evals, lead infra).
Workers never commit. CLAUDE.md and agents/RULES.md were untracked before this run: don't commit them
without asking; agents/runs/2026-11-19/ (the run record) can go in the lead infra commit.
