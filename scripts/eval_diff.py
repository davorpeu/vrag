"""Check the clause diff against data/golden/changes.json for every consecutive version pair.

Needs Elasticsearch with the folder corpus synced. No LLM involved.
Usage: .venv/Scripts/python scripts/eval_diff.py
"""

import json
import os
import re
import sys
from pathlib import Path

from dotenv import load_dotenv

os.environ.setdefault("ES_INDEX", "vrag-eval")  # eval index, not the user's library
load_dotenv(".env")

from vrag import store  # noqa: E402
from vrag.diff import clause_changes, diff_versions  # noqa: E402

truth = json.loads(Path("data/golden/changes.json").read_text(encoding="utf-8"))
failed = 0
for doc, info in truth.items():
    vs = info["versions"]
    for a, b in zip(vs, vs[1:]):
        old = store.version_chunks(doc, a["version"])
        new = store.version_chunks(doc, b["version"])
        got: set[str] = set()
        for c in clause_changes(diff_versions(old, new)):
            for t in (c.old_text, c.new_text):
                got |= set(re.findall(r"(?:^|\s)(\d+\.\d+)\s", t))
        want = set(b["changed"])
        ok = want == got
        failed += not ok
        print(f"{'OK  ' if ok else 'FAIL'} {doc} {a['version']} -> {b['version']}: want {sorted(want)} got {sorted(got)}")
sys.exit(1 if failed else 0)
