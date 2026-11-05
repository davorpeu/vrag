from abc import ABC, abstractmethod
from collections.abc import Iterator

from vrag.models import DocVersion


class VersionSource(ABC):
    """Adapter over wherever document versions live (folder, git, DB, object storage).

    Each adapter owns its doc identity rule and must emit a stable doc_id for every
    version of the same document, plus a monotonically increasing version_num.
    """

    name: str

    @abstractmethod
    def iter_versions(self) -> Iterator[DocVersion]:
        """Yield every version the source knows about. Sync dedupes by content hash."""
