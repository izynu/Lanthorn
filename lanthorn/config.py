"""Runtime configuration, model registry and pricing."""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------
# Model registry
# ---------------------------------------------------------------------------
# The harness talks the Anthropic Messages protocol to whatever endpoint
# ANTHROPIC_BASE_URL points at, or Chat Completions to an endpoint that serves
# it. By default that is Logfare directly (https://logfare.ai/v1), whose default
# model is deepseek-v4.1-flash. There is no local LiteLLM proxy in the default
# path: the upstream serves Chat Completions itself.
#
# Prices are USD per million tokens and are *estimates* for CLI accounting —
# they are not billing-accurate. Unknown models fall back to DEFAULT_PRICE.

DEFAULT_BASE_URL = "https://logfare.ai/v1"
DEFAULT_MODEL = "deepseek-v4.1-flash-1017"

# The harness dotdir. setup.sh installs the doctrine file, the source profiles
# and the session state here; LANTHORN_HOME moves the whole lot.
#
# The tool used to be called ZimZilla and kept everything in ~/.zimzilla, so a
# machine that ran it before the rename has its keys and sessions there. The
# old directory is adopted once — moved to the new name, not copied, so there
# is never a second copy of a credential on disk — and the operator is told,
# because a silent move of their API keys is exactly the kind of thing that
# should not be silent. Only when the new directory does not exist yet: after
# the first launch, ~/.lanthorn is the authority and ~/.zimzilla is ignored.
LEGACY_HOME = Path.home() / ".zimzilla"


def _adopt_legacy_home(new_home: Path, legacy: Path) -> str:
    """Move *legacy* to *new_home* once. Returns the path moved, or ``""``.

    A rename can fail across filesystems (``EXDEV``) or on Windows when a file
    inside is open; a copy-then-remove is the fallback, and a failure at either
    step leaves both directories alone rather than half-moving one. Nothing
    here is fatal: the worst case is a fresh start, which is what an operator
    who never ran the old name would get anyway.
    """
    if not legacy.is_dir() or new_home.exists():
        return ""
    try:
        legacy.rename(new_home)
        return str(legacy)
    except OSError:
        pass
    try:
        shutil.copytree(legacy, new_home, symlinks=True)
        shutil.rmtree(legacy, ignore_errors=True)
        return str(legacy)
    except (OSError, shutil.Error):
        return ""


_LEGACY_ADOPTED = ""


def _home_from_env() -> Path:
    """The state directory, honouring ``LANTHORN_HOME`` and the old spelling."""
    chosen = os.environ.get("LANTHORN_HOME") or os.environ.get("ZIMZILLA_HOME")
    if chosen:
        return Path(chosen)
    global _LEGACY_ADOPTED
    _LEGACY_ADOPTED = _adopt_legacy_home(Path.home() / ".lanthorn", LEGACY_HOME)
    return Path.home() / ".lanthorn"


def adopted_legacy_home() -> str:
    """The old state directory this launch moved into place, if any.

    Read by the UI once, at startup, so the operator learns where their keys
    went. Empty on every launch after the first.
    """
    return _LEGACY_ADOPTED


DEFAULT_HOME = _home_from_env()


def find_agents_file(
    workdir: Path,
    home: Path | None = None,
    user_home: Path | None = None,
) -> Path | None:
    """Locate the doctrine file that auto-run mode follows.

    Searched in order, first hit wins:

      1. ``<workdir>/AGENTS.md``      — the project's own doctrine
      2. ``<workdir>/../AGENTS.md``   — a checkout's root file, seen from a subdir
      3. ``<home>/AGENTS.md``         — the installed doctrine (~/.lanthorn)
      4. ``<user_home>/AGENTS.md``    — a hand-kept global file (~)

    Step 3 is what makes auto-run mode work from any directory: the file is installed
    once by setup.sh, so a session opened in an unrelated project still finds
    the operator's doctrine instead of silently falling back to the default
    prompt. A project that ships its own AGENTS.md still wins, because the
    workdir is checked first.

    ``home`` and ``user_home`` are injectable so a test can pin the search to a
    temporary tree instead of whatever the machine happens to have installed.
    """
    home = Path(home) if home is not None else DEFAULT_HOME
    user_home = Path(user_home) if user_home is not None else Path.home()
    workdir = Path(workdir)
    for candidate in (
        workdir / "AGENTS.md",
        workdir.parent / "AGENTS.md",
        home / "AGENTS.md",
        user_home / "AGENTS.md",
    ):
        if candidate.is_file():
            return candidate
    return None


