"""Client for the local LLM Gateway (D:\\MyData\\Software\\openclaw-config, POST /ask, Haiku).

The user's choice over calling the Claude CLI directly. `fresh: true` asks for a new conversation per
call (gateway branch searchproduct-fresh-ask); an older gateway ignores it and resumes the project's
conversation, which is tolerable because every vetting prompt is self-contained.
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

import httpx

log = logging.getLogger("price_watch.gateway")

DEFAULT_URL = "http://127.0.0.1:18789"  # same PC; the Tailscale IP depends on which tailnet is active
TOKEN_FILE = Path.home() / ".openclaw" / "openclaw.json"
ASK_TIMEOUT_S = 150  # gateway kills Claude at 120 s
MAX_BUSY_RETRIES = 12


class GatewayError(RuntimeError):
    pass


def _token() -> str:
    try:
        return json.loads(TOKEN_FILE.read_text(encoding="utf-8"))["gateway"]["auth"]["token"]
    except (OSError, KeyError, json.JSONDecodeError) as exc:
        raise GatewayError(f"no gateway token in {TOKEN_FILE}: {exc}") from exc


def ask(project: str, message: str, base_url: str | None = None) -> str:
    """Send one stateless request; returns Claude's text. Retries while the project/gateway is busy."""
    url = (base_url or os.environ.get("PRICEWATCH_GATEWAY_URL") or DEFAULT_URL).rstrip("/") + "/ask"
    body = {"project": project, "message": message, "context": "none", "fresh": True}
    headers = {"Authorization": f"Bearer {_token()}"}
    for attempt in range(MAX_BUSY_RETRIES + 1):
        try:
            r = httpx.post(url, json=body, headers=headers, timeout=ASK_TIMEOUT_S)
        except httpx.HTTPError as exc:
            raise GatewayError(f"gateway unreachable at {url}: {exc}") from exc
        if r.status_code in (409, 429) and attempt < MAX_BUSY_RETRIES:
            wait = min(int(r.json().get("retry_after_ms", 5000)), 30000) / 1000
            log.info("gateway busy (%d), retry %d in %.0fs", r.status_code, attempt + 1, wait)
            time.sleep(wait)
            continue
        if r.status_code != 200:
            raise GatewayError(f"gateway HTTP {r.status_code}: {r.text[:300]}")
        data = r.json()
        if data.get("status") != "ok":
            raise GatewayError(f"gateway status {data.get('status')}: {str(data)[:300]}")
        log.debug("gateway ok project=%s %dms %d chars", project, data.get("duration_ms", -1),
                  len(data.get("response", "")))
        return data.get("response", "")
    raise GatewayError("gateway stayed busy")


def parse_json_reply(text: str) -> dict:
    """First top-level {...} in the reply (Haiku sometimes wraps JSON in prose or ``` fences)."""
    start = text.find("{")
    if start < 0:
        raise ValueError(f"no JSON object in reply: {text[:200]!r}")
    obj, _ = json.JSONDecoder().raw_decode(text[start:])
    if not isinstance(obj, dict):
        raise ValueError("reply JSON is not an object")
    return obj
