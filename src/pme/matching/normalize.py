from __future__ import annotations

import re
import unicodedata

TEAM_ALIASES = {
    "la lakers": "los angeles lakers",
    "l.a. lakers": "los angeles lakers",
    "los angeles lakers": "los angeles lakers",
    "boston celtics": "boston celtics",
    "ny knicks": "new york knicks",
    "new york knicks": "new york knicks",
    "okc thunder": "oklahoma city thunder",
}


def normalize_text(text: str) -> str:
    value = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    value = value.replace("&", " and ")
    value = re.sub(r"[^a-z0-9.%$+-]+", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    for alias, canonical in TEAM_ALIASES.items():
        value = re.sub(rf"\b{re.escape(alias)}\b", canonical, value)
    return value


def canonical_slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", normalize_text(text)).strip("_")
