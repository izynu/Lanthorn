"""DeepSeek's own API — Chat Completions, reached directly.

The harness already speaks Chat Completions (:mod:`lanthorn.openai_compat`), so
this provider is a base URL and a key: ``https://api.deepseek.com`` is in
``openai_compat.OPENAI_HOSTS``, and the protocol resolves to ``openai`` from the
host with no flag.

``GET /models`` lists ``deepseek-chat`` and ``deepseek-reasoner`` — the two
names DeepSeek's own documentation uses, which are stable aliases onto the
current model rather than dated snapshots, so they do not retire on a schedule.

The catalog is **key-gated** (401 without a key), so the listing is made with
the same credential the turns will use — which also makes it a real key check.

Model ids come from the listing; ``DEFAULT_MODEL`` is only the fallback for a
provider that has never been switched to. A switch that finds it missing
re-picks from the catalog rather than insisting on it.
"""

from __future__ import annotations

import os

from . import direct_common as common

API_BASE_URL = "https://api.deepseek.com"

MODELS_URL = f"{API_BASE_URL}/models"

PROFILE = common.LANTHORN_HOME / "deepseek" / "source"

KEY_PREFIX = "sk-"

DEFAULT_MODEL = "deepseek-chat"

_CATALOG = common.Catalog(MODELS_URL, "api.deepseek.com")


def load_credentials() -> tuple[str, str, str] | None:
    """The DeepSeek key: the environment first, then the stored profile."""
    token = (os.environ.get("DEEPSEEK_API_KEY") or "").strip()
    if token:
        return token, os.environ.get("DEEPSEEK_MODEL", ""), "environment"
    exports = common.read_exports(PROFILE)
    token = exports.get("ANTHROPIC_AUTH_TOKEN") or exports.get("ANTHROPIC_API_KEY")
    if token:
        return token, exports.get("ANTHROPIC_MODEL", ""), str(PROFILE)
    return None


def save_key(token: str, model: str = ""):
    """Write the key to this provider's profile, mode 600."""
    return common.write_profile(
        PROFILE, API_BASE_URL, (token or "").strip(), model,
        "DeepSeek API profile (written by /connect)")


def looks_like_key(value: str) -> bool:
    return (value or "").strip().startswith(KEY_PREFIX)


def fetch_models(token: str = "", *, timeout: float = common.TIMEOUT,
                 use_cache: bool = True):
    return _CATALOG.fetch(token, use_cache=use_cache)


def chat_models(token: str = "", **kw):
    models, err = fetch_models(token, **kw)
    if models is None:
        return None, err
    return [m for m in models if m.chat], ""
