"""The one-time move from the old state directory.

The tool was called ZimZilla and kept its keys and sessions in ``~/.zimzilla``.
The rename has to find them, and has to do it without a second copy of a
credential being left behind, without moving anything when the operator has
already started using the new name, and without touching a directory the
operator pointed at with ``LANTHORN_HOME``.

Run:  python tests/test_rename_migration.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lanthorn import config as config_mod  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, extra: str = "") -> None:
    RESULTS.append((name, ok, extra))
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {extra}")


def fresh() -> tuple[Path, Path]:
    """A sandbox with a fake home; returns (home, old dotdir)."""
    home = Path(tempfile.mkdtemp(prefix="ln-migrate-"))
    return home, home / ".zimzilla"


def make_old(old: Path) -> None:
    (old / "openrouter").mkdir(parents=True)
    (old / "openrouter" / "source").write_text(
        'export ANTHROPIC_AUTH_TOKEN="sk-or-REDACTED"\n', encoding="utf-8")
    (old / "sessions").mkdir()
    (old / "sessions" / "one.json").write_text("{}", encoding="utf-8")


def main() -> int:
    # --- the old directory is adopted, once -------------------------------
    home, old = fresh()
    make_old(old)
    with mock.patch.object(config_mod.Path, "home", staticmethod(lambda: home)):
        moved = config_mod._adopt_legacy_home(home / ".lanthorn", old)
    check("the old directory is reported as adopted", moved == str(old), moved)
    check("the new directory exists", (home / ".lanthorn").is_dir())
    check("the old directory is gone", not old.exists())
    check("a key came with it",
          (home / ".lanthorn" / "openrouter" / "source").is_file())
    check("a session came with it",
          (home / ".lanthorn" / "sessions" / "one.json").is_file())
    check("there is no second copy of the key", not old.exists())

    # --- a second launch moves nothing ------------------------------------
    again = config_mod._adopt_legacy_home(home / ".lanthorn", old)
    check("a second launch adopts nothing", again == "", again)

    # --- a new directory already in use wins over the old one -------------
    home, old = fresh()
    make_old(old)
    (home / ".lanthorn").mkdir()
    (home / ".lanthorn" / "keep.txt").write_text("mine", encoding="utf-8")
    moved = config_mod._adopt_legacy_home(home / ".lanthorn", old)
    check("an existing new directory is not overwritten", moved == "", moved)
    check("the old one is left where it was", old.is_dir())
    check("the new one is untouched",
          (home / ".lanthorn" / "keep.txt").read_text(encoding="utf-8") == "mine")

    # --- nothing to adopt --------------------------------------------------
    home, old = fresh()
    check("a fresh machine adopts nothing",
          config_mod._adopt_legacy_home(home / ".lanthorn", old) == "")

    # --- LANTHORN_HOME is the authority, and is never moved ----------------
    env = {"LANTHORN_HOME": str(Path(tempfile.mkdtemp()) / "chosen")}
    with mock.patch.dict(os.environ, env, clear=False):
        os.environ.pop("ZIMZILLA_HOME", None)
        chosen = config_mod._home_from_env()
    check("LANTHORN_HOME is honoured", str(chosen) == env["LANTHORN_HOME"],
          str(chosen))

    # --- the old env spelling still works, so an existing shell keeps going
    with mock.patch.dict(os.environ, {"ZIMZILLA_HOME": "/tmp/old-spelling"},
                         clear=False):
        os.environ.pop("LANTHORN_HOME", None)
        from_old = config_mod._home_from_env()
    check("the old ZIMZILLA_HOME is still read",
          from_old == Path("/tmp/old-spelling"), str(from_old))

    # --- and the new spelling wins when both are set -----------------------
    with mock.patch.dict(os.environ, {"LANTHORN_HOME": "/tmp/new",
                                      "ZIMZILLA_HOME": "/tmp/old"}, clear=False):
        both = config_mod._home_from_env()
    check("LANTHORN_HOME wins over the old spelling",
          both == Path("/tmp/new"), str(both))

    failed = [r for r in RESULTS if not r[1]]
    print(f"\n{len(RESULTS) - len(failed)} passed, {len(failed)} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
