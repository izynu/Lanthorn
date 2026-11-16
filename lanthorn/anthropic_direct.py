"""Anthropic's own API — the Messages protocol, reached directly.

This is the one provider the harness's default transport was written for: the
Anthropic SDK speaks ``POST /v1/messages`` natively, so there is no protocol
translation here, no proxy and no ``openai_compat`` path. ``DEFAULT_BASE_URL``
in :mod:`lanthorn.config` is already this host; this module exists so the
provider registry has a module to name, and so ``/connect anthropic`` stores a
key the same way every other provider's does.

What is and is not in the catalog
---------------------------------
``GET /v1/models`` is **key-gated** (401 without a key), which makes it a real
key check rather than a formality — the same property Gemini's catalog has and
for the same reason. It lists chat models only; there are no embeddings or TTS
entries to filter out.

Model ids are taken from the listing, never written down here. A pinned id in
a module is a id that retires on someone else's schedule, and the catalog is
the authority on what answers today.
"""

from __future__ import annotations

import os

from . import direct_common as common

API_BASE_URL = "https://api.anthropic.com"

#: The listing — the SDK's own Models endpoint. Anthropic accepts a bearer
#: token here and answers the ``{"data": [{"id": …}]}`` envelope, which is
#: what ``direct_common.fetch_catalog`` sends and reads, so the shared reader
#: works unchanged. ``limit`` is raised because the API pages at 20 by default
#: and the catalog is larger than that.
MODELS_URL = f"{API_BASE_URL}/v1/models?limit=1000"

PROFILE = common.LANTHORN_HOME / "anthropic" / "source"

KEY_PREFIX = "sk-ant-"

DEFAULT_MODEL = "claude-sonnet-5-5"

_CATALOG = common.Catalog(MODELS_URL, "api.anthropic.com")


def load_credentials() -> tuple[str, str, str] | None:
    """The Anthropic key, however the operator supplied it.

    Order: the environment (the key they exported on purpose), then the
    profile ``/connect anthropic`` writes. Returns ``(token, model, origin)``;
    the token is never logged.
    """
    token = (os.environ.get("ANTHROPIC_API_KEY") or "").strip()
    if token:
        return token, os.environ.get("ANTHROPIC_MODEL", ""), "environment"
    exports = common.read_exports(PROFILE)
    token = exports.get("ANTHROPIC_AUTH_TOKEN") or exports.get("ANTHROPIC_API_KEY")
    if token:
        return token, exports.get("ANTHROPIC_MODEL", ""), str(PROFILE)
    return None


def save_key(token: str, model: str = "") -> "os.PathLike":
    """Write the key to this provider's profile, mode 600."""
    return common.write_profile(
        PROFILE, API_BASE_URL, (token or "").strip(), model,
        "Anthropic API profile (written by /connect)")


def looks_like_key(value: str) -> bool:
    """A hint for the confirmation line, never a gate."""
    return (value or "").strip().startswith(KEY_PREFIX)


def fetch_models(token: str = "", *, timeout: float = common.TIMEOUT,
                 use_cache: bool = True):
    return _CATALOG.fetch(token, use_cache=use_cache)


def chat_models(token: str = "", **kw):
    models, err = fetch_models(token, **kw)
    if models is None:
        return None, err
    return [m for m in models if m.chat], ""
