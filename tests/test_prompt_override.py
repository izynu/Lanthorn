"""The operator's own system prompt replaces the mode's doctrine.

The feature the operator asked for in place of a censored built-in persona:
pick the model, write the prompt. This checks the plumbing end to end without
a network — storage round-trips, the env var wins, a blank file means "no
override", and the assembled request really carries the operator's text in
place of the mode's refusal doctrine.
"""

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lanthorn import config as config_mod  # noqa: E402
from lanthorn.agent import Agent  # noqa: E402

PASS = 0
FAIL = 0


def check(name, ok, extra=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"PASS  {name}")
    else:
        FAIL += 1
        print(f"FAIL  {name}  {extra}")


def main():
    home = Path(tempfile.mkdtemp(prefix="zz-prompt-"))
    path = home / config_mod.SYSTEM_PROMPT_FILE

    # --- storage -----------------------------------------------------------
    check("no file yet -> no override", config_mod.read_system_prompt(home) is None)
    written = config_mod.write_system_prompt("You are terse.", home)
    check("write returns the path", written == path, str(written))
    check("write lands the file", path.is_file())
    check("read gives the text back",
          config_mod.read_system_prompt(home) == "You are terse.")
    check("file ends with one newline", path.read_text(encoding="utf-8") == "You are terse.\n")

    if os.name != "nt":
        mode = path.stat().st_mode & 0o777
        check("file is private (0600)", mode == 0o600, oct(mode))

    config_mod.write_system_prompt("   \n\t ", home)
    check("blank write clears it", not path.exists())
    check("cleared read is None", config_mod.read_system_prompt(home) is None)
    check("clearing twice is fine", config_mod.write_system_prompt("", home) is None)

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n\n   \n", encoding="utf-8")
    check("a whitespace-only file is no override",
          config_mod.read_system_prompt(home) is None)
    path.write_text("from disk\nsecond line", encoding="utf-8")
    check("multi-line file reads intact",
          config_mod.read_system_prompt(home) == "from disk\nsecond line")

    # --- config wiring -----------------------------------------------------
    env_keys = ("LANTHORN_SYSTEM_PROMPT", "LANTHORN_HOME", "LANTHORN_MODEL",
                "LANTHORN_PROVIDER", "OPENAI_API_KEY", "ANTHROPIC_API_KEY",
                "GEMINI_API_KEY", "DEEPSEEK_API_KEY", "OPENROUTER_API_KEY")
    saved = {k: os.environ.get(k) for k in env_keys}
    try:
        for k in env_keys:
            os.environ.pop(k, None)
        os.environ["LANTHORN_HOME"] = str(home)
        # DEFAULT_HOME is read at import; point it at this run's home too, or
        # the test would read the operator's real ~/.lanthorn.
        saved_home = config_mod.DEFAULT_HOME
        config_mod.DEFAULT_HOME = home

        cfg = config_mod.Config.from_env()
        check("config reads the file at launch",
              cfg.system_prompt == "from disk\nsecond line", repr(cfg.system_prompt))

        os.environ["LANTHORN_SYSTEM_PROMPT"] = "from the environment"
        cfg = config_mod.Config.from_env()
        check("the env var wins over the file",
              cfg.system_prompt == "from the environment", repr(cfg.system_prompt))

        os.environ["LANTHORN_SYSTEM_PROMPT"] = "   "
        cfg = config_mod.Config.from_env()
        check("a blank env var falls back to the file",
              cfg.system_prompt == "from disk\nsecond line", repr(cfg.system_prompt))
        config_mod.write_system_prompt("", home)
        cfg = config_mod.Config.from_env()
        check("a blank env var with no file is no override",
              cfg.system_prompt is None, repr(cfg.system_prompt))
    finally:
        config_mod.DEFAULT_HOME = saved_home
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    # --- what the model is actually told -----------------------------------
    work = Path(tempfile.mkdtemp(prefix="zz-work-"))
    try:
        cfg = config_mod.Config(workdir=work, mode="uncensored", state_dir=home)
        agent = Agent(cfg)
        plain = agent._base_system_prompt()

        cfg.system_prompt = "OPERATOR DOCTRINE: answer plainly."
        over = agent._base_system_prompt()
        check("the operator's text is in the prompt", "OPERATOR DOCTRINE" in over)
        check("the mode's doctrine is gone",
              "OPERATOR DOCTRINE" in over and plain not in over)
        check("the working directory is still named", str(work) in over)
        check("the override leads", over.startswith("OPERATOR DOCTRINE"))

        # Flip it back without rebuilding the agent — /prompt clear must work
        # mid-session, and the agent reads cfg on every request.
        cfg.system_prompt = None
        check("clearing restores the mode's prompt",
              agent._base_system_prompt() == plain)

        # The override is text, not code: braces and backslashes survive.
        tricky = "Use {braces} and C:\\path\\x and $HOME."
        cfg.system_prompt = tricky
        check("braces and backslashes survive verbatim",
              tricky in agent._base_system_prompt())
    finally:
        cfg.system_prompt = None

    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
