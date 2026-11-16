"""One place that knows every AI provider, its key, and its models.

Lanthorn already had a module per hosted provider — ``openrouter``, ``gemini``,
``nvidia``, and so on — each with its own ``/x-api-setup`` command, its own
profile file and its own catalog fetcher. That worked, but it made the operator
learn the shape of the tool before they could connect a key: the command was
``/gemini-api-setup`` here, ``/nvidia-api-setup`` there, and there was no single
answer to "which providers do I have keys for?".

This module is that answer. It is a **registry**, not a new client: every entry
names the module that already owns that provider, and delegates to it. Nothing
here re-implements a fetch, a save or a model filter, so a provider keeps one
implementation and the registry cannot drift from it.

What a provider entry carries
-----------------------------
``key``          the short name the operator types — ``openrouter``, ``gemini``
``label``        what to show — ``OpenRouter``
``module``       the module that owns it (imported lazily, by name)
``env``          the environment variable its key conventionally lives in
``base_url``     the endpoint the session is pointed at when the provider is
                 switched to, when the module does not say (see ``base_url``
                 on the entry, resolved through the module)
``protocol``     ``anthropic`` (Messages natively) or ``openai`` (Chat
                 Completions), which decides how the harness talks to it
``free``         whether the provider has a free tier worth naming, and what
                 its limit is, in the operator's words
``key_url``      where the operator gets a key

Why environment variables are read first
----------------------------------------
A key in the environment is the one the operator exported on purpose, so it
wins over a stored profile — the same precedence every provider module already
uses. :func:`resolve` reports *which* source answered, and never the key
itself: the caller gets ``(key, origin)`` and is expected to show ``origin``
and at most the last four characters.
"""

from __future__ import annotations

import importlib
import os
from dataclasses import dataclass, field
from pathlib import Path

#: The harness home. Read at import, like every provider module does.
LANTHORN_HOME = Path(os.environ.get("LANTHORN_HOME") or (Path.home() / ".lanthorn"))


@dataclass(frozen=True)
class Provider:
    """One provider the harness can talk to, and how to reach it."""

    key: str
    label: str
    module: str
    env: str
    key_url: str = ""
    protocol: str = "openai"
    #: What the free tier is, in one clause, or "" when there is not one.
    free: str = ""
    #: Where the key prefix hint comes from, when the module has one. Not a
    #: gate — a hint for the confirmation line only.
    prefix_attr: str = "KEY_PREFIX"
    #: Modules that are a local proxy rather than a remote endpoint need
    #: nothing extra here; they simply have no key to set and ``keyless``
    #: says so.
    keyless: bool = False
    #: Whether the owning module records the selection in ``current-source``
    #: (``set_current``). The three direct providers have no module of their
    #: own beyond this tree's shared one, so a switch records for them here.
    remembers_selection: bool = True
    #: For a provider whose module owns several routes (``sources``), the name
    #: of the route constant that is this provider's endpoint.
    route_attr: str = ""

    def mod(self):
        """The owning module, imported on demand.

        Lazy because importing twelve provider modules to show one list is
        wasted work at launch, and because a module that fails to import
        should cost that provider's row and not the whole registry.
        """
        return importlib.import_module(f"lanthorn.{self.module}")

    @property
    def has_free_model_picker(self) -> bool:
        """Whether the module can name a free model — see :data:`FREE_MODEL_PICKERS`."""
        return self.key in FREE_MODEL_PICKERS

    @property
    def public_catalog(self) -> bool:
        """Whether the catalog lists without a key — see :data:`PUBLIC_CATALOGS`."""
        return self.key in PUBLIC_CATALOGS

    @property
    def key_prefix(self) -> str:
        try:
            return str(getattr(self.mod(), self.prefix_attr, "") or "")
        except Exception:
            return ""

    @property
    def profile(self) -> Path | None:
        try:
            return getattr(self.mod(), "PROFILE", None)
        except Exception:
            return None

    @property
    def base_url(self) -> str:
        """The endpoint a session is pointed at when this provider is chosen.

        ``DEFAULT_BASE_URL`` first, not ``API_BASE_URL``: for the two providers
        that run a local proxy (IteraCompute, InfronAI) the default is the
        proxy and ``API_BASE_URL`` is the upstream behind it — and the proxy is
        what the rest of the harness expects a session to talk to. For the
        providers with no proxy the two names are the same host.
        """
        mod = self.mod()
        # ``sources`` has no single base URL — it owns several routes — so a
        # provider that lives there names its route instead.
        route = getattr(mod, self.route_attr, None) if self.route_attr else None
        if route is not None and getattr(route, "base_url", ""):
            return str(route.base_url).rstrip("/")
        for attr in ("DEFAULT_BASE_URL", "API_BASE_URL", "BASE_URL"):
            value = getattr(mod, attr, None)
            if value:
                return str(value).rstrip("/")
        return ""

    def active(self, base_url: str | None) -> bool:
        """Whether *base_url* is this provider.

        Uses the module's own host test where it has one — ``is_openrouter``,
        ``is_nvidia`` and friends — because those know the spellings their
        provider is reached under. Falls back to the host of ``base_url``,
        which every provider entry resolves from its module.
        """
        url = (base_url or "").strip().lower()
        if not url:
            return False
        mod = self.mod()
        for name in ("active_endpoint", f"is_{self.key}"):
            test = getattr(mod, name, None)
            if callable(test):
                try:
                    return bool(test(base_url))
                except Exception:  # noqa: BLE001 - fall through to the host
                    break
        host = self.base_url.lower()
        for scheme in ("https://", "http://"):
            if host.startswith(scheme):
                host = host[len(scheme):]
        host = host.split("/")[0]
        if not host or host.startswith("localhost") or host.startswith("127."):
            # A proxy route: matching its hostname would match every proxy in
            # the tree. The module's own port check is the only correct test,
            # and it has already run above.
            return False
        return host in url


