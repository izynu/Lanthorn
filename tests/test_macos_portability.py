"""Regression suite for macOS support in the shell entry points.

Run:  python tests/test_macos_portability.py

The harness is Python and cross-platform; the launcher, installer and proxy
manager are bash and were written on Linux. Four things in them were
GNU/Linux-only and would have failed on a stock Mac:

1. ``readlink -f`` — GNU's ``-f`` does not exist in BSD ``readlink``, so every
   self-locating script died before it could find the checkout.
2. ``ss`` — Linux-only; macOS ships ``lsof`` instead, so the proxy manager could
   not identify what held :4001.
3. ``seq`` — not reliably present on macOS; replaced with bash arithmetic loops.
4. ``~/.bashrc`` — macOS defaults to zsh, so the PATH hint told the operator to
   edit a file their shell never reads.

The runtime check below exercises the symlink-following helper directly: it
copies the block out of ``run.sh``, runs it through a two-hop symlink chain and
asserts it reports the real directory. That is the part a static grep cannot
prove, and it is the part that broke.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

SCRIPTS = [
    "install.sh",
    "setup.sh",
    "run.sh",
    "packaging/lanthorn",
    "packaging/logfare/start-litellm.sh",
    "packaging/iteracompute/start-litellm.sh",
    "packaging/infronai/start-litellm.sh",
]
PROXY_MANAGERS = [
    "packaging/logfare/start-litellm.sh",
    "packaging/iteracompute/start-litellm.sh",
    "packaging/infronai/start-litellm.sh",
]

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, extra: str = "") -> None:
    RESULTS.append((name, ok, extra))


def code_lines(path: Path) -> list[str]:
    """The script's lines with comments stripped, so prose about `readlink -f`
    does not count as a use of it."""
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        # Drop a trailing comment only when it is not inside a string. The
        # scripts do not put `#` inside strings on the lines checked here.
        out.append(line.split(" #", 1)[0] if " #" in line else line)
    return out


_BASH_FORM: str | None = None
_BASH_PROGRAM: list[str] = ["bash"]


def bash_argv(*args: str) -> list[str]:
    """``bash`` plus *args*, with Windows paths in the form that shell accepts.

    POSIX hosts are untouched. On Windows, ``bash`` on PATH is not reliably the
    shell these scripts are written for: it may be WSL's, which cannot see the
    ``D:`` drive checkout at all and reports every script as missing, or Git's MSYS
    build, which wants the ``/d/...`` mount form. Which shell and which path
    form work is asked once, by having each candidate open a known file,
    rather than assumed.
    """
    global _BASH_FORM
    if sys.platform != "win32":
        return ["bash", *args]
    global _BASH_PROGRAM
    if _BASH_FORM is None:
        probe = str(ROOT / "run.sh")
        programfiles = os.environ.get("ProgramFiles", r"C:\Program Files")
        candidates = [
            ("native", ["bash"], probe),
            ("msys", ["bash"], _msys(probe)),
            ("msys", [os.path.join(programfiles, "Git", "bin", "bash.exe")],
             _msys(probe)),
        ]
        for form, argv, path in candidates:
            try:
                proc = subprocess.run(argv + ["-c", "test -f \"$1\"", "_", path],
                                      capture_output=True, text=True)
            except OSError:
                continue
            if proc.returncode == 0:
                _BASH_FORM = form
                _BASH_PROGRAM = argv
                break
        else:
            _BASH_FORM = "native"
    out = list(_BASH_PROGRAM)
    for a in args:
        out.append(_msys(a) if _BASH_FORM == "msys" else a)
    return out


def _msys(path: str) -> str:
    drive = re.match(r"^([A-Za-z]):[\\/](.*)$", path)
    if drive is None:
        return path.replace("\\", "/")
    return "/" + drive.group(1).lower() + "/" + drive.group(2).replace("\\", "/")


def test_bash_syntax() -> None:
    for rel in SCRIPTS:
        proc = subprocess.run(bash_argv("-n", str(ROOT / rel)),
                              capture_output=True, text=True)
        check(f"bash -n {rel}", proc.returncode == 0, proc.stderr.strip()[:200])


def test_no_gnu_readlink() -> None:
    for rel in SCRIPTS:
        hits = [ln for ln in code_lines(ROOT / rel) if "readlink -f" in ln]
        check(f"no readlink -f in {rel}", not hits, hits[0][:120] if hits else "")


def test_no_gnu_option_separators() -> None:
    """`dirname --`/`basename --` are GNU habits; BSD tools may treat `--` as a
    path. None of these paths start with a dash, so the separator is not needed."""
    for rel in SCRIPTS:
        hits = [ln for ln in code_lines(ROOT / rel)
                if re.search(r"\b(dirname|basename) --", ln)]
        check(f"no dirname/basename -- in {rel}", not hits, hits[0][:120] if hits else "")


def test_no_seq() -> None:
    for rel in SCRIPTS:
        hits = [ln for ln in code_lines(ROOT / rel) if "$(seq " in ln]
        check(f"no $(seq) in {rel}", not hits, hits[0][:120] if hits else "")


def test_proxy_manager_has_both_port_tools() -> None:
    for rel in PROXY_MANAGERS:
        text = (ROOT / rel).read_text(encoding="utf-8")
        check(f"{rel}: guards ss behind command -v",
              'command -v ss' in text, "ss is Linux-only")
        check(f"{rel}: falls back to lsof",
              "lsof -nP -iTCP:" in text, "macOS has no ss")
        check(f"{rel}: port hint is portable",
              'ss -ltnp \\"sport' not in text, "hint would fail on macOS")


def test_installer_macos_hints() -> None:
    text = (ROOT / "install.sh").read_text(encoding="utf-8")
    check("install.sh: brew hint for old python",
          "brew install python@3.12" in text, "macOS ships 3.9")
    check("install.sh: chooses the shell's rc file",
          ".zshrc" in text and 'case "${SHELL:-}" in *zsh' in text,
          "zsh does not read ~/.bashrc")


def _same_dir(reported: str, real: Path) -> bool:
    """Whether the shell's ``pwd -P`` output names *real*.

    A shell that sees Windows paths through an MSYS mount answers in its own
    form (``/c/Users/...``), which is the same directory as ``C:\\Users\\...``.
    POSIX output matches the native form directly.
    """
    if reported == str(real):
        return True
    return sys.platform == "win32" and reported == _msys(str(real))


def test_self_dir_helper_follows_symlinks() -> None:
    """Run the exact helper from run.sh through a two-hop symlink chain."""
    text = (ROOT / "run.sh").read_text(encoding="utf-8")
    start = text.index('_self_path="${BASH_SOURCE[0]}"')
    end = text.index('_self_dir="$(cd -- "$(dirname "$_self_path")" && pwd -P)"')
    block = text[start:end] + '_self_dir="$(cd -- "$(dirname "$_self_path")" && pwd -P)"'

    with tempfile.TemporaryDirectory() as td_str:
        td = Path(td_str)
        real = td / "real"
        links = td / "links"
        real.mkdir()
        links.mkdir()
        tool = real / "tool.sh"
        tool.write_text("#!/usr/bin/env bash\nset -euo pipefail\n"
                        + block + "\nprintf '%s\\n' \"$_self_dir\"\n")
        tool.chmod(0o755)
        try:
            (links / "hop1.sh").symlink_to(tool)
            (links / "hop2.sh").symlink_to(links / "hop1.sh")
        except OSError as exc:
            # Creating a symlink needs SeCreateSymbolicLinkPrivilege on Windows,
            # which an ordinary account does not hold. The helper itself is
            # still exercised below by its real path; the chain cannot be built.
            check("self-dir helper resolves a symlink chain",
                  sys.platform == "win32" and exc.winerror == 1314,
                  f"symlink unavailable: {exc}")
            proc = subprocess.run(bash_argv(str(tool)), capture_output=True, text=True)
            check("self-dir helper resolves a direct path",
                  proc.returncode == 0 and _same_dir(proc.stdout.strip(), real),
                  proc.stdout.strip())
            return

        proc = subprocess.run(bash_argv(str(links / "hop2.sh")),
                              capture_output=True, text=True)
        got = proc.stdout.strip()
        check("self-dir helper resolves a symlink chain",
              proc.returncode == 0 and _same_dir(got, real),
              f"got {got!r}, want {str(real)!r} {proc.stderr.strip()[:120]}")

        # And invoked by its real path, it must still land on the real dir.
        proc = subprocess.run(bash_argv(str(tool)), capture_output=True, text=True)
        check("self-dir helper resolves a direct path",
              proc.returncode == 0 and _same_dir(proc.stdout.strip(), real),
              proc.stdout.strip())


def main() -> int:
    test_bash_syntax()
    test_no_gnu_readlink()
    test_no_gnu_option_separators()
    test_no_seq()
    test_proxy_manager_has_both_port_tools()
    test_installer_macos_hints()
    test_self_dir_helper_follows_symlinks()

    failed = [n for n, ok, _ in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} passed")
    for name, ok, extra in RESULTS:
        print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  {extra}" if not ok and extra else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
