"""Run the router (LLM classify + resolve) over data/golden/router_cases.json and score it.

Usage: .venv/Scripts/python scripts/eval_router.py
"""

import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

os.environ.setdefault("ES_INDEX", "vrag-eval")  # eval index, not the user's library
load_dotenv()

from vrag import store  # noqa: E402
from vrag.router import classify, resolve  # noqa: E402

cases = json.loads(Path("data/golden/router_cases.json").read_text(encoding="utf-8"))
catalog = store.catalog()


def top_doc(question):
    def f(doc_ids):
        filters = [{"term": {"is_latest": True}}]
        if doc_ids:
            filters.append({"terms": {"doc_id": doc_ids}})
        hits = store.hybrid_search(question, filters=filters, top_k=1)
        return hits[0]["doc_id"] if hits else None

    return f


ok = 0
for c in cases:
    d = classify(c["q"], catalog)
    r = resolve(c["q"], d, catalog, top_doc(c["q"]))
    good = r.intent == c["intent"] and (
        c["intent"] in ("latest", "unclear") or (r.doc_id == c["doc"] and r.versions == c["versions"])
    )
    ok += good
    print(f"{'PASS' if good else 'FAIL'}  {c['q']}")
    if not good:
        print(f"      expected {c['intent']} {c.get('doc')} {c.get('versions')}")
        print(f"      got      {r.intent} {r.doc_id} {r.versions}  note={r.note!r}")
        print(f"      llm      {d.model_dump()}")
print(f"\n{ok}/{len(cases)} routed correctly")
sys.exit(0 if ok == len(cases) else 1)