#: Whether a catalog can be listed without a key. It is a **fact about each
#: upstream**, measured rather than assumed, and it decides what the model
#: picker says before a key exists: a real list, or "add a key first". A wrong
#: True here shows the operator an empty catalog and blames their key.
#:   anthropic / openai / deepseek / gemini  401 without a key
#:   the hosted gateways below               public listings
PUBLIC_CATALOGS: frozenset[str] = frozenset({
    "openrouter", "nvidia", "unorouter", "aihubmix", "beatapi", "moonmath",
    "iteracompute", "infronai",
})

#: The providers, in the order the picker shows them: the ones most people
#: have a key for first, then the free-tier ones, then the gateways.
#:
#: Every entry names a module that exists in this tree. Adding a provider is
#: one row here plus whatever that provider's module already does — never a
#: second copy of the fetch/save logic.
PROVIDERS: tuple[Provider, ...] = (
    Provider(
        key="anthropic", label="Anthropic (Claude)", module="anthropic_direct",
        env="ANTHROPIC_API_KEY", protocol="anthropic",
        key_url="https://console.anthropic.com/settings/keys",
        free="pay as you go — no free tier",
        remembers_selection=False,
    ),
    Provider(
        key="openai", label="OpenAI", module="openai_direct",
        env="OPENAI_API_KEY", protocol="openai",
        key_url="https://platform.openai.com/api-keys",
        free="pay as you go — no free tier",
        remembers_selection=False,
    ),
    Provider(
        key="deepseek", label="DeepSeek", module="deepseek_direct",
        env="DEEPSEEK_API_KEY", protocol="openai",
        key_url="https://platform.deepseek.com/api_keys",
        free="new accounts get a trial balance",
        remembers_selection=False,
    ),
    Provider(
        key="gemini", label="Google Gemini", module="gemini",
        env="GEMINI_API_KEY", protocol="openai",
        key_url="https://aistudio.google.com/apikey",
        free="free tier — daily request quota per model",
    ),
    Provider(
        key="openrouter", label="OpenRouter", module="openrouter",
        env="OPENROUTER_API_KEY", protocol="anthropic",
        key_url="https://openrouter.ai/keys",
        free="hundreds of models, some :free at $0",
    ),
    Provider(
        key="nvidia", label="NVIDIA NIM", module="nvidia",
        env="NVIDIA_API_KEY", protocol="openai",
        key_url="https://build.nvidia.com",
        free="developer tier — trial credits per account",
    ),
    Provider(
        key="unorouter", label="UnoRouter", module="unorouter",
        env="UNOROUTER_API_KEY", protocol="anthropic",
        key_url="https://unorouter.com",
        free="free models listed in its catalog",
    ),
    Provider(
        key="aihubmix", label="AIHubMix", module="aihubmix",
        env="AIHUBMIX_API_KEY", protocol="anthropic",
        key_url="https://aihubmix.com",
        free="free trial models",
    ),
    Provider(
        key="beatapi", label="BeatAPI", module="beatapi",
        env="BEATAPI_API_KEY", protocol="anthropic",
        key_url="https://beatapi.io",
        free="free trial models",
    ),
    Provider(
        key="moonmath", label="Zro (MoonMath)", module="moonmath",
        env="MOONMATH_API_KEY", protocol="anthropic",
        key_url="https://zro.moonmath.ai",
        free="free daily usage on the Flash models",
    ),
    Provider(
        key="iteracompute", label="IteraCompute", module="iteracompute",
        env="ITERACOMPUTE_API_KEY", protocol="openai",
        key_url="https://api.iteracompute.com",
        free="free tier — 30M tokens per month",
    ),
    Provider(
        key="infronai", label="InfronAI", module="infronai",
        env="INFRONAI_API_KEY", protocol="openai",
        key_url="https://llm.onerouter.pro",
        free="free models — 1000 requests per day",
    ),
    Provider(
        key="tokenjuice", label="Token Juice", module="sources",
        env="TOKENJUICE_API_KEY", protocol="anthropic",
        key_url="https://tokenjuice.ai",
        free="free key at tokenjuice.ai",
        keyless=True, route_attr="TOKENJUICE_DIRECT",
    ),
)

