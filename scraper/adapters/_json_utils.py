from __future__ import annotations

import json
import re

_NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.DOTALL
)


def extract_next_data(html: str) -> dict | None:
    """Pulls the Next.js SSR JSON payload embedded in a FotMob page's HTML. Returns None
    if the marker script tag isn't found (layout change — see BlockedError/FetchError
    handling in the adapter and the 'scraper doctor' canary idea in the project plan)."""
    match = _NEXT_DATA_RE.search(html)
    if not match:
        return None
    return json.loads(match.group(1))


def find_dicts_with_keys(obj, required_keys: set[str], _out: list[dict] | None = None) -> list[dict]:
    """Recursively walks a nested JSON structure (dict/list) collecting every dict that
    contains all of `required_keys`. Used instead of a hardcoded key-path because FotMob's
    __NEXT_DATA__ shape has changed before and a duck-typed search degrades more gracefully
    than a brittle exact path."""
    if _out is None:
        _out = []
    if isinstance(obj, dict):
        if required_keys.issubset(obj.keys()):
            _out.append(obj)
        for value in obj.values():
            find_dicts_with_keys(value, required_keys, _out)
    elif isinstance(obj, list):
        for item in obj:
            find_dicts_with_keys(item, required_keys, _out)
    return _out
