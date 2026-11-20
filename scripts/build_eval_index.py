"""Build the eval index: the fixed corpus that e2e tests and the evals run against.

It is separate from the user's library (vrag-chunks, what the UI and API use), so whatever someone
loads in the UI never changes eval results. Idempotent: unchanged versions are skipped.

Corpus:
  folder  data/corpus                         refund-policy, leave-policy, data-retention (v1-v3)
  git     data/repos/semver semver.md, tags   semver/semver
  db      docs-db (scripts/seed_db.py)        travel-expense-policy
  s3      docs-minio (scripts/seed_minio.py)  it-security-policy

Usage: .venv/Scripts/python scripts/build_eval_index.py [--recreate]
       ES_INDEX=<name> picks another index (default vrag-eval); the library index is refused.
"""

import os
import sys

from dotenv import load_dotenv

os.environ.setdefault("ES_INDEX", "vrag-eval")  # before .env, which names the library index
load_dotenv(".env")

from vrag import store  # noqa: E402
from vrag.config import get_settings  # noqa: E402
from vrag.sources.db import DbSource  # noqa: E402
from vrag.sources.folder import FolderSource  # noqa: E402
from vrag.sources.git import GitSource  # noqa: E402
from vrag.sources.s3 import S3Source  # noqa: E402
from vrag.sync import sync  # noqa: E402

LIBRARY_INDEX = "vrag-chunks"


def main() -> int:
    s = get_settings()
    if s.es_index == LIBRARY_INDEX:
        print(f"refusing to build the eval corpus into the library index {LIBRARY_INDEX}")
        return 2
    store.ensure_index(recreate="--recreate" in sys.argv)
    sources = [
        ("folder", FolderSource("data/corpus")),
        ("git semver", GitSource("data/repos/semver", ["semver.md"], by="tags")),
        ("db", DbSource(s.docs_db_url)),
        ("s3", S3Source(s.docs_s3_url, s.docs_s3_bucket, access_key=s.docs_s3_access_key, secret_key=s.docs_s3_secret_key)),
    ]
    for name, src in sources:
        r = sync(src)
        print(
            f"[{s.es_index}] {name}: indexed {len(r.indexed)}, skipped {len(r.skipped)}, "
            f"renumbered {len(r.renumbered)}, removed {len(r.removed)}, empty {len(r.empty)}"
        )
    for d in store.catalog():
        print(f"  {d['doc_id']}: {', '.join(v['version'] for v in d['versions'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
