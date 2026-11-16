"""Print what /connect and /prompt look like, as text, for a human to read.

Not a test: a way to look at the screens without a terminal. Run it, read the
output, and delete nothing — it is cheap and it catches the layout mistakes an
assertion never would (a column that does not line up, a label that runs into
its value).
"""

import asyncio
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

HOME = Path(tempfile.mkdtemp(prefix="zz-render-"))
os.environ["LANTHORN_HOME"] = str(HOME)
for _var in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "OPENROUTER_API_KEY",
             "GEMINI_API_KEY", "NVIDIA_API_KEY", "ANTHROPIC_BASE_URL"):
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


def show(app, title: str) -> None:
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)
    lines = [s.text for s in app.query_one("#transcript").lines]
    print("\n".join(lines[-40:]))


async def main() -> None:
    wd = Path(tempfile.mkdtemp(prefix="zz-render-wd-"))
    cfg = config_mod.Config.from_env(workdir=wd, api_key="x",
                                     base_url="https://openrouter.ai/api",
                                     boot_rain=False)
    cfg.state_dir = wd / "ui-state"
    app = LanthornApp(cfg)
    async with app.run_test(size=(120, 50)) as pilot:
        app.pop_screen()
        await pilot.pause()

        app._handle_command("/connect")
        await pilot.pause()
        show(app, "/connect  (the list)")

        app._handle_command("/connect gemini")
        await pilot.pause()
        show(app, "/connect gemini  (one provider, no key)")

        app._handle_command("/connect openrouter sk-or-v1-abcdefghijklmnop")
        await pilot.pause()
        show(app, "/connect openrouter <key>  (stored)")

        app._handle_command("/connect openrouter")
        await pilot.pause()
        show(app, "/connect openrouter  (one provider, with a key)")

        app._handle_command("/prompt You are a terse engineer. Answer in "
                            "sentences, never lists.")
        await pilot.pause()
        app._handle_command("/prompt")
        await pilot.pause()
        show(app, "/prompt  (after setting one)")

        app._handle_command("/prompt clear")
        await pilot.pause()
        app._handle_command("/prompt")
        await pilot.pause()
        show(app, "/prompt  (cleared)")


if __name__ == "__main__":
    asyncio.run(main())
