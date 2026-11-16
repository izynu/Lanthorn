"""Session persistence: save / load / list conversation state."""

from __future__ import annotations

import json
import re
import time
from dataclasses import asdict
from pathlib import Path

_SLUG = re.compile(r"[^a-zA-Z0-9._-]+")


def sessions_dir(cfg) -> Path:
    d = Path(cfg.state_dir) / "sessions"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _slug(text: str, limit: int = 40) -> str:
    text = _SLUG.sub("-", text.strip()).strip("-")
    return (text[:limit] or "session")


def save(cfg, agent, name: str | None = None) -> Path:
    """Persist the current conversation. Returns the file written."""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    label = _slug(name) if name else stamp
    filename = f"{stamp}-{label}.json" if name else f"{stamp}.json"
    path = sessions_dir(cfg) / filename

    payload = {
        "version": 1,
        "saved_at": time.time(),
        "model": cfg.model,
        "base_url": cfg.base_url,
        "workdir": str(cfg.workdir),
        "unsafe": cfg.unsafe,
        "allow_path": str(cfg.allow_path) if cfg.allow_path else None,
        "deny_path": str(cfg.deny_path) if cfg.deny_path else None,
        "scope_armed": agent.scope.armed,
        "scope_loaded": agent.scope.loaded,
        "tokens": {
            "input": agent.session_input_tokens,
            "output": agent.session_output_tokens,
        },
        "cost": agent.session_cost,
        "turn_count": agent.turn_count,
        "messages": agent.messages,
        # The rest of what makes the history what it is. A compacted session
        # keeps its recap in the system prompt and its history can be empty, so
        # saving messages alone loses the whole conversation: `--resume` came
        # back with no history and no record that one had been compacted. The
        # measured prompt size and the endpoint's stated limit come along
        # because the compaction threshold reads them, and a resumed session
        # that has forgotten the endpoint's real limit walks straight back into
        # the overflow it was told about. All optional on load — an older file
        # has none of them.
        "summary": getattr(agent, "_summary", ""),
        "last_input_tokens": getattr(agent, "_last_input_tokens", 0),
        "window_hint": getattr(agent, "_window_hint", 0),
        "dropped_params": sorted(getattr(agent, "_dropped_params", set())),
    }
    path.write_text(json.dumps(payload, indent=2, default=str))
    return path


def load(cfg, agent, path: str) -> tuple[bool, str]:
    """Restore a conversation into *agent*. Returns (ok, message)."""
    p = Path(path).expanduser()
    if not p.exists():
        # Allow loading by bare name from the sessions dir.
        candidate = sessions_dir(cfg) / path
        if candidate.exists():
            p = candidate
        else:
            matches = sorted(sessions_dir(cfg).glob(f"*{path}*"))
            if not matches:
                return False, f"no session matching '{path}'"
            p = matches[-1]

    try:
        data = json.loads(p.read_text())
    except Exception as e:  # noqa: BLE001
        return False, f"cannot parse session: {e}"

    agent.messages = data.get("messages", [])
    agent.turn_count = int(data.get("turn_count", 0))
    toks = data.get("tokens", {})
    agent.session_input_tokens = int(toks.get("input", 0))
    agent.session_output_tokens = int(toks.get("output", 0))
    agent.session_cost = float(data.get("cost", 0.0))
    if data.get("model"):
        cfg.model = data["model"]
    # Restored where present, left at their constructor defaults where not:
    # every one of these is a plain attribute a v1 file simply does not have.
    agent._summary = str(data.get("summary") or "")
    agent._last_input_tokens = int(data.get("last_input_tokens") or 0)
    agent._window_hint = int(data.get("window_hint") or 0)
    dropped = data.get("dropped_params") or []
    agent._dropped_params = {str(p) for p in dropped}
    if agent._summary:
        return True, (f"loaded {len(agent.messages)} messages and a compacted "
                      f"summary from {p.name}")
    return True, f"loaded {len(agent.messages)} messages from {p.name}"


def list_sessions(cfg) -> list[dict]:
    out = []
    for p in sorted(sessions_dir(cfg).glob("*.json"), reverse=True):
        try:
            data = json.loads(p.read_text())
            out.append(
                {
                    "file": p.name,
                    "saved_at": data.get("saved_at", 0),
                    "model": data.get("model", "?"),
                    "turns": data.get("turn_count", 0),
                    "messages": len(data.get("messages", [])),
                }
            )
        except Exception:
            continue
    return out
