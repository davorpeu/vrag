You are worker W6 (repo activity: "what was done recently, in which files") in project D:/projects/versioned-rag. Read in order: agents/RULES.md, STATE.md, agents/runs/2026-11-20/RULES.md, agents/runs/2026-11-20/BOARD.md (wave 2 table). We are on branch run-2026-11-20; never commit or switch branches. Edit only the files BOARD.md gives your task id; anything else goes under "Requests to lead". When done, save the final report to agents/runs/2026-11-20/reports/<task id>.md in the run RULES format, print it, and stop.

Goal (from the user): load GitHub repos with lots of changes and ask "describe what's been done recently and in what files".
Today the app only versions single document files; it knows nothing about commits across a repo. Build a separate repo-activity feature:
- src/vrag/activity.py (new): from a repo link (reuse sources/remote.py parse_link/ensure_clone; read-only use, don't edit it),
  read git history for a time window or last N commits (default: last 90 days, cap ~500 commits; keep clones small, e.g.
  --shallow-since, explain your choice): per commit sha, date, author, message, files with +/- line counts.
  Index into its own ES index (setting ACTIVITY_INDEX, default vrag-activity; embeddings of message + file paths). Idempotent by sha.
- Answer: question + repo + time window (parse "last week", "since March", "recently" = 14 days default) -> fetch commits in the window
  (plus semantic search when the question names a topic), aggregate files/directories by change count IN CODE, and have the LLM
  summarise the themes from commit messages only. File lists and counts come from data, never from the LLM. Cite commit shas
  (link to GitHub when the repo is on GitHub). Answer schema like the existing ones (Pydantic).
- CLI: `vrag activity sync --url <repo>` and `vrag activity ask --url <repo> "question"`.
- API: POST /activity/sync (background job like /sources/git/sync), POST /activity/ask, GET /activity/repos.
- UI: in ui/streamlit_app.py add a "Repo activity" area: paste repo link -> load (progress) -> chat about it; show the answer,
  a "files changed" table (path, commits, +/-), and commit list with links. Keep the existing document UI working.
- Tests (offline, local temp git repo): tests/test_activity.py, tests/test_api_activity.py, plus UI tests in tests/test_ui.py.
- Live check: load 2 real active GitHub repos of moderate size (pick ones with many commits in the last weeks, not giants
  like linux/react) into a scratch index vrag-dev-w6, ask 3 questions each, put questions + answers in the report.
  Embedding/sync work needs no LLM lock; asking more than ~10 questions does.
RAM is tight: no new heavy packages. Don't touch router.py, answer.py, store.py (W4/W5 own them); write your own ES calls in activity.py.
Settings: config.py belongs to W5, so read ACTIVITY_INDEX with os.getenv in activity.py and list the config.py line you want under Requests to lead.
