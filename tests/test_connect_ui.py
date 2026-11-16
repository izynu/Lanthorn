"""`/connect` and `/models <provider>` through the real app.

What an operator does: run ``/connect``, see every provider and which have a
key; run ``/connect openrouter <key>``, see it stored without the key echoed;
run ``/models openrouter``, land on OpenRouter with its model and its catalog.
Each of those is driven through ``LanthornApp`` rather than called as a
function, because the failures this is for are the wiring ones — a command
registered in one table but not the palette, a switch that sets the endpoint
but not the protocol, a key that is stored but never applied.

No network: every catalog fetch is stubbed, and every key lives in a temp home.
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

HOME = Path(tempfile.mkdtemp(prefix="zz-connect-"))
os.environ["LANTHORN_HOME"] = str(HOME)
for _var in ("ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL", "OPENAI_API_KEY",
             "OPENROUTER_API_KEY", "GEMINI_API_KEY", "NVIDIA_API_KEY"):
    os.environ.pop(_var, None)

from lanthorn import config as config_mod  # noqa: E402
from lanthorn import providers as prov  # noqa: E402
from lanthorn.ui.app import LanthornApp  # noqa: E402

config_mod.DEFAULT_HOME = HOME
import importlib  # noqa: E402

for _name in ("anthropic_direct", "openai_direct", "deepseek_direct", "gemini",
              "openrouter", "nvidia", "unorouter", "aihubmix", "beatapi",
              "moonmath", "iteracompute", "infronai", "sources"):
    _mod = importlib.import_module(f"lanthorn.{_name}")
    if hasattr(_mod, "LANTHORN_HOME"):
        _mod.LANTHORN_HOME = HOME

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, extra: str = "") -> None:
    RESULTS.append((name, ok, extra))
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {extra}")


def transcript(app) -> str:
    return "\n".join(s.text for s in app.query_one("#transcript").lines)


class _Model:
    """A catalog entry with the attributes the picker actually reads.

    Taken from OpenRouter's own ``Model`` — ``blurb`` and ``tools`` are not
    decoration: ``/models`` prints the blurb and filters on tool support, so a
    stub without them fails the render rather than the assertion.
    """

    def __init__(self, mid: str):
        self.id = mid
        self.label = mid
        self.chat = True
        self.free = mid.endswith(":free")
        self.tools = True
        self.blurb = "free · 128k ctx"


def make_app(wd: Path, base_url: str = "https://openrouter.ai/api") -> LanthornApp:
    """A session already on OpenRouter, so /models openrouter is a listing.

    The switch path is covered by test_model_switch.py and by the keyless case
    below; what is tested here is the *store* half, which is what /connect adds.
    """
    cfg = config_mod.Config.from_env(workdir=wd, api_key="x",
                                     base_url=base_url, boot_rain=False)
    cfg.state_dir = wd / "ui-state"
    return cfg


async def settle(pilot, app, until: str | None = None, tries: int = 150) -> None:
    for _ in range(tries):
        await asyncio.sleep(0.02)
        await pilot.pause()
        if until and until in transcript(app):
            return
    await asyncio.sleep(0.2)
    await pilot.pause()


async def test_list(wd: Path) -> None:
    app = LanthornApp(make_app(wd / "list"))
    async with app.run_test(size=(120, 45)) as pilot:
        app.pop_screen()
        await pilot.pause()
        app._handle_command("/connect")
        await settle(pilot, app, "CONNECT")
        blob = transcript(app)
        check("/connect renders", "CONNECT" in blob, blob[-200:])
        for provider in prov.PROVIDERS:
            if provider.key not in blob:
                check(f"/connect lists {provider.key}", False)
                break
        else:
            check("/connect lists every provider", True)
        check("/connect names the add command",
              "/connect <provider> <key>" in blob)
        # No key is set anywhere, so every provider that needs one says so.
        check("/connect shows a missing key",
              blob.count("no key") >= 5, str(blob.count("no key")))


async def test_store_and_switch(wd: Path) -> None:
    from lanthorn import openrouter as or_mod

    key = "sk-or-v1-THISISASECRETVALUE"
    app = LanthornApp(make_app(wd / "store"))
    async with app.run_test(size=(120, 45)) as pilot:
        app.pop_screen()
        await pilot.pause()

        app._handle_command(f"/connect openrouter {key}")
        await settle(pilot, app, "key stored")
        blob = transcript(app)
        check("/connect stores a key", "OpenRouter key stored" in blob, blob[-300:])
        check("the key is NOT echoed in full", key not in blob, blob[-300:])
        check("the last four are shown", key[-4:] in blob, blob[-300:])
        check("the profile is written", or_mod.PROFILE.is_file(),
              str(or_mod.PROFILE))
        check("the stored file holds the key",
              key in or_mod.PROFILE.read_text(encoding="utf-8"))
        if os.name != "nt":
            check("the profile is 600",
                  (or_mod.PROFILE.stat().st_mode & 0o777) == 0o600,
                  oct(or_mod.PROFILE.stat().st_mode & 0o777))

        # A pasted `export …` line is a common paste and must not be stored whole.
        app._handle_command('/connect deepseek export DEEPSEEK_API_KEY="sk-ds-abc"')
        await settle(pilot, app, "DeepSeek key stored")
        ds = prov.BY_KEY["deepseek"]
        text = ds.profile.read_text(encoding="utf-8")
        check("a pasted export line is unwrapped",
              'ANTHROPIC_AUTH_TOKEN="sk-ds-abc"' in text, text)

        # The key was stored for the provider the session is already on, so it
        # must be in force now — not on the next launch.
        check("the stored key is applied to the live session",
              app.cfg.auth_token == key, str(app.cfg.auth_token)[:6])
        check("applying it is announced",
              "applied to this session" in blob, blob[-400:])

        # Listing the active provider's catalog: offline, via a stub.
        fetched = ([_Model("dots-studio/dots:free"), _Model("other/model")], "")
        with mock.patch.object(or_mod, "fetch_models", lambda *a, **k: fetched):
            app._handle_command("/models openrouter")
            await settle(pilot, app, "OPENROUTER")
        blob = transcript(app)
        check("the catalog is listed", "OPENROUTER — FREE MODELS" in blob,
              blob[-400:])
        check("a model from it is shown", "dots-studio/dots:free" in blob,
              blob[-400:])
        check("the endpoint did not move",
              app.cfg.base_url == "https://openrouter.ai/api", app.cfg.base_url)

        # Clearing removes the stored key and says the session still holds it.
        app._handle_command("/connect openrouter clear")
        await settle(pilot, app, "removed the stored")
        check("clear removes the profile", not or_mod.PROFILE.exists())
        check("clear is confirmed",
              "removed the stored OpenRouter key" in transcript(app))


async def test_switch_to_another_provider(wd: Path) -> None:
    """Storing a key for a provider you are not on, then switching to it.

    The two things a switch must get right are the protocol and the model id.
    A Messages request to a Chat Completions host is a 404 on every turn while
    the catalog keeps working, and a model id carried over from the previous
    provider is a 404 on the first turn — both look like "the tool is broken".
    """
    from lanthorn import deepseek_direct as ds_mod

    key = "sk-ds-REALKEYVALUE1234"
    app = LanthornApp(make_app(wd / "switch"))
    async with app.run_test(size=(120, 45)) as pilot:
        app.pop_screen()
        await pilot.pause()
        app._handle_command(f"/connect deepseek {key}")
        await settle(pilot, app, "DeepSeek key stored")
        blob = transcript(app)
        check("a key for an inactive provider is stored", ds_mod.PROFILE.is_file())
        check("it is not applied to the session yet",
              app.cfg.auth_token != key, str(app.cfg.auth_token)[:6])
        check("the store says how to switch", "/models deepseek" in blob,
              blob[-300:])

        fetched = ([_Model("deepseek-chat"), _Model("deepseek-reasoner")], "")
        with mock.patch.object(ds_mod, "fetch_models", lambda *a, **k: fetched):
            app._handle_command("/models deepseek")
            await settle(pilot, app, "DEEPSEEK")
        blob = transcript(app)
        check("the switch moved the endpoint",
              app.cfg.base_url == "https://api.deepseek.com", app.cfg.base_url)
        check("the protocol is the provider's",
              app.cfg.protocol == "openai", app.cfg.protocol)
        check("the key is in force", app.cfg.auth_token == key,
              str(app.cfg.auth_token)[:6])
        check("the switch is announced",
              "source switched to DeepSeek" in blob, blob[-400:])
        check("the model is the provider's own",
              app.cfg.model == "deepseek-chat", app.cfg.model)
        check("the catalog is listed", "DEEPSEEK — FREE MODELS" in blob,
              blob[-400:])
        # The three direct providers have no set_current of their own, so the
        # switch must not call one — and must not crash reaching for it.
        check("the switch completes without a selection hook",
              "source switched to DeepSeek" in blob)


async def test_keyless_switch_is_refused(wd: Path) -> None:
    app = LanthornApp(make_app(wd / "keyless"))
    async with app.run_test(size=(120, 45)) as pilot:
        app.pop_screen()
        await pilot.pause()
        app._handle_command("/models anthropic")
        await settle(pilot, app, "no Anthropic")
        blob = transcript(app)
        check("a keyless switch is refused", "no Anthropic (Claude) key" in blob,
              blob[-300:])
        check("it names the fix", "/connect anthropic <key>" in blob, blob[-300:])
        check("the session did not move",
              "api.anthropic.com" not in app.cfg.base_url, app.cfg.base_url)


async def test_unknown(wd: Path) -> None:
    app = LanthornApp(make_app(wd / "unknown"))
    async with app.run_test(size=(120, 45)) as pilot:
        app.pop_screen()
        await pilot.pause()
        app._handle_command("/connect nosuchprovider sk-1")
        await settle(pilot, app, "unknown provider")
        check("/connect rejects an unknown provider",
              "unknown provider" in transcript(app))
        app._handle_command("/models nosuchprovider")
        await settle(pilot, app, "unknown provider")
        blob = transcript(app)
        check("/models rejects an unknown provider",
              "unknown upstream" in blob, blob[-300:])


async def test_help_and_palette(wd: Path) -> None:
    app = LanthornApp(make_app(wd / "help"))
    async with app.run_test(size=(120, 45)) as pilot:
        app.pop_screen()
        await pilot.pause()
        app._handle_command("/help")
        await settle(pilot, app, "/connect")
        check("/connect is in /help", "/connect" in transcript(app))
        entries = app._palette_entries()
        # Command entries carry a "cmd:" key prefix and the slash form as the
        # title — the pair the palette renders.
        check("/connect is in the palette",
              "cmd:connect" in [e.key for e in entries]
              and "/connect" in [e.title for e in entries],
              str([e.key for e in entries][:5]))


async def main() -> int:
    wd = Path(tempfile.mkdtemp(prefix="zz-connect-wd-"))
    await test_list(wd)
    await test_store_and_switch(wd)
    await test_switch_to_another_provider(wd)
    await test_keyless_switch_is_refused(wd)
    await test_unknown(wd)
    await test_help_and_palette(wd)
    failed = [r for r in RESULTS if not r[1]]
    print(f"\n{len(RESULTS) - len(failed)} passed, {len(failed)} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
