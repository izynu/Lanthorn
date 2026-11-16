"""Regression suite for the one-line installer and the launcher it writes.

Run:  python tests/test_install.py

Hermetic: no network, no real dependencies. It builds a throwaway git repo to
stand in for the upstream, runs install.sh against it with the dependency and
credential steps switched off, and checks the install layout it produces — the
checkout, the launcher shim, and the interpreter the launcher then chooses.

What is being protected is the promise install.sh makes: `lanthorn` on PATH,
running the checkout's own venv, from any directory, with no activation. The
parts that break silently are the two that decide *which* interpreter runs — the
shim's LANTHORN_ROOT and the launcher's root derivation — so those are asserted
directly, by reading the path the launcher reports in its failure message, not
merely by exit code.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

from support import private_file_ok  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []

#: First line of a piped script: reports the HOME the child bash will use.
REPORT_HOME = "echo @@HOME=$HOME" + chr(10)


def check(name: str, ok: bool, extra: str = "") -> None:
    RESULTS.append((name, ok, extra))


def run(argv: list[str], *, env: dict, cwd: Path | str, stdin: str | None = None):
    """Run a command, returning (returncode, stdout, stderr). Never raises.

    Bytes in, bytes out, decoded by hand. A script fed on stdin has to arrive
    byte-for-byte: Windows Python's text mode translates every newline to CRLF
    on the way in, and bash then reads the CR as part of the last word on each
    line -- `set -euo pipefail` became `pipefail
` and the installer died
    before its first step. That is a property of this host, not of the script.
    """
    proc = subprocess.run(
        argv,
        cwd=str(cwd),
        env=env,
        input=None if stdin is None else stdin.encode("utf-8"),
        capture_output=True,
    )
    out = proc.stdout.decode("utf-8", "replace")
    err = proc.stderr.decode("utf-8", "replace")
    # HOME as the child script sees it, not as this process set it. Git Bash
    # on Windows is a Cygwin build: it derives HOME from the passwd database
    # when started with a Windows-format HOME and ignores such a path, so the
    # environment this test hands over is not proof the script used it. The
    # scripts are POSIX, so every HOME they touch is spelled the way that bash
    # spells it.
    m = re.search(r"^@@HOME=(.*)$", out, re.M)
    if m is not None:
        out = out[:m.start()] + out[m.end():]
        # A copy, not *env*: the caller's environment is the input to its own
        # later checks -- the launcher's interpreter is looked for in the same
        # PATH, and every run after this one has to start from the same state
        # or the test measures itself.
        env = dict(env)
        env["HOME"] = m.group(1).strip()
        # XDG_DATA_HOME wins over HOME in the install path, so it moves with
        # it. Everything the child inherits is POSIX-spelled, because a
        # Windows-style path passed to Git Bash is ignored, not translated.
        env["XDG_DATA_HOME"] = env["HOME"] + "/.local/share"
        env["TMPDIR"] = "/tmp"
    return proc.returncode, out, err


#: The bash the installer tests run. Absolute on Windows so the child sees the
#: environment this test hands it rather than the MSYS shell's idea of HOME.
BASH = shutil.which("bash") or "bash"


#: A two-line launcher shim: runs the interpreter it is given.
SHIM = "#!/bin/sh" + chr(10) + 'exec "{}" "$@"' + chr(10)


def link_python(target: str, link: Path) -> None:
    """Make *link* run *target*: a symlink, or a shell shim where that is not allowed.

    Creating a symlink on Windows needs a privilege an ordinary account does
    not have. A copy of python.exe is no use either -- it cannot start without
    its DLLs and Lib/ beside it -- so the fallback is a two-line sh script,
    which is all the launcher needs: it has to *run* the interpreter, not be
    one.
    """
    try:
        os.symlink(target, link)
        return
    except OSError:
        pass
    # Absolute: the launcher may be started from any directory, and a relative
    # target would then resolve against that directory instead of this one.
    link.write_text(SHIM.format(Path(target).resolve().as_posix()))
    os.chmod(link, 0o755)


def posix_home(path: Path | str) -> str:
    """*path* as Git Bash spells it (``C:/Users/x`` -> ``/c/Users/x``).

    The installer and launcher are POSIX scripts and the test runs them under
    Git Bash, which is a Cygwin build: given a Windows path in HOME or
    XDG_DATA_HOME it ignores the variable and falls back to the passwd entry,
    so a test that hands over the Windows spelling silently installs into the
    real home directory instead of its sandbox.
    """
    text = Path(path).as_posix()
    if sys.platform != "win32":
        return text
    m = re.match(r"^([A-Za-z]):(/.*)?$", text)
    if not m:
        return text
    return "/" + m.group(1).lower() + (m.group(2) or "")


def posix_path(*entries: Path | str) -> str:
    """PATH for a child bash, every entry in the spelling that bash can read.

    Not cosmetic. This test process is started from the MSYS shell, and a bash
    it spawns directly is then a Cygwin program whose startup converts the
    inherited PATH into the *Windows* spelling -- while the shell that actually
    runs the scripts is /bin/sh, which reads the variable as a POSIX list. One
    mixed value (``C:/.../stubs:/usr/bin:/bin``) is therefore read as a single
    Windows entry, no ``/usr/bin`` exists for ``sh`` to find, and every
    command the scripts run fails at once. Handing over the same entries
    POSIX-spelled removes the conversion from the picture: ``/c/...`` for a
    drive path, ``/usr/bin`` as it stands.
    """
    return ":".join(posix_home(entry) for entry in entries)


def base_env(home: Path) -> dict:
    """A clean environment: a temp HOME, no inherited venv, real PATH.

    XDG_DATA_HOME is pinned into the temp HOME too. install.sh honours it, so an
    operator who exports it (common on Arch) would otherwise send the test's
    clone into their real ~/.local/share — the test must not depend on the
    ambient environment to stay inside its sandbox.
    """
    env = dict(os.environ)
    env["HOME"] = posix_home(home)
    env["XDG_DATA_HOME"] = posix_home(home / ".local" / "share")
    for var in ("VIRTUAL_ENV", "LANTHORN_ROOT", "LANTHORN_INSTALL_DIR",
                "LANTHORN_BIN_DIR", "LANTHORN_SKIP_DEPS", "LANTHORN_SKIP_SETUP",
                # The launcher derives its own PYTHONPATH. An inherited one --
                # this checkout, or a stale path from an earlier install -- would
                # make the launcher look correct whatever it actually computed.
                "PYTHONPATH", "PYTHONHOME"):
        env.pop(var, None)
    if sys.platform == "win32":
        # Git Bash is a Cygwin build, and the shell it starts from matters:
        # spawned by the MSYS shell that launched this test, a Windows-spelled
        # HOME is discarded and the passwd entry (/root) used instead, taking
        # every path derived from HOME out of the sandbox. Spawned by a native
        # process it honours the variable, so on Windows the tests invoke bash
        # by its absolute path -- see BASH.
        env["USERPROFILE"] = str(home)
        env["HOMEDRIVE"] = home.drive or "C:"
        env["HOMEPATH"] = str(home)[2:] if home.drive else str(home)
    return env


def traced(env: dict) -> dict:
    """*env* with the launcher's re-exec switched off, so `bash -x` covers it.

    On Windows the launcher re-execs itself once under the Git Bash on PATH,
    and a trace of that first run stops at the exec: the second process is the
    one that resolves the interpreter, so the trace has to be of that one.
    """
    env = dict(env)
    env["LANTHORN_REEXEC"] = "1"
    return env


def _execs_to(trace: str, interpreter: Path) -> bool:
    """Does *trace* show the launcher exec'ing *interpreter* with -m lanthorn?

    Compared as whole words, because a test's install directory is shared
    between runs and one path is a prefix of another: .../byo must not match
    .../byo.old/. Bash quotes the path when it holds spaces, hence the quotes
    are stripped before splitting.
    """
    want = [posix_home(interpreter), "-m", "lanthorn"]
    for line in trace.splitlines():
        if not line.startswith("+ exec "):
            continue
        words = [w.strip("'\"") for w in line[len("+ exec "):].split()]
        if words[:3] == want:
            return True
    return False


#: A checkout's package directory, as the launcher now insists on finding it.
PKG_INIT = "__init__.py"


def fake_checkout(root: Path) -> None:
    """Give *root* the one thing that makes it a checkout: a lanthorn/ package.

    The launcher refuses a root without it -- the interpreter's own "No module
    named lanthorn" names the interpreter, which sends the reader to the wrong
    place -- so every fixture standing in for a checkout has to carry it.
    """
    pkg = root / "lanthorn"
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / PKG_INIT).write_text("__version__ = '0.0.0-test'\n")
    (pkg / "__main__.py").write_text("print('lanthorn-test')\n")


def make_source(td: Path) -> Path:
    """A minimal git repo to clone: the launcher, requirements, a setup stub."""
    src = td / "src"
    (src / "packaging").mkdir(parents=True)
    shutil.copy(ROOT / "packaging" / "lanthorn", src / "packaging" / "lanthorn")
    os.chmod(src / "packaging" / "lanthorn", 0o755)
    # Empty (comment-only) so the launcher's dependency sync, if it ever runs,
    # is a no-op rather than a network fetch.
    (src / "requirements.txt").write_text("# no deps in this test\n")
    fake_checkout(src)
    (src / "setup.sh").write_text("#!/usr/bin/env bash\necho setup-stub\n")
    os.chmod(src / "setup.sh", 0o755)

    ident = ["-c", "user.email=t@t", "-c", "user.name=t"]
    subprocess.run(["git", "init", "-q", "-b", "main", str(src)], check=True)
    subprocess.run(["git", *ident, "-C", str(src), "add", "-A"], check=True)
    subprocess.run(["git", *ident, "-C", str(src), "commit", "-qm", "seed"], check=True)
    return src


def head_of(repo: Path) -> str:
    """The commit *repo* is on, or "" if it is not a checkout."""
    proc = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                          capture_output=True, text=True)
    return proc.stdout.strip() if proc.returncode == 0 else ""


def rewrite_upstream(src: Path) -> str:
    """Replace *src*'s history with a fresh root commit and return its sha.

    The shape of a filtered or re-created upstream: the same files, no commit
    in common with anything cloned from the old history. The new commit adds
    ``lanthorn/effort.py``, standing in for a command shipped after the
    rewrite — the thing an old install would never see.
    """
    ident = ["-c", "user.email=t@t", "-c", "user.name=t"]
    subprocess.run(["git", "-C", str(src), "checkout", "-q", "--orphan", "rewritten"],
                   check=True)
    (src / "lanthorn").mkdir(exist_ok=True)
    (src / "lanthorn" / "effort.py").write_text("# shipped after the rewrite\n")
    subprocess.run(["git", *ident, "-C", str(src), "add", "-A"], check=True)
    subprocess.run(["git", *ident, "-C", str(src), "commit", "-qm", "rewritten"],
                   check=True)
    subprocess.run(["git", "-C", str(src), "branch", "-M", "rewritten", "main"],
                   check=True)
    return head_of(src)


# ---------------------------------------------------------------------------
# install.sh — the layout it lays down
# ---------------------------------------------------------------------------

def test_install_layout(td: Path) -> dict:
    home = td / "home"
    home.mkdir()
    src = make_source(td)
    env = base_env(home)
    env["LANTHORN_REPO"] = f"file://{src}"
    env["LANTHORN_SKIP_DEPS"] = "1"
    env["LANTHORN_SKIP_SETUP"] = "1"

    install_dir = home / ".local" / "share" / "lanthorn"
    bin_dir = home / ".local" / "bin"

    # Feed the script on stdin, exactly as `curl … | bash` would, to prove
    # nothing in it reads standard input. The first line is read by this bash,
    # not by the script, and reports the HOME it will use -- see run().
    rc, out, err = run([BASH], env=env, cwd=ROOT,
                       stdin=REPORT_HOME + (ROOT / "install.sh").read_text())
    # Paths the script laid down, spelled the way Windows Python can reach
    # them (the HOME bash reported is POSIX form, /c/Users/...).
    install_dir = home / ".local" / "share" / "lanthorn"
    bin_dir = home / ".local" / "bin"
    check("install: exits 0", rc == 0, err.strip()[-400:])
    check("install: clones the checkout", (install_dir / ".git").is_dir(),
          str(install_dir))
    check("install: lays the launcher on PATH",
          (bin_dir / "lanthorn").is_file(), str(bin_dir / "lanthorn"))
    check("install: the launcher is executable",
          os.access(bin_dir / "lanthorn", os.X_OK))

    shim = (bin_dir / "lanthorn").read_text()
    # The shim is written with %q, so the paths appear quoted the way the
    # script needs them; compare on the POSIX spelling the installer uses.
    posix_install = posix_home(install_dir)
    check("install: the shim pins LANTHORN_ROOT to the checkout",
          f'LANTHORN_ROOT="{posix_install}"' in shim
          or f"LANTHORN_ROOT={posix_install}" in shim, shim.strip())
    check("install: the shim execs the real launcher",
          f"{posix_install}/packaging/lanthorn" in shim, shim.strip())

    return {"home": home, "install_dir": install_dir, "bin_dir": bin_dir, "src": src}


def test_install_updates_a_rewritten_upstream(td: Path) -> None:
    """Re-running the installer onto a rewritten upstream must move the checkout.

    This is the failure the installer used to hide: a checkout cloned before the
    upstream history was rewritten shares no commit with it, so `pull --ff-only`
    aborts for ever. The old code warned, carried on, and printed "installed." —
    so an install stayed at the commit it was cloned at, missing every command
    shipped since, with no signal that the update had not happened.
    """
    base = td / "rewrite"
    base.mkdir()
    home = base / "home"
    home.mkdir()
    src = make_source(base)
    env = base_env(home)
    env["LANTHORN_REPO"] = f"file://{src}"
    env["LANTHORN_SKIP_DEPS"] = "1"
    env["LANTHORN_SKIP_SETUP"] = "1"
    install_dir = home / ".local" / "share" / "lanthorn"
    script = (ROOT / "install.sh").read_text()

    rc, out, err = run([BASH], env=env, cwd=ROOT, stdin=script)
    check("rewrite: the first install lands",
          rc == 0 and (install_dir / ".git").is_dir(), err.strip()[-300:])
    before = head_of(install_dir)

    want = rewrite_upstream(src)

    rc, out, err = run([BASH], env=env, cwd=ROOT, stdin=script)
    check("rewrite: re-running the installer exits 0", rc == 0, err.strip()[-400:])
    check("rewrite: the checkout moved onto the new history",
          head_of(install_dir) == want,
          f"{head_of(install_dir)[:8]} != {want[:8]}")
    check("rewrite: the module shipped after the rewrite is on disk",
          (install_dir / "lanthorn" / "effort.py").exists(),
          str(sorted(p.name for p in (install_dir / "lanthorn").iterdir()))
          if (install_dir / "lanthorn").is_dir() else "no package dir")
    check("rewrite: the abandoned head is kept in a ref",
          before in subprocess.run(
              ["git", "-C", str(install_dir), "for-each-ref",
               "--format=%(objectname)", "refs/lanthorn/"],
              capture_output=True, text=True).stdout)
    check("rewrite: the banner names the code it installed", want[:7] in out,
          out.strip()[-300:])


def test_install_leaves_local_edits_alone(td: Path) -> None:
    """A checkout with local edits is reported as not updated, and not touched."""
    base = td / "dirty"
    base.mkdir()
    home = base / "home"
    home.mkdir()
    src = make_source(base)
    env = base_env(home)
    env["LANTHORN_REPO"] = f"file://{src}"
    env["LANTHORN_SKIP_DEPS"] = "1"
    env["LANTHORN_SKIP_SETUP"] = "1"
    install_dir = home / ".local" / "share" / "lanthorn"
    script = (ROOT / "install.sh").read_text()

    rc, out, err = run([BASH], env=env, cwd=ROOT, stdin=script)
    check("dirty: the first install lands", rc == 0, err.strip()[-300:])
    before = head_of(install_dir)

    # An edit to a tracked file — work a reset would destroy.
    (install_dir / "requirements.txt").write_text("# no deps in this test\n# mine\n")
    rewrite_upstream(src)

    rc, out, err = run([BASH], env=env, cwd=ROOT, stdin=script)
    check("dirty: the installer still exits 0", rc == 0, err.strip()[-400:])
    check("dirty: HEAD is untouched", head_of(install_dir) == before)
    check("dirty: the edit survives",
          (install_dir / "requirements.txt").read_text().endswith("# mine\n"))
    check("dirty: the run says the install was not updated",
          "NOT updated" in out + err, (out + err).strip()[-400:])


# ---------------------------------------------------------------------------
# The launcher — which interpreter it picks
# ---------------------------------------------------------------------------

def fake_bundled_venv(install_dir: Path) -> Path:
    """A stand-in venv: a python that exists, plus a stamp so sync_deps no-ops.

    The python is the real system interpreter, so the dependency import check
    fails as it would on a fresh machine — which is the point: the failure
    message names the interpreter that was chosen.
    """
    venv_bin = install_dir / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    # The interpreter this test runs under: on Windows "python3" on PATH is the
    # Microsoft Store stub, which exits 9009 without running anything.
    real = sys.executable
    link_python(real, venv_bin / "python")

    # The stamp sync_deps compares against. Written the way the launcher reads
    # it, without a trailing newline: the comparison is exact, and a "\\n" here
    # makes every launch look stale and try to pip-install.
    (install_dir / ".venv" / ".requirements.sha").write_text(
        hashlib.sha256((install_dir / "requirements.txt").read_bytes()).hexdigest())
    return venv_bin / "python"


def test_launcher_prefers_bundled_venv(ctx: dict) -> None:
    install_dir, bin_dir = ctx["install_dir"], ctx["bin_dir"]
    launcher = install_dir / "packaging" / "lanthorn"
    if not launcher.exists():
        # A failed install should read as a failed check, not a traceback.
        check("launcher: the checkout was installed", False, str(launcher))
        return

    venv_py = fake_bundled_venv(install_dir)
    env = base_env(ctx["home"])

    # Through the installed shim — the way a user runs it.
    # Through bash: a shell script is not an executable image on Windows, so
    # the kernel cannot start it directly and Windows refuses with WinError 193.
    #
    # The venv is a shim over the real interpreter, so the dependency probe
    # passes and the launcher goes on to exec it. Success is therefore read from
    # the launcher's own trace: the interpreter it resolved, and the command it
    # handed that interpreter.
    rc, out, err = run([BASH, str(bin_dir / "lanthorn"), "--version"],
                       env=env, cwd=ctx["home"])
    check("launcher: the shim starts the harness (exit 0)", rc == 0,
          err.strip()[-300:])

    # The launcher itself, traced, with the shim's LANTHORN_ROOT carried over:
    # the shim only exports the root and hands over, so the decision worth
    # checking -- which interpreter runs -- is made by the launcher.
    shim_env = traced(env)
    shim_env["LANTHORN_ROOT"] = posix_home(install_dir)
    rc, out, err = run([BASH, "-x", str(launcher), "--version"],
                       env=shim_env, cwd=ctx["home"])
    trace = err + out
    check("launcher: the shim resolves the checkout's venv",
          _execs_to(trace, Path(posix_home(venv_py))),
          trace.strip()[-300:])

    # The launcher directly, with no LANTHORN_ROOT and from an unrelated cwd —
    # it must derive the checkout as the parent of packaging/ on its own.
    derive_env = traced(env)
    derive_env.pop("LANTHORN_ROOT", None)
    # The install directory is inside HOME, and the launcher may reach it by
    # either spelling (/tmp/x on the shell's scratch drive, /c/... from the
    # test's tempdir); both name the same files, so compare as POSIX spellings.
    expected = Path(posix_home(venv_py))
    rc, out, err = run([BASH, "-x", str(launcher), "--version"],
                       env=derive_env, cwd="/")
    trace = err + out
    # The root is derived with `pwd -P`, so the launcher sees the shell's own
    # spelling of the install directory, not the one this test handed it.
    check("launcher: derives the checkout from its own location",
          any(line.startswith("+ exec ") and "/.venv/bin/python -m lanthorn" in line
              for line in trace.splitlines()),
          trace.strip()[-300:])


def test_launcher_starts_without_a_key_or_a_proxy(td: Path) -> None:
    """An empty key must not stop the harness, and no proxy may be started.

    This is the fresh-install state: the profile exists but carries no token,
    because no key ships. The launcher used to fail closed on the missing
    credential, which left the operator unable to run the very command that sets
    one. It must exec the harness instead — and it must not start the retired
    :4001 proxy on the way.

    The profile is also the state an install updated by the launch-time
    self-update carries: setup.sh never ran, so it still names the proxy that
    is no longer installed. The launch has to move it to the direct route (and
    write that back) or the session comes up on an address nothing answers —
    which is what made an updated install look like it had lost commands.
    """
    root = td / "launcher-nokey"
    (root / "packaging").mkdir(parents=True)
    shutil.copy(ROOT / "packaging" / "lanthorn", root / "packaging" / "lanthorn")
    os.chmod(root / "packaging" / "lanthorn", 0o755)
    (root / "requirements.txt").write_text("# no deps in this test\n")
    fake_checkout(root)

    venv_bin = root / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    args_log = td / "launcher-args.log"
    env_log = td / "launcher-env.log"
    proxy_marker = td / "proxy-started"
    py = venv_bin / "python"
    py.write_text(
        "#!/bin/sh\n"
        # The launcher's dependency probe is the only -c it runs.
        'if [ "$1" = "-c" ]; then exit 0; fi\n'
        f'printf "%s\\n" "$@" >> "{args_log}"\n'
        f'env | sort > "{env_log}"\n'
        "exit 0\n"
    )
    os.chmod(py, 0o755)
    proc = subprocess.run(["sha256sum", str(root / "requirements.txt")],
                          capture_output=True, text=True)
    if proc.returncode == 0:
        (root / ".venv" / ".requirements.sha").write_text(proc.stdout.split()[0])

    home = td / "launcher-nokey-home"
    home.mkdir()
    zhome = home / ".lanthorn"
    (zhome / "logfare").mkdir(parents=True)
    (zhome / "logfare" / "source").write_text(
        'export ANTHROPIC_BASE_URL="http://localhost:4001"\n'
        'export ANTHROPIC_AUTH_TOKEN=""\n'
        'export ANTHROPIC_MODEL="deepseek-v4.1-flash"\n'
        'export ANTHROPIC_API_KEY=""\n'
    )
    os.chmod(zhome / "logfare" / "source", 0o600)
    service = zhome / "logfare" / "start-litellm.sh"
    service.write_text(f'#!/bin/sh\necho started > "{proxy_marker}"\n')
    os.chmod(service, 0o755)

    env = base_env(home)
    env["LANTHORN_HOME"] = posix_home(zhome)
    for var in ("ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY",
                "LANTHORN_NO_PROXY", "LANTHORN_REQUIRE_PROXY", "LANTHORN_WITH_PROXY"):
        env.pop(var, None)

    rc, out, err = run([BASH, str(root / "packaging" / "lanthorn"), "--version"],
                       env=env, cwd="/")
    check("launcher/nokey: it reaches the harness (exit 0)", rc == 0,
          err.strip()[-400:])
    check("launcher/nokey: the harness was exec'd",
          args_log.is_file() and "-m\nlanthorn\n--version" in args_log.read_text(),
          args_log.read_text() if args_log.is_file() else "no args log")
    check("launcher/nokey: no proxy was started", not proxy_marker.exists(),
          str(proxy_marker))
    env_text = env_log.read_text() if env_log.is_file() else ""
    check("launcher/nokey: the old :4001 profile is moved to the direct route",
          "ANTHROPIC_BASE_URL=https://logfare.ai/v1" in env_text
          and "ANTHROPIC_BASE_URL=http://localhost:4001" not in env_text,
          env_text[-300:])
    profile = zhome / "logfare" / "source"
    check("launcher/nokey: the migration is written back to the profile",
          'ANTHROPIC_BASE_URL="https://logfare.ai/v1"' in profile.read_text(),
          profile.read_text())
    check("launcher/nokey: the previous profile is kept as a backup",
          (zhome / "logfare" / "source.bak").is_file()
          and 'http://localhost:4001' in (zhome / "logfare" / "source.bak").read_text())

    # An explicit proxy request is honoured: the profile is left alone and the
    # service manager is what the launch asks for.
    (zhome / "logfare" / "source").write_text(
        'export ANTHROPIC_BASE_URL="http://localhost:4001"\n'
        'export ANTHROPIC_AUTH_TOKEN=""\n'
    )
    proxy_marker.unlink(missing_ok=True)
    env_proxy = dict(env)
    env_proxy["LANTHORN_NO_PROXY"] = "0"
    env_log.unlink(missing_ok=True)
    rc, out, err = run([BASH, str(root / "packaging" / "lanthorn"), "--version"],
                       env=env_proxy, cwd="/")
    check("launcher/proxy: an explicit proxy request still starts the proxy",
          rc == 0 and proxy_marker.is_file(), err.strip()[-400:])
    env_text = env_log.read_text() if env_log.is_file() else ""
    check("launcher/proxy: the :4001 profile is left as it was",
          "ANTHROPIC_BASE_URL=http://localhost:4001" in env_text,
          env_text[-300:])
    check("launcher/proxy: the profile on disk is untouched",
          'http://localhost:4001' in (zhome / "logfare" / "source").read_text())

    # No profile at all: still start, on the direct default.
    home2 = td / "launcher-noprofile-home"
    home2.mkdir()
    zhome2 = home2 / ".lanthorn"
    zhome2.mkdir()
    env2 = base_env(home2)
    env2["LANTHORN_HOME"] = posix_home(zhome2)
    for var in ("ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY",
                "LANTHORN_NO_PROXY", "LANTHORN_REQUIRE_PROXY", "LANTHORN_WITH_PROXY"):
        env2.pop(var, None)
    env_log.unlink(missing_ok=True)
    rc, out, err = run([BASH, str(root / "packaging" / "lanthorn"), "--version"],
                       env=env2, cwd="/")
    check("launcher/noprofile: it still reaches the harness", rc == 0,
          err.strip()[-400:])
    env_text = env_log.read_text() if env_log.is_file() else ""
    check("launcher/noprofile: it starts on the direct Logfare route",
          "ANTHROPIC_BASE_URL=https://logfare.ai/v1" in env_text,
          env_text[-300:])


def test_launcher_falls_back_to_path_python(td: Path) -> None:
    # A checkout with no bundled venv: the bring-your-own-venv layout. With no
    # VIRTUAL_ENV either, it must fall back to python3 on PATH.
    root = td / "byo"
    (root / "packaging").mkdir(parents=True)
    shutil.copy(ROOT / "packaging" / "lanthorn", root / "packaging" / "lanthorn")
    os.chmod(root / "packaging" / "lanthorn", 0o755)
    fake_checkout(root)

    home = td / "home2"
    home.mkdir()
    env = base_env(home)
    rc, out, err = run([BASH, "-x", str(root / "packaging" / "lanthorn"), "--version"],
                       env=traced(env), cwd="/")
    trace = err + out
    # The first PATH candidate that runs: python3 where it is a real
    # interpreter, `python` where python3 is the Windows Store stub. The
    # launcher resolves it with `command -v`, so the trace names the POSIX
    # spelling of whichever one won.
    candidates = [Path(posix_home(c)).with_suffix("").as_posix() for c in
                  (shutil.which("python3"), shutil.which("python")) if c]
    execs = [line for line in trace.splitlines()
             if line.startswith("+ exec ") and "-m lanthorn" in line]
    check("launcher: falls back to python3 on PATH",
          bool(execs) and any(c in execs[-1] for c in candidates),
          repr(execs[-1]) + " vs " + repr(candidates) if execs
          else trace.strip()[-300:])


def test_setup_installs_without_a_key_and_without_prompting(td: Path) -> None:
    """A fresh install must neither stop to ask for a key nor invent one.

    The install path is `curl … | bash` and then `lanthorn`, so a prompt for a
    third-party signup in the middle of that is friction worth avoiding. But the
    other half matters just as much: no credential ships in the tree, because a
    key committed to a public repository is spendable by anyone and cannot be
    unpublished. setup.sh therefore installs the route, writes a profile with no
    token in it, and says where a key goes — with no terminal attached.

    Run in a throwaway HOME with stdin closed. Every network step is stubbed
    (the proxy is never started, nothing is fetched): what is asserted is the
    credential decision, not a route.
    """
    home = td / "home-setup"
    home.mkdir()
    zhome = home / ".lanthorn"
    checkout = td / "checkout"
    (checkout / "packaging" / "logfare").mkdir(parents=True)
    # The shipped profile, as it lands in a clone: the real template, placeholder
    # and all. Copying it rather than inventing one means this test fails if a
    # live key is ever committed to that path again.
    shutil.copy(ROOT / "packaging" / "logfare" / "source",
                checkout / "packaging" / "logfare" / "source")
    shutil.copy(ROOT / "setup.sh", checkout / "setup.sh")
    os.chmod(checkout / "setup.sh", 0o755)
    # setup.sh refuses to configure a proxy for an interpreter that cannot run
    # the harness, so the checkout needs a venv whose python really has the
    # dependencies. A shim that execs the interpreter running this test does
    # that without building a venv or touching the network. A symlink will not
    # do: a bare symlink has no pyvenv.cfg, so sys.prefix stays /usr and the
    # dependencies are not on the path.
    venv_bin = checkout / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    shim = venv_bin / "python"
    shim.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n')
    os.chmod(shim, 0o755)

    env = base_env(home)
    env["LANTHORN_HOME"] = posix_home(zhome)
    env["LANTHORN_ROOT"] = posix_home(checkout)
    env.pop("ANTHROPIC_AUTH_TOKEN", None)
    env.pop("ANTHROPIC_API_KEY", None)
    # Hermetic: no network, and nothing slow. A `curl` that always fails stops
    # the pagekite download and makes the route probe read as "nothing
    # answering" — both are warnings, so the script still exits 0. No `litellm`
    # stub is needed: the direct route never looks for one, and if it did the
    # command would fail loudly here.
    stubs = td / "stubs"
    stubs.mkdir()
    (stubs / "curl").write_text("#!/bin/sh\nexit 1\n")
    for stub in stubs.iterdir():
        os.chmod(stub, 0o755)
    env["PATH"] = posix_path(stubs, "/usr/bin", "/bin")

    proc = subprocess.run(
        [BASH, str(checkout / "setup.sh")],
        cwd=str(checkout), env=env,
        stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=120,
    )
    combined = proc.stdout + proc.stderr
    check("setup: finishes with no terminal and no prompt", proc.returncode == 0,
          combined.strip()[-400:])
    check("setup: never asks for a key",
          "Paste your" not in combined and "key:" not in combined,
          combined.strip()[-400:])

    profile = zhome / "logfare" / "source"
    text = profile.read_text() if profile.is_file() else ""
    check("setup: writes a profile so the launcher has one", profile.is_file(),
          str(profile))
    check("setup: the profile carries no key", 'ANTHROPIC_AUTH_TOKEN=""' in text,
          text[:200])
    check("setup: the profile points at the direct upstream",
          'ANTHROPIC_BASE_URL="https://logfare.ai/v1"' in text, text[:200])
    check("setup: no placeholder is copied into the profile",
          "REPLACE_ME" not in text, text[:200])
    check("setup: it says where a key goes",
          "/logfare-api-setup" in combined and "logfare.ai" in combined,
          combined.strip()[-400:])
    check("setup: no proxy is installed or started",
          "Starting the proxy" not in combined
          and "Checking the LiteLLM proxy" not in combined,
          combined.strip()[-400:])
    check("setup: no Logfare proxy files are laid down",
          not (zhome / "logfare" / "start-litellm.sh").exists()
          and not (zhome / "logfare" / "litellm-config.yaml").exists(),
          str(sorted(p.name for p in (zhome / "logfare").iterdir()))
          if (zhome / "logfare").is_dir() else "no logfare dir")
    ok600, detail600 = (private_file_ok(profile) if profile.is_file()
                        else (False, "missing"))
    check("setup: the profile is mode 600", ok600, detail600)

    # A personal credential in the environment is still picked up.
    personal = td / "home-setup2"
    personal.mkdir()
    env2 = base_env(personal)
    env2["LANTHORN_HOME"] = posix_home(personal / ".lanthorn")
    env2["LANTHORN_ROOT"] = posix_home(checkout)
    env2["ANTHROPIC_AUTH_TOKEN"] = "lfu_personal_key_should_win"
    env2["PATH"] = env["PATH"]
    subprocess.run([BASH, str(checkout / "setup.sh")],
                   cwd=str(checkout), env=env2,
                   stdin=subprocess.DEVNULL, capture_output=True, text=True,
                   timeout=120)
    written = (personal / ".lanthorn" / "logfare" / "source")
    check("setup: a personal key in the environment is written to the profile",
          written.is_file() and "lfu_personal_key_should_win" in written.read_text(),
          written.read_text()[:120] if written.is_file() else "missing")


def test_setup_migrates_logfare_to_direct(td: Path) -> None:
    """An older Logfare profile is moved off :4001, key intact, no proxy.

    The route used to be a LiteLLM proxy on :4001. It is not any more — the
    upstream serves Chat Completions itself — so a profile left pointing at that
    port would aim the session at an address nothing answers. setup.sh rewrites
    the address, keeps the token byte for byte, and removes the retired proxy
    files rather than leaving a manager nothing will start.
    """
    home = td / "home-logfare"
    home.mkdir()
    zhome = home / ".lanthorn"
    checkout = td / "checkout-logfare"
    (checkout / "packaging" / "logfare").mkdir(parents=True)
    shutil.copy(ROOT / "packaging" / "logfare" / "source",
                checkout / "packaging" / "logfare" / "source")
    shutil.copy(ROOT / "setup.sh", checkout / "setup.sh")
    os.chmod(checkout / "setup.sh", 0o755)

    venv_bin = checkout / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    shim = venv_bin / "python"
    shim.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n')
    os.chmod(shim, 0o755)

    # The install as it was: a :4001 profile, plus the proxy it started.
    lf = zhome / "logfare"
    lf.mkdir(parents=True)
    old = (
        'export ANTHROPIC_BASE_URL="http://localhost:4001"\n'
        'export ANTHROPIC_AUTH_TOKEN="lfu_test_key_1234567890"\n'
        'export ANTHROPIC_MODEL="deepseek-v4.1-flash"\n'
        'export ANTHROPIC_API_KEY=""\n'
        'export MODEL_QWEN="qwen-3.8-27b"\n'
    )
    (lf / "source").write_text(old)
    os.chmod(lf / "source", 0o600)
    (lf / "start-litellm.sh").write_text("#!/bin/sh\n")
    (lf / "litellm-config.yaml").write_text("model_list: []\n")

    env = base_env(home)
    env["LANTHORN_HOME"] = posix_home(zhome)
    env["LANTHORN_ROOT"] = posix_home(checkout)
    env.pop("ANTHROPIC_AUTH_TOKEN", None)
    env.pop("ANTHROPIC_API_KEY", None)
    stubs = td / "stubs-logfare"
    stubs.mkdir()
    (stubs / "curl").write_text("#!/bin/sh\nexit 1\n")
    for stub in stubs.iterdir():
        os.chmod(stub, 0o755)
    env["PATH"] = posix_path(stubs, "/usr/bin", "/bin")

    proc = subprocess.run(
        [BASH, str(checkout / "setup.sh")],
        cwd=str(checkout), env=env,
        stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=120,
    )
    check("setup/logfare: finishes", proc.returncode == 0,
          (proc.stdout + proc.stderr).strip()[-400:])

    text = (lf / "source").read_text() if (lf / "source").is_file() else ""
    check("setup/logfare: the profile now names the upstream",
          'ANTHROPIC_BASE_URL="https://logfare.ai/v1"' in text, text[:200])
    check("setup/logfare: the key is untouched",
          'ANTHROPIC_AUTH_TOKEN="lfu_test_key_1234567890"' in text, text[:200])
    check("setup/logfare: the model aliases are kept",
          'MODEL_QWEN="qwen-3.8-27b"' in text, text[:200])
    ok_lf, detail_lf = private_file_ok(lf / "source")
    check("setup/logfare: the profile stays mode 600", ok_lf, detail_lf)
    check("setup/logfare: the previous profile is kept as a backup",
          (lf / "source.bak").is_file(), str(sorted(p.name for p in lf.iterdir())))
    check("setup/logfare: the retired proxy files are gone",
          not (lf / "start-litellm.sh").exists()
          and not (lf / "litellm-config.yaml").exists(),
          str(sorted(p.name for p in lf.iterdir())))
    check("setup/logfare: no proxy is started",
          "Starting the proxy" not in proc.stdout + proc.stderr,
          (proc.stdout + proc.stderr).strip()[-400:])

    # A second run is a no-op: the profile is already direct.
    proc2 = subprocess.run(
        [BASH, str(checkout / "setup.sh")],
        cwd=str(checkout), env=env,
        stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=120,
    )
    check("setup/logfare: a second run leaves the direct profile alone",
          "already on the direct route" in proc2.stdout + proc2.stderr,
          (proc2.stdout + proc2.stderr).strip()[-400:])


def test_setup_migrates_tokenjuice_to_direct(td: Path) -> None:
    """An older Token Juice profile is moved onto the direct route, key intact.

    The route had a LiteLLM proxy on :4000. It does not any more — the upstream
    serves Chat Completions itself — so a profile left pointing at that port
    would aim the session at an address nothing answers. setup.sh rewrites the
    address and the model id, keeps the key byte for byte, and removes the
    retired proxy files rather than leaving a manager nothing will start.

    Shell-side, so it is run against a throwaway checkout and HOME.
    """
    home = td / "home-tokenjuice"
    home.mkdir()
    zhome = home / ".lanthorn"
    checkout = td / "checkout-tokenjuice"
    (checkout / "packaging" / "logfare").mkdir(parents=True)
    (checkout / "packaging" / "tokenjuice").mkdir(parents=True)
    shutil.copy(ROOT / "packaging" / "logfare" / "source",
                checkout / "packaging" / "logfare" / "source")
    shutil.copy(ROOT / "packaging" / "tokenjuice" / "source.example",
                checkout / "packaging" / "tokenjuice" / "source.example")
    shutil.copy(ROOT / "setup.sh", checkout / "setup.sh")
    os.chmod(checkout / "setup.sh", 0o755)

    venv_bin = checkout / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    shim = venv_bin / "python"
    shim.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n')
    os.chmod(shim, 0o755)

    # The install as it was: a :4000 profile, plus the proxy it started.
    tj = zhome / "tokenjuice"
    tj.mkdir(parents=True)
    old = (
        'export ANTHROPIC_BASE_URL="http://localhost:4000"\n'
        'export ANTHROPIC_AUTH_TOKEN="tj_test_key_123"\n'
        'export ANTHROPIC_MODEL="deepseek-v4.1-flash"\n'
        'export ANTHROPIC_API_KEY=""\n'
    )
    (tj / "source").write_text(old)
    os.chmod(tj / "source", 0o600)
    (tj / "start-litellm.sh").write_text("#!/bin/sh\n")
    (tj / "litellm-config.yaml").write_text("model_list: []\n")

    env = base_env(home)
    env["LANTHORN_HOME"] = posix_home(zhome)
    env["LANTHORN_ROOT"] = posix_home(checkout)
    env.pop("ANTHROPIC_AUTH_TOKEN", None)
    env.pop("ANTHROPIC_API_KEY", None)
    stubs = td / "stubs-tokenjuice"
    stubs.mkdir()
    (stubs / "litellm").write_text("#!/bin/sh\nexit 0\n")
    (stubs / "curl").write_text("#!/bin/sh\nexit 1\n")
    for stub in stubs.iterdir():
        os.chmod(stub, 0o755)
    env["PATH"] = posix_path(stubs, "/usr/bin", "/bin")

    proc = subprocess.run(
        [BASH, str(checkout / "setup.sh")],
        cwd=str(checkout), env=env,
        stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=120,
    )
    check("setup/tokenjuice: finishes", proc.returncode == 0,
          (proc.stdout + proc.stderr).strip()[-400:])

    text = (tj / "source").read_text() if (tj / "source").is_file() else ""
    check("setup/tokenjuice: the profile now names the upstream",
          'ANTHROPIC_BASE_URL="https://api.tokenjuice.ai/v1"' in text, text[:200])
    check("setup/tokenjuice: and the model the upstream serves",
          'ANTHROPIC_MODEL="deepseek-ai/DeepSeek-V4.1-Flash-1017"' in text,
          text[:200])
    check("setup/tokenjuice: the key is untouched",
          'ANTHROPIC_AUTH_TOKEN="tj_test_key_123"' in text, text[:200])
    ok_tj, detail_tj = private_file_ok(tj / "source")
    check("setup/tokenjuice: the profile stays mode 600", ok_tj, detail_tj)
    check("setup/tokenjuice: the previous profile is kept as a backup",
          (tj / "source.bak").is_file(), str(sorted(p.name for p in tj.iterdir())))
    check("setup/tokenjuice: the retired proxy files are gone",
          not (tj / "start-litellm.sh").exists()
          and not (tj / "litellm-config.yaml").exists(),
          str(sorted(p.name for p in tj.iterdir())))
    check("setup/tokenjuice: the template is refreshed",
          (tj / "source.example").is_file()
          and "api.tokenjuice.ai" in (tj / "source.example").read_text())


def main() -> int:
    with tempfile.TemporaryDirectory() as td_str:
        td = Path(td_str)
        ctx = test_install_layout(td)
        test_install_updates_a_rewritten_upstream(td)
        test_install_leaves_local_edits_alone(td)
        test_launcher_prefers_bundled_venv(ctx)
        test_launcher_starts_without_a_key_or_a_proxy(td)
        test_launcher_falls_back_to_path_python(td)
        test_setup_installs_without_a_key_and_without_prompting(td)
        test_setup_migrates_logfare_to_direct(td)
        test_setup_migrates_tokenjuice_to_direct(td)

    failed = [n for n, ok, _ in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} passed")
    for name, ok, extra in RESULTS:
        print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  {extra}" if not ok and extra else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