#: Modules that can name a free model to land on when their profile carries
#: none. Declared here rather than as a field on each entry because it is a
#: fact about the module, not a choice about the provider — and the module is
#: what is called, so the two cannot disagree.
FREE_MODEL_PICKERS: frozenset[str] = frozenset({
    "unorouter", "moonmath",
})


#: Lookup by short name. Built once; the tuple above is the source of truth.
BY_KEY: dict[str, Provider] = {p.key: p for p in PROVIDERS}

#: The spellings the operator might type for a provider, mapped to its key.
#: Only unambiguous aliases belong here — a wrong guess sends a key to the
#: wrong host, which is the one mistake this list must not make.
ALIASES: dict[str, str] = {
    "claude": "anthropic",
    "google": "gemini",
    "nvidia-nim": "nvidia",
    "nim": "nvidia",
    "zro": "moonmath",
    "moonmath.ai": "moonmath",
    "zro-moonmath": "moonmath",
    "uno": "unorouter",
    "itera": "iteracompute",
    "infron": "infronai",
    "tj": "tokenjuice",
}


def find(name: str) -> Provider | None:
    """The provider called *name*, following the aliases; ``None`` if unknown."""
    word = (name or "").strip().lower()
    if not word:
        return None
    if word in BY_KEY:
        return BY_KEY[word]
    return BY_KEY.get(ALIASES.get(word, ""))


def known_names() -> list[str]:
    """Every spelling :func:`find` accepts, for an error message."""
    return sorted(set(BY_KEY) | set(ALIASES))


# ---------------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------------


def resolve(provider: Provider) -> tuple[str, str] | None:
    """The key for *provider*, and where it came from.

    Order, first hit wins:

      1. the provider's own environment variable (``OPENROUTER_API_KEY`` …),
         because a key the operator exported on purpose is the one they mean;
      2. the provider module's own resolution — which reads the stored profile,
         and for some providers a ``~/claude-source`` file as a last resort.

    Returns ``(key, origin)`` or ``None``. ``origin`` is ``"environment"`` or a
    file path — never the key, which the caller must not print.
    """
    env_value = (os.environ.get(provider.env) or "").strip()
    if env_value:
        return env_value, "environment"
    try:
        found = provider.mod().load_credentials()
    except Exception:
        return None
    if not found:
        return None
    key, _model, origin = found
    key = (key or "").strip()
    return (key, origin) if key else None