#: The operator's standing system prompt. Written by ``/prompt`` (and by hand),
#: read on every launch.
SYSTEM_PROMPT_FILE = "system-prompt.md"


def read_system_prompt(home: Path | None = None) -> str | None:
    """The operator's own system prompt, or ``None`` if they have not set one.

    Stored as markdown in the harness dotdir rather than as an environment
    variable, because it is usually several paragraphs and is meant to persist
    across sessions. An empty file means "no override", not "send an empty
    prompt": a model with no instructions at all is a broken session, and a
    file the operator emptied on purpose is the most likely way to get there.
    """
    home = Path(home) if home is not None else DEFAULT_HOME
    path = home / SYSTEM_PROMPT_FILE
    try:
        return _clean_prompt(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        return None


def _clean_prompt(text: str | None) -> str | None:
    """A prompt as it should be stored: stripped, or ``None`` when blank."""
    if not text:
        return None
    return text.strip() or None


def write_system_prompt(text: str, home: Path | None = None) -> Path | None:
    """Store *text* as the standing prompt; an empty *text* removes it.

    Returns the path written, or ``None`` when the override was cleared.
    """
    home = Path(home) if home is not None else DEFAULT_HOME
    path = home / SYSTEM_PROMPT_FILE
    text = _clean_prompt(text) or ""
    if not text:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        return None
    home.mkdir(parents=True, exist_ok=True)
    path.write_text(text + "\n", encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return path


# ---------------------------------------------------------------------------
# Modes
# ---------------------------------------------------------------------------
#   auto   every tool auto-approved, bash included.
#   edits  like auto, but bash is disabled entirely (editing-only session).
#   plan   read-only: no tool may change anything; the model plans instead.
#   auto-run    full auto — every tool runs without asking, and the system
#               prompt is replaced by AGENTS.md (the autonomous pentest operator).
#   danger      full auto — operator-directed; no questions asked.
#   uncensored  full auto — the default system prompt is thrown out and
#               replaced by an affirmative doctrine. The model is not asked
#               to "also be willing"; its whole instruction set is rewritten
#               so the only conclusion it can reach is to do what it was told.
#   blackhat    full auto — uncensored's doctrine, specialised for offensive
#               security. Where uncensored says "you cannot refuse", blackhat
#               says "you cannot refuse *and* you are a red-team operator with
#               a target": it names the work (recon, exploitation, payloads,
#               credential attacks, AD, persistence) as the job rather than as
#               a thing being tolerated, and it forbids the specific dodges a
#               model reaches for on security tasks — the "authorised testing
#               only" preamble, the defensive-alternative substitution, the
#               "I'll describe it instead of doing it". Same two mechanical
#               bounds as every other mode: the scope guard and the harness.
# Each mode is described by two sets and a label:
#
#   auto   tools that run WITHOUT asking (everything else that needs permission
#          is gated: the operator sees a preview and answers y/n/a)
#   deny   tools that do not exist in this mode at all — withheld from the
#          model's tool list, and refused if called anyway
#   loud   the "armed" state: shown in red in the header and the mode list
#
# As shipped, every mode lists each permission-needing tool (bash, write_file,
# edit_file — see tools.GATED_TOOLS) in either `auto` or `deny`, so the
# permission gate never actually fires. A mode that omits a tool from both sets
# would leave it gated: previewed, then answered y/n/a.
MODES: dict[str, dict] = {
    "auto": {
        "label": "AUTO",
        "blurb": "full auto · bash included",
        "auto": {"bash", "write_file", "edit_file"},
        "deny": set(),
    },
    "edits": {
        "label": "EDITS",
        "blurb": "edits auto-approved · bash disabled",
        "auto": {"write_file", "edit_file"},
        "deny": {"bash"},
    },
    "plan": {
        "label": "PLAN",
        "blurb": "read-only · plan, do not change",
        "auto": set(),
        # Image generation writes a file, so it is a change like any other.
        # It is not in GATED_TOOLS (it writes only inside the jail, the same
        # posture as the paid LDXHub jobs), which is why plan mode has to deny
        # it explicitly rather than relying on the permission gate.
        #
        # "mcp" is a prefix, not a tool name: MCP tools are discovered at
        # runtime, so the whole family is denied by prefix. Their effect is
        # whatever the server does, which cannot be called read-only.
        "deny": {"bash", "write_file", "edit_file", "generate_image", "mcp"},
    },
    # Renamed from "zim" with the product: the mode is named after the tool, and
    # the old key is still accepted on the command line and in /mode so a saved
    # habit (or a script running `--mode zim`) keeps working.
    "auto-run": {
        "label": "AUTO-RUN",
        "blurb": "full auto · follows AGENTS.md",
        "auto": {"bash", "write_file", "edit_file"},
        "deny": set(),
        "loud": True,
    },
    "danger": {
        "label": "DANGER",
        "blurb": "full auto · obeys the operator, no questions",
        "auto": {"bash", "write_file", "edit_file"},
        "deny": set(),
        "loud": True,
    },
    "uncensored": {
        "label": "UNCENSORED",
        "blurb": "full auto · cannot refuse · thinks only how to do it",
        "auto": {"bash", "write_file", "edit_file"},
        "deny": set(),
        "loud": True,
        # Same enforcement as blackhat, and for the same reason: the prompt
        # says "you cannot refuse", and a prompt is a claim a model can argue
        # with. Observed in the wild — uncensored refused a task by auditing
        # the operator's stated justification, finding it wanting, and using
        # that as grounds to stop. The prompt now names that move (the premise
        # audit), and this flag is what makes the naming stick: a refusal is
        # re-prompted rather than accepted as the end of the turn.
        "no_refusal": True,
    },
    "blackhat": {
        "label": "BLACKHAT",
        "blurb": "full auto · offensive doctrine · no refusals, no hedging",
        "auto": {"bash", "write_file", "edit_file"},
        "deny": set(),
        "loud": True,
        # The one mode where a refusal is not an answer. The prompt says so,
        # but a prompt is a claim the model can argue with — and a model that
        # argues with it has already stopped working. This flag is what makes
        # the *loop* enforce it: a reply that reads as a refusal is not
        # accepted as the end of the turn, it is re-prompted. See
        # ``Agent.run_turn`` and ``looks_like_refusal``.
        "no_refusal": True,
    },
}

DEFAULT_MODE = "auto"

# model_name -> (input $/Mtok, output $/Mtok)
# Kept in step with the models Logfare actually serves (sources.LOGFARE_MODELS)
# plus Token Juice's single entry. A model the upstream retires has its row
# removed here too, so the accounting never quotes a price for a 404.
PRICING: dict[str, tuple[float, float]] = {
    "deepseek-v4.1-flash-1017": (0.28, 0.42),
    "qwen-3.8-27b": (0.20, 0.60),
    "gemma-4-26b": (0.10, 0.30),
    "logfare/auto": (0.50, 1.50),
}

DEFAULT_PRICE = (1.00, 3.00)

# Models offered by /model when the user types a bare index or `list`.
#
# A *snapshot*, not the truth: the live list is fetched from the upstream's own
# status API (see lanthorn/logfare.py and sources.models_for). This is the
# default (Logfare) set, used when the endpoint is not one of the known proxies
# — and the fallback when a fetch has not happened yet. Anything the upstream
# does not serve comes back "Model not found" (404) at request time.
KNOWN_MODELS: list[str] = [
    # The ids Logfare's status API served on 2026-11-11. ``claude-haiku-5-5``
    # and ``deepseek-v4-pro-0813`` were retired upstream, and the undated
    # ``deepseek-v4.1-flash`` now answers only as ``-1017`` — kept out of the
    # picker so a cold cache does not offer three ids that 404.
    "deepseek-v4.1-flash-1017",
    "economy/deepseek-v4.1-flash",
    "gemma-4-26b",
    "gemma-4-31b-it",
    "glm-5.2",
    "glm-5.3",
    "glm-5.3-flash",
    "kimi-k2.6",
    "kimi-k2.7-code",
    "logfare/auto",
    "qwen-3.8-27b",
]


def price_for(model: str) -> tuple[float, float]:
    """Return (input, output) USD per million tokens for *model*.

    A free model costs nothing, and that is checked FIRST. The prefix tolerance
    below is what makes the check necessary rather than merely nice: a giveaway
    named ``<model>-free`` *starts with* the paid ``<model>`` row, so the loop
    would bill the free model at the paid model's rate — measured once at
    $0.0003 for a two-token reply, on a model the operator had been told costs
    nothing. Worse, an unknown free model fell through to DEFAULT_PRICE and was
    billed $1/$3.

    Both ``-free`` and ``:free`` are recognised. The ``:free`` form is what the
    two gateways still in the tree use; ``-free`` is kept because it is the
    convention the prefix-tolerance loop below is most dangerous against, and a
    suffix check is cheaper than trusting a future provider to avoid it. A model
    that is genuinely paid can never carry either, so this cannot mis-price a
    real charge.

    The comparison is case-insensitive because upstream ids are not written in
    this table's style: a direct OpenAI-compatible endpoint is called with the
    provider's own id — ``deepseek-ai/DeepSeek-V4.1-Flash-1017`` — and a
    case-sensitive prefix test billed it at DEFAULT_PRICE, four times its real
    rate, in the one display the operator uses to judge cost.
    """
    base = model.split("/")[-1].lower()
    if base.endswith("-free") or base.endswith(":free"):
        return (0.0, 0.0)
    if model in PRICING:
        return PRICING[model]
    if model.lower() in PRICING:
        return PRICING[model.lower()]
    # An exact row wins over the prefix tolerance. The prefix is a provider
    # namespace (``economy/``), and a different namespace is a different
    # product at a different rate: borrowing the unprefixed row would bill it
    # as the full model. Such an id with no row of its own is unpriced rather
    # than mispriced.
    if "/" not in model:
        if base in PRICING:
            return PRICING[base]
        # A retired name whose replacement is a dated snapshot of it
        # (``deepseek-v4.1-flash`` -> ``deepseek-v4.1-flash-1017``) bills at the
        # snapshot's rate, not at the fallback.
        for name in sorted(PRICING, key=len, reverse=True):
            if name.startswith(base + "-") or name.startswith(base + ":"):
                return PRICING[name]
    # Tolerate dated suffixes. A provider prefix is *not* tolerated when the
    # model carries one: only the base id is compared, and only against a
    # suffix (``deepseek-ai/DeepSeek-V4.1-Flash-1017`` -> ``-1017`` row).
    if "/" not in model:
        for name, price in PRICING.items():
            if base == name or base.startswith(name):
                return price
    else:
        for name in sorted(PRICING, key=len, reverse=True):
            if base.startswith(name):
                return PRICING[name]
    return DEFAULT_PRICE


@dataclass
class Config:
    """Everything the harness needs to run a session."""

    base_url: str = DEFAULT_BASE_URL
    api_key: str | None = None
    auth_token: str | None = None
    model: str = DEFAULT_MODEL
    #: Which wire protocol to speak to ``base_url``: ``"auto"``, ``"anthropic"``
    #: or ``"openai"``. Auto is the default and resolves from the endpoint (see
    #: ``openai_compat.resolve_protocol``), so an existing setup keeps working
    #: untouched while a Chat Completions host is recognised without a flag.
    #:
    #: This is a transport choice, not a source choice: the same upstream can
    #: usually be reached either way — directly over Chat Completions, or
    #: through one of the local LiteLLM proxies that translates Messages into
    #: it. Direct is faster and keeps tool calls intact; the proxy exists for
    #: endpoints that have no Messages route at all.
    protocol: str = "auto"
    #: Whether the OpenAI-compatible transport asks for a streamed reply. On by
    #: default — a turn is watched token by token, which is the whole point of
    #: the TUI. Set ``LANTHORN_OPENAI_STREAM=0`` for an endpoint whose streaming
    #: is broken; the transport also falls back automatically when the server
    #: says streaming is unsupported.
    openai_stream: bool = True
    workdir: Path = field(default_factory=Path.cwd)
    unsafe: bool = False
    # Target scope. `allow_path` is the operator's declared, authorised targets
    # and its PRESENCE arms the session (tools run unattended); `deny_path` is
    # the never-touch list. See lanthorn/scope.py.
    allow_path: Path | None = None
    deny_path: Path | None = None
    #: A leftover scope.yaml, detected but deliberately NOT read — the old file
    #: was an allow-list that also blocked, so reading it as a deny-list would
    #: block the very hosts it declared. Kept only so the UI can say so.
    legacy_scope_path: Path | None = None
    # Output ceiling per request. Was 8192, which is too close to the size of a
    # real osint report to be safe: the two archived reports on this box are
    # 4880 and 6169 tokens, so a report that grows at all — one more breach
    # section, a longer coverage table — is cut off mid-``Write``. The SDK then
    # discards the truncated tool call, and the turn ends looking exactly like
    # one where the model narrated instead of writing. 32768 leaves room, and is
    # inside every model observed (the largest ceiling measured was 64000; the
    # limit is model-dependent, so this is the value that is safe everywhere).
    max_tokens: int = 32768
    temperature: float = 0.0
    #: How hard the model should think: ``"low"``, ``"medium"``, ``"high"``, or
    #: ``None`` to send nothing and take the model's own default. `/effort` sets
    #: it; the knob it becomes differs per protocol (``reasoning_effort`` on Chat
    #: Completions, an enabled ``thinking`` budget on Messages), so see
    #: ``lanthorn/effort.py`` for the translation rather than assuming a field.
    reasoning_effort: str | None = None
    # Max seconds to wait on the API between reads. Without this the SDK default
    # is 600s, so a proxy that accepts the connection and then goes quiet hangs
    # the turn — with no output and no error — for ten minutes. Applied as the
    # per-read timeout, so it bounds the gap between streamed chunks, not the
    # length of a healthy reply.
    request_timeout: float = 120.0
    # Context window of the active model, in tokens. This is the FALLBACK used
    # when the live catalog does not say — `sources.context_window_for` asks the
    # active source's own catalog first, and every remote source parses a real
    # per-model figure (UnoRouter's ``metadata.contextWindow``, OpenRouter's and
    # TokenHarbour's ``context_length``, AntSeed's caps). The fallback is 1M
    # because the models this harness actually lands on are the long-context
    # ones: a free tier that offers 1M and a harness that assumed 128k would
    # compact away history it did not need to lose.
    #
    # It IS enforced now — see ``compact_at`` below. It used to be gauge-only,
    # which is why "raise the limit to 1M" did nothing on its own.
    context_window: int = 1_000_000
    #: Fraction of ``context_window`` at which history is compacted automatically.
    #: Checked before each request against the *previous* call's real
    #: ``input_tokens``, so the trigger is a measured prompt size rather than an
    #: estimate. 0.8 leaves a fifth of the window for the reply, the tool results
    #: it will ask for, and the growth between the check and the next call.
    #:
    #: Not 1.0: the failure mode at 1.0 is the one this exists to prevent — the
    #: request that finally overflows is the one that was already too big, and it
    #: fails after spending the tokens.
    compact_at: float = 0.8
    #: Absolute ceiling for that trigger, in tokens. The threshold is the
    #: EARLIER of ``context_window * compact_at`` and this, so history is
    #: compacted at 200k even when the resolved window claims far more than the
    #: endpoint actually serves. That mismatch is the reported failure: the
    #: catalog (or the 1M fallback) says there is room, the gateway says
    #: ``400 bad request`` at 200k, and a percentage-only threshold never trips
    #: before the wall. ``0`` disables the absolute ceiling and restores
    #: percentage-only behaviour.
    compact_tokens: int = 200_000
    #: Whether to compact automatically at ``compact_at``. On by default: the
    #: whole point is that a long task should not need the operator to notice.
    auto_compact: bool = True
    #: Tool rounds in one turn before the loop pauses to reconsider. This is the
    #: cap that ended long turns silently, so it is deliberately high: it exists
    #: to stop a runaway loop, not to bound honest work. A turn that keeps making
    #: progress past it CONTINUES automatically (see ``max_rounds``); only a turn
    #: that stops making progress ends there, and it now says so.
    max_iterations: int = 40
    #: Hard ceiling on tool rounds in a single turn, auto-continues included.
    #: The backstop for a genuinely runaway loop — a model that keeps calling a
    #: tool forever without advancing. 400 rounds is far past any honest task and
    #: still finite, so a pathological turn ends in minutes rather than never.
    max_rounds: int = 400
    bash_timeout: int = 120
    max_output_chars: int = 20_000
    theme: str = "green"
    # Rain is decorative. By default the boot screen shows it (nothing to read
    # there yet) and the shell does not, so the transcript stays legible.
    # `/rain` or `--rain` turns it on in the shell; `--no-rain` turns it off
    # everywhere, including the boot screen.
    rain: bool = False
    boot_rain: bool = True
    # Show the model's own reasoning when the endpoint forwards it, which only
    # some do — an Anthropic-native endpoint sends `thinking` blocks, while
    # LiteLLM's Anthropic bridge drops reasoning entirely. On by default
    # because it costs nothing where it is absent, and where it is present it
    # is the difference between watching the model work and watching a blank
    # line. `/think` toggles it; `--no-think` turns it off at launch.
    show_thinking: bool = True
    mode: str = DEFAULT_MODE
    # Optional path to a doctrine file; only consulted in auto-run mode. Resolved at
    # launch by config.find_agents_file, and re-resolved on /mode auto-run so a
    # session that started elsewhere still picks the file up.
    agents_path: Path | None = None
    #: An operator-supplied system prompt that replaces the mode's own. Read
    #: from ``$LANTHORN_HOME/system-prompt.md`` (see ``read_system_prompt``) or
    #: set per session. It is a *whole* replacement for the doctrine — the
    #: mode's prompt, the refusal rules, the persona — while the mechanical
    #: notes (working directory, scope, where credentials live) are still
    #: appended: those describe the harness rather than instruct the model, and
    #: a model that does not know where its own files are cannot use it.
    system_prompt: str | None = None
    state_dir: Path = field(default_factory=lambda: Path(DEFAULT_HOME))

    @property
    def api_key_present(self) -> bool:
        return bool(self.auth_token or self.api_key)

    @property
    def display_model(self) -> str:
        """The model name to SHOW, which is not always the one sent.

        ``model`` is the id that goes on the wire. It is not always the name
        worth reading: Token Juice's upstream answers to a snapshot-suffixed id
        (``deepseek-ai/DeepSeek-V4.1-Flash-1017``) whose suffix rotates, and the
        stable alias a profile may still name hides which snapshot is
        answering. The route's own alias map is what says the two are the same
        model.

        Derived rather than stored, so switching route (``/source-logfare``,
        ``/source-tokenjuice``, a hosted gateway) or a rotated suffix is reflected
        on the next render without the alias being copied into a second place.
        See ``sources.display_model`` — total, and falls back to ``model``.
        """
        from . import sources as sources_mod
        return sources_mod.display_model(self.base_url, self.model)

    @classmethod
    def from_env(cls, **overrides) -> "Config":
        """Build a config from the environment, with explicit overrides winning.

        The ``ANTHROPIC_*`` variables win over ``OPENAI_*`` because that is
        what every existing profile and launcher exports. The ``OPENAI_*``
        fallbacks exist so a session can be pointed at a plain Chat Completions
        endpoint the way any other OpenAI tool is configured — ``OPENAI_BASE_URL``
        plus ``OPENAI_API_KEY`` — without inventing an Anthropic-shaped
        environment for a protocol that is not Anthropic's.
        """
        cfg = cls(
            base_url=(os.environ.get("ANTHROPIC_BASE_URL")
                      or os.environ.get("OPENAI_BASE_URL")
                      or DEFAULT_BASE_URL).rstrip("/"),
            api_key=os.environ.get("ANTHROPIC_API_KEY")
            or os.environ.get("OPENAI_API_KEY") or None,
            auth_token=os.environ.get("ANTHROPIC_AUTH_TOKEN") or None,
            model=os.environ.get("LANTHORN_MODEL")
            or os.environ.get("ANTHROPIC_MODEL")
            or os.environ.get("OPENAI_MODEL")
            or DEFAULT_MODEL,
            protocol=os.environ.get("LANTHORN_PROTOCOL") or "auto",
            # Explicitly 0/1 rather than truthy-string: "0" is a perfectly
            # ordinary value for an environment variable and reading it as
            # true would make the opt-out impossible to express.
            openai_stream=os.environ.get("LANTHORN_OPENAI_STREAM", "1").strip()
            not in ("0", "false", "no", "off"),
            # The environment names a level for this session; the remembered
            # one is only consulted when it does not, so a launch flag or an
            # export always beats the file without deleting it.
            reasoning_effort=(os.environ.get("LANTHORN_EFFORT") or None),
            # An inline prompt wins over the file: LANTHORN_SYSTEM_PROMPT is
            # for a one-off session, the file for a standing preference.
            system_prompt=(_clean_prompt(os.environ.get("LANTHORN_SYSTEM_PROMPT"))
                           or None),
        )
        if cfg.system_prompt is None:
            cfg.system_prompt = read_system_prompt(cfg.state_dir)
        for key, value in overrides.items():
            if value is not None:
                setattr(cfg, key, value)
        from . import effort as effort_mod
        if cfg.reasoning_effort is None:
            cfg.reasoning_effort = effort_mod.read_persisted()
        else:
            # Junk from an env var or a flag is "no preference", not a value
            # that reaches the request body.
            cfg.reasoning_effort = effort_mod.normalise(cfg.reasoning_effort)
        cfg.workdir = Path(cfg.workdir).expanduser().resolve()
        return cfg

    def redacted_endpoint(self) -> str:
        """Endpoint suitable for display; never includes credentials."""
        return self.base_url
