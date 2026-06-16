from __future__ import annotations

import json
import logging
import time
from pathlib import Path

logger = logging.getLogger(__name__)


class CacheManager:
    def __init__(self, cache_dir: str, slug: str) -> None:
        self._base = Path(cache_dir) / slug
        self._base.mkdir(parents=True, exist_ok=True)

    def get(self, key: str, ttl_seconds: int) -> dict | None:
        path = self._base / f"{key}.json"
        if not path.exists():
            return None
        age = time.time() - path.stat().st_mtime
        if age > ttl_seconds:
            logger.debug("Cache stale for %s (age %.0fs > ttl %ds)", key, age, ttl_seconds)
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            logger.debug("Cache hit for %s (age %.0fs)", key, age)
            return data
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Cache read error for %s: %s", key, exc)
            return None

    def set(self, key: str, data: dict) -> None:
        path = self._base / f"{key}.json"
        tmp = path.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(path)
            logger.debug("Cache written for %s", key)
        except OSError as exc:
            logger.warning("Cache write error for %s: %s", key, exc)

    def invalidate(self, key: str | None = None) -> None:
        if key is None:
            for p in self._base.glob("*.json"):
                p.unlink(missing_ok=True)
            logger.debug("Cache invalidated for all keys")
        else:
            path = self._base / f"{key}.json"
            path.unlink(missing_ok=True)
            logger.debug("Cache invalidated for %s", key)