def status(provider: Provider) -> tuple[bool, str]:
    """``(has_key, origin)`` — what the picker shows for one provider."""
    found = resolve(provider)
    if found is None:
        return False, ""
    return True, found[1]


def mask(key: str) -> str:
    """The last four characters of a key, for a confirmation line.

    Four is enough to tell two keys apart and not enough to be one. Short
    values are masked entirely rather than half-shown.
    """
    key = key or ""
    if len(key) < 12:
        return "…" if key else ""
    return "…" + key[-4:]


def save(provider: Provider, key: str, model: str = "") -> Path | None:
    """Store *key* for *provider* through that provider's own module.

    Delegated so there is exactly one writer per provider and one file format
    (``export VAR="value"``, mode 600) across the project. Returns the path
    written, or ``None`` when the provider has nothing to store.
    """
    if provider.keyless:
        return None
    key = (key or "").strip()
    if not key:
        raise ValueError("empty key")
    mod = provider.mod()
    # Token Juice lives in ``sources`` and names its writer ``save_token``;
    # every provider with a module of its own calls it ``save_key``.
    writer = getattr(mod, "save_key", None) or getattr(mod, "save_token", None)
    if writer is None:
        raise AttributeError(f"{provider.label} has no way to store a key")
    return writer(key) if writer.__name__ == "save_token" else writer(key, model)


def clear(provider: Provider) -> bool:
    """Remove the stored profile. True when a file was actually removed.

    Only the profile is removed. A key in the environment is the operator's
    shell, not ours, and deleting it from under them would be a surprise.
    """
    profile = provider.profile
    if profile is None:
        return False
    try:
        profile.unlink()
    except FileNotFoundError:
        return False
    return True


def env_for(provider: Provider, key: str) -> dict[str, str]:
    """The environment a session on *provider* runs with.

    Returned rather than applied, so a switch can hand it to the config and
    the child processes at once. ``ANTHROPIC_*`` is the harness's own spelling
    for "the key and the endpoint", whichever protocol the provider speaks —
    ``openai_compat`` reads the same names.
    """
    base = provider.base_url
    return {
        "ANTHROPIC_BASE_URL": base,
        "ANTHROPIC_AUTH_TOKEN": key,
        "ANTHROPIC_API_KEY": "",
        "OPENAI_API_KEY": key,
        "OPENAI_BASE_URL": base,
        "LANTHORN_PROTOCOL": provider.protocol,
    }


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


def models(provider: Provider, *, key: str | None = None):
    """The provider's catalog, as ``(ids, free_ids, error)``.

    Delegated to the module's own ``fetch_models`` and read through the same
    duck-typed attributes every provider's ``Model`` carries (``id``,
    ``chat``, ``free``), so a module that adds a field does not need a change
    here. A provider with no fetcher reports that rather than raising.
    """
    mod = provider.mod()
    fetch = getattr(mod, "fetch_models", None)
    if fetch is None:
        return [], [], f"{provider.label} has no model catalog to list"
    if key is None:
        found = resolve(provider)
        key = found[0] if found else ""
    try:
        listing, err = fetch(key or "")
    except Exception as exc:  # a network failure must not take the TUI down
        return [], [], f"could not list {provider.label} models: {exc}"
    if listing is None:
        return [], [], err or f"could not list {provider.label} models"
    usable = [m for m in listing if getattr(m, "chat", True)]
    ids = [str(getattr(m, "id", "")) for m in usable if getattr(m, "id", "")]
    free = [str(getattr(m, "id", "")) for m in usable
            if getattr(m, "free", False) and getattr(m, "id", "")]
    return ids, free, ""


def summarise() -> list[tuple[Provider, bool, str]]:
    """Every provider with ``(provider, has_key, origin)`` — the connect list."""
    out = []
    for provider in PROVIDERS:
        has, origin = status(provider)
        out.append((provider, has, origin))
    return out
