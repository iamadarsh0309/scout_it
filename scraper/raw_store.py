from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Protocol

from scraper.base import RawFetchResult


def content_hash(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


class RawStore(Protocol):
    def write(self, result: RawFetchResult) -> str:
        """Persist a raw fetch result, returning the path/key it was written to."""
        ...

    def read(self, path: str) -> bytes: ...


class LocalFileRawStore:
    """Filesystem-backed raw store: data/raw/{source}/{entity_type}/{source_entity_id}/{ts}.{ext}
    plus a .meta.json sidecar, per ProjectPlan.md's own raw-data example tree. A future
    S3RawStore can implement the same RawStore protocol as a near drop-in replacement.
    """

    def __init__(self, root: Path):
        self.root = root

    def _entity_dir(self, result: RawFetchResult) -> Path:
        return self.root / result.source / result.entity_type / result.source_entity_id

    def write(self, result: RawFetchResult) -> str:
        entity_dir = self._entity_dir(result)
        entity_dir.mkdir(parents=True, exist_ok=True)

        ts = result.fetched_at.strftime("%Y%m%dT%H%M%S%f")
        ext = "html" if result.content_type == "html" else "json"
        artifact_path = entity_dir / f"{ts}.{ext}"

        if result.content_type == "html":
            body = (result.html or "").encode("utf-8")
            artifact_path.write_bytes(body)
        else:
            body = json.dumps(result.json_payloads or []).encode("utf-8")
            artifact_path.write_bytes(body)

        meta = {
            "source": result.source,
            "entity_type": result.entity_type,
            "source_entity_id": result.source_entity_id,
            "url": result.url,
            "http_status": result.http_status,
            "fetched_at": result.fetched_at.isoformat(),
            "content_hash": content_hash(body),
        }
        meta_path = artifact_path.with_suffix(artifact_path.suffix + ".meta.json")
        meta_path.write_text(json.dumps(meta, indent=2))

        return str(artifact_path)

    def read(self, path: str) -> bytes:
        return Path(path).read_bytes()
