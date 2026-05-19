from __future__ import annotations

import json
import re
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from govkb.models.site_info import SiteInfo


class RecipeMemory:
    """Persist verified site analysis recipes by domain."""

    VERSION = 1

    def __init__(self, root: str | Path = "recipes"):
        self.root = Path(root)

    def load(self, url: str) -> SiteInfo | None:
        path = self._path_for_url(url)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if int(data.get("version") or 0) != self.VERSION:
                return None
            site = data.get("site") or {}
            return SiteInfo(**site)
        except Exception:
            return None

    def save(self, site: SiteInfo) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        path = self._path_for_domain(site.domain)
        payload = {
            "version": self.VERSION,
            "saved_at": datetime.now().isoformat(timespec="seconds"),
            "site": asdict(site),
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def _path_for_url(self, url: str) -> Path:
        parsed = urlparse(url if re.match(r"https?://", url, re.I) else "https://" + url)
        return self._path_for_domain(parsed.netloc)

    def _path_for_domain(self, domain: str) -> Path:
        safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", domain.lower()).strip("._") or "unknown"
        return self.root / f"{safe}.json"

