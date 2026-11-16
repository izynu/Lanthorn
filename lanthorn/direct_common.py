"""The shared shape of a provider reached directly, with the operator's own key.

OpenRouter, Gemini, NVIDIA and the rest each grew their own copy of the same
four functions — ``load_credentials``, ``save_key``, ``fetch_models`` and the
profile reader — because each was written against a different catalog with
different quirks. Anthropic, OpenAI and DeepSeek have none of those quirks: each
is one host, one key format, one catalog endpoint, and the harness already
speaks their protocols (``openai_compat`` for Chat Completions, the SDK for
Messages). Writing three near-identical modules by hand would be the same
duplication three more times, so this is the one implementation they share.

Nothing here decides anything about a provider that the provider's own module
does not declare: the constants below are the *shape*, and each provider module
supplies its own values.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

LANTHORN_HOME = Path(os.environ.get("LANTHORN_HOME") or (Path.home() / ".lanthorn"))

#: One HTTP request per call. Short on purpose: a call that hangs is worth
#: abandoning, and the operator can run it again.
TIMEOUT = 25.0

#: A fetched catalog older than this is refetched rather than trusted.
CACHE_TTL = 300.0

#: Families that cannot answer a text turn. Matched as substrings on the id.
#: Deliberately narrow and erring toward keeping an unknown model: hiding one
#: the operator could have used is worse than showing one that 400s.
NON_CHAT_MARKERS: tuple[str, ...] = (
    "embedding", "embed-", "tts", "whisper", "transcribe", "dall-e",
    "moderation", "rerank", "davinci", "babbage", "image", "audio",
    "realtime", "search-", "computer-use",
)


@dataclass(frozen=True)
class Model:
    """One entry from an OpenAI-shaped ``/v1/models`` listing."""

    id: str
    owned_by: str = ""
    created: int | None = None

    @property
    def chat(self) -> bool:
        lowered = self.id.lower()
        return not any(marker in lowered for marker in NON_CHAT_MARKERS)

    @property
    def free(self) -> bool:
        # None of these three publishes a per-model price in its catalog, and
        # inventing one would bill a model at the harness default. The tier is
        # an account property; ``cost`` reads "unknown" rather than "free".
        return False


def read_exports(path: Path) -> dict[str, str]:
    """Parse an ``export VAR="value"`` profile.

    The same reader every provider module uses, kept here so a new provider
    does not grow a fourth spelling of it. A line that does not parse is
    skipped rather than raising: a profile with a comment in it is normal.
    """
    out: dict[str, str] = {}
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return out
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("export "):
            continue
        try:
            name, value = line[len("export "):].split("=", 1)
        except ValueError:
            continue
        out[name.strip()] = value.strip().strip('"').strip("'")
    return out


def write_profile(path: Path, base_url: str, token: str, model: str,
                  note: str) -> Path:
    """Write a provider profile, mode 600 from the moment it exists.

    Created with 600 and then rewritten in place, so there is never a window
    in which the key sits in a world-readable file. The parent is 700, since
    its name alone says which provider is configured.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        pass
    body = (
        f"# Lanthorn — {note}\n"
        "# Treat this file as a credential. Never commit it.\n"
        f'export ANTHROPIC_BASE_URL="{base_url}"\n'
        f'export ANTHROPIC_AUTH_TOKEN="{token}"\n'
        f'export ANTHROPIC_MODEL="{model}"\n'
        'export ANTHROPIC_API_KEY=""\n'
    )
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(body)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return path


def fetch_catalog(url: str, token: str = "", *, host: str,
                  timeout: float = TIMEOUT) -> tuple[list[Model] | None, str]:
    """GET an OpenAI-shaped model list. Returns ``(models, error)``.

    A network failure is returned, never raised: the caller is a TUI command
    that must say "could not reach X" rather than take the session down.
    """
    headers = {"accept": "application/json", "User-Agent": "lanthorn"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            return None, f"key rejected (HTTP {e.code}) — check it with /connect"
        return None, f"HTTP {e.code} from {host}"
    except (urllib.error.URLError, OSError, ValueError) as e:
        return None, f"could not reach {host}: {e}"

    entries = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(entries, list):
        return None, f"{host} sent a listing this tool does not recognise"
    out: list[Model] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        mid = str(entry.get("id") or "").strip()
        if not mid:
            continue
        created = entry.get("created")
        out.append(Model(id=mid,
                         owned_by=str(entry.get("owned_by") or ""),
                         created=created if isinstance(created, int) else None))
    return out, ""


class Catalog:
    """A per-key cache around :func:`fetch_catalog`, one instance per module."""

    def __init__(self, url: str, host: str):
        self.url = url
        self.host = host
        self._cache: dict[str, tuple[list[Model], float]] = {}

    def fetch(self, token: str = "", *, use_cache: bool = True
              ) -> tuple[list[Model] | None, str]:
        token = (token or "").strip()
        if use_cache:
            hit = self._cache.get(token)
            if hit and (time.monotonic() - hit[1]) < CACHE_TTL:
                return hit[0], ""
        models, err = fetch_catalog(self.url, token, host=self.host)
        if models is not None:
            self._cache[token] = (models, time.monotonic())
        return models, err
