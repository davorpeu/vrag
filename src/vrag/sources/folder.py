import re
from collections.abc import Iterator
from datetime import date, datetime
from pathlib import Path

from vrag.models import DocVersion
from vrag.sources.base import VersionSource

# <root>/<doc_id>/v<N>[_YYYY-MM-DD].(pdf|docx)
VERSION_FILE = re.compile(r"^v(?P<num>\d+)(?:_(?P<date>\d{4}-\d{2}-\d{2}))?\.(?:pdf|docx)$", re.I)


class FolderSource(VersionSource):
    """Doc identity = directory name; version = filename."""

    name = "folder"

    def __init__(self, root: str | Path):
        self.root = Path(root)

    def iter_versions(self) -> Iterator[DocVersion]:
        if not self.root.is_dir():
            raise FileNotFoundError(f"Corpus folder not found: {self.root}")
        for doc_dir in sorted(p for p in self.root.iterdir() if p.is_dir()):
            for f in sorted(doc_dir.iterdir()):
                m = VERSION_FILE.match(f.name)
                if not m:
                    continue
                if m["date"]:
                    src_date = date.fromisoformat(m["date"])
                else:
                    src_date = datetime.fromtimestamp(f.stat().st_mtime).date()
                yield DocVersion(
                    doc_id=doc_dir.name,
                    version=f"v{int(m['num'])}",
                    version_num=int(m["num"]),
                    source=self.name,
                    source_ref=str(f.relative_to(self.root)).replace("\\", "/"),
                    filename=f.name,
                    content=f.read_bytes(),
                    source_date=src_date,
                )
