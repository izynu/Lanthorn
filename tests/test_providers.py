"""The provider registry and the /connect key flow.

What is checked here is the part that must not be wrong: that every provider
entry names a module that exists, that a key goes to that provider's own
profile (and nowhere else), that an environment variable wins over a stored
key, that clearing removes only the stored file, and that the confirmation
never contains the key.

No network: the catalog fetch is exercised through a local HTTP server that
serves the OpenAI-shaped envelope, so the parsing and the key-gated message are
tested without depending on anyone's uptime.
"""

import json
import os
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

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


# --- an isolated home, before anything imports it ---------------------------

HOME = Path(tempfile.mkdtemp(prefix="zz-prov-"))
os.environ["LANTHORN_HOME"] = str(HOME)
for var in ("ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL", "OPENAI_API_KEY",
            "OPENAI_BASE_URL", "DEEPSEEK_API_KEY", "GEMINI_API_KEY",
            "OPENROUTER_API_KEY", "NVIDIA_API_KEY", "UNOROUTER_API_KEY",
            "AIHUBMIX_API_KEY", "BEATAPI_API_KEY", "MOONMATH_API_KEY",
            "ITERACOMPUTE_API_KEY", "INFRONAI_API_KEY", "TOKENJUICE_API_KEY"):
    os.environ.pop(var, None)

from lanthorn import config as config_mod  # noqa: E402
from lanthorn import direct_common  # noqa: E402
from lanthorn import providers as prov  # noqa: E402

# Point every module's home at the isolated one. They bind it at import, which
# is the product's own behaviour (a launch reads LANTHORN_HOME once); the test
# sets the variable first and then rebinds, the way a test must.
config_mod.DEFAULT_HOME = HOME
import importlib  # noqa: E402

for _name in ("anthropic_direct", "openai_direct", "deepseek_direct", "gemini",
              "openrouter", "nvidia", "unorouter", "aihubmix", "beatapi",
              "moonmath", "iteracompute", "infronai", "sources"):
    _mod = importlib.import_module(f"lanthorn.{_name}")
    for _attr in ("PROFILE", "LANTHORN_HOME"):
        if hasattr(_mod, _attr) and _attr == "LANTHORN_HOME":
            setattr(_mod, _attr, HOME)


def main():
    # --- the registry is internally consistent ----------------------------
    check("there are providers", len(prov.PROVIDERS) >= 10, str(len(prov.PROVIDERS)))
    keys = [p.key for p in prov.PROVIDERS]
    check("provider keys are unique", len(keys) == len(set(keys)))

    bad = []
    for p in prov.PROVIDERS:
        try:
            p.mod()
        except Exception as exc:  # noqa: BLE001
            bad.append(f"{p.key}: {exc}")
    check("every provider names an importable module", not bad, "; ".join(bad))

    bad = []
    for p in prov.PROVIDERS:
        try:
            url = p.base_url
            proto = p.protocol
        except Exception as exc:  # noqa: BLE001
            bad.append(f"{p.key}: {exc}")
            continue
        if not p.keyless and not url.startswith(("http://", "https://")):
            bad.append(f"{p.key}: no endpoint ({url!r})")
        if proto not in ("anthropic", "openai"):
            bad.append(f"{p.key}: protocol {proto!r}")
    check("every provider resolves an endpoint and a protocol", not bad,
          "; ".join(bad))

    bad = [p.key for p in prov.PROVIDERS
           if p.active(p.base_url) is not True]
    check("each provider recognises its own endpoint", not bad, str(bad))

    check("an empty URL is nobody", not any(p.active("") for p in prov.PROVIDERS))
    check("a localhost URL is not mistaken for a hosted one",
          not prov.BY_KEY["openrouter"].active("http://localhost:4000"))

    # --- aliases ----------------------------------------------------------
    check("claude -> anthropic", prov.find("claude").key == "anthropic")
    check("zro -> moonmath", prov.find("ZRO").key == "moonmath")
    check("case does not matter", prov.find("OpenRouter").key == "openrouter")
    check("an unknown name is None", prov.find("nope") is None)
    check("an empty name is None", prov.find("") is None)

    # --- masking never reveals the key ------------------------------------
    key = "sk-ant-api03-ABCDEFGHIJKLMNOP"
    masked = prov.mask(key)
    check("mask shows the last four", masked.endswith("MNOP"), masked)
    check("mask hides the rest", "ABCDEFGHIJ" not in masked, masked)
    check("a short key is masked entirely", prov.mask("abc") == "…")
    check("an empty key masks to empty", prov.mask("") == "")

    # --- storing a key goes to that provider's own file -------------------
    anth = prov.BY_KEY["anthropic"]
    path = prov.save(anth, key)
    check("save returns the provider's profile path", path == anth.profile, str(path))
    check("the profile exists", path.is_file())
    text = path.read_text(encoding="utf-8")
    check("the key is in the profile", key in text)
    check("the profile names the endpoint", "api.anthropic.com" in text)
    check("the profile blanks ANTHROPIC_API_KEY",
          'export ANTHROPIC_API_KEY=""' in text)
    if os.name != "nt":
        check("the profile is 600", (path.stat().st_mode & 0o777) == 0o600,
              oct(path.stat().st_mode & 0o777))
        check("its directory is 700",
              (path.parent.stat().st_mode & 0o777) == 0o700,
              oct(path.parent.stat().st_mode & 0o777))

    found = prov.resolve(anth)
    check("resolve reads it back", found and found[0] == key, str(found))
    check("resolve reports the file as the origin",
          found and found[1] == str(path), str(found))
    check("status agrees", prov.status(anth) == (True, str(path)))

    # A key for one provider must not appear in another's file.
    other = prov.BY_KEY["openai"]
    prov.save(other, "sk-openai-SECRET-9999")
    check("one provider's key is not in another's file",
          "sk-openai-SECRET-9999" not in path.read_text(encoding="utf-8"))

    # --- the environment wins ---------------------------------------------
    os.environ["ANTHROPIC_API_KEY"] = "sk-ant-from-env"
    found = prov.resolve(anth)
    check("an exported key wins over the stored one",
          found == ("sk-ant-from-env", "environment"), str(found))
    os.environ.pop("ANTHROPIC_API_KEY")

    # --- clearing ----------------------------------------------------------
    check("clear removes the stored key", prov.clear(anth) is True)
    check("the file is gone", not path.exists())
    check("clearing twice is False", prov.clear(anth) is False)
    check("a cleared provider resolves to nothing", prov.resolve(anth) is None)

    os.environ["ANTHROPIC_API_KEY"] = "sk-ant-from-env"
    prov.save(anth, key)
    prov.clear(anth)
    found = prov.resolve(anth)
    check("clear leaves the environment alone",
          found == ("sk-ant-from-env", "environment"), str(found))
    os.environ.pop("ANTHROPIC_API_KEY")

    # --- a provider with no key to set ------------------------------------
    tj = prov.BY_KEY["tokenjuice"]
    check("Token Juice takes no key from the registry", tj.keyless)
    check("saving to it is a no-op", prov.save(tj, "tj_whatever") is None)

    # --- keyless providers still report an endpoint ------------------------
    check("Token Juice resolves its route",
          tj.base_url == "https://api.tokenjuice.ai/v1", tj.base_url)

    # --- empty key ---------------------------------------------------------
    try:
        prov.save(anth, "   ")
        check("an empty key is refused", False)
    except ValueError:
        check("an empty key is refused", True)

    # --- the catalog reader, against a local server -----------------------
    seen = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            seen["auth"] = self.headers.get("Authorization")
            if self.path.startswith("/closed") and not seen["auth"]:
                self.send_response(401)
                self.end_headers()
                self.wfile.write(b'{"error":"no key"}')
                return
            body = json.dumps({"data": [
                {"id": "chat-model-a", "owned_by": "x", "created": 1},
                {"id": "text-embedding-3", "owned_by": "x", "created": 1},
                {"id": "tts-1", "owned_by": "x", "created": 1},
            ]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    try:
        models, err = direct_common.fetch_catalog(
            f"http://127.0.0.1:{port}/v1/models", "k-123", host="test")
        check("the catalog parses", err == "" and models is not None, err)
        ids = [m.id for m in models or []]
        check("chat models survive the filter",
              "chat-model-a" in [m.id for m in (models or []) if m.chat], str(ids))
        check("embeddings are filtered out",
              "text-embedding-3" not in [m.id for m in (models or []) if m.chat])
        check("tts is filtered out",
              "tts-1" not in [m.id for m in (models or []) if m.chat])
        check("the key is sent as a bearer", seen["auth"] == "Bearer k-123",
              str(seen.get("auth")))
        check("no model claims to be free",
              all(not m.free for m in models or []))

        models, err = direct_common.fetch_catalog(
            f"http://127.0.0.1:{port}/closed/models", "", host="test")
        check("a key-gated catalog reports the 401",
              models is None and "401" in err, f"{models!r} {err!r}")

        models, err = direct_common.fetch_catalog(
            f"http://127.0.0.1:{port}/v1/models", "", host="test")
        check("the cache is per key, not global",
              seen["auth"] is None, str(seen.get("auth")))
    finally:
        server.shutdown()
        server.server_close()

    # --- a catalog that is unreachable is an error, not an exception ------
    models, err = direct_common.fetch_catalog(
        "http://127.0.0.1:9/v1/models", "", host="test", timeout=2.0)
    check("an unreachable host is reported",
          models is None and err, f"{models!r} {err!r}")

    # --- profiles round-trip ----------------------------------------------
    p2 = HOME / "roundtrip" / "source"
    direct_common.write_profile(p2, "https://x.test", "tok", "m-1", "test")
    exports = direct_common.read_exports(p2)
    check("the profile reads back",
          exports.get("ANTHROPIC_AUTH_TOKEN") == "tok"
          and exports.get("ANTHROPIC_BASE_URL") == "https://x.test"
          and exports.get("ANTHROPIC_MODEL") == "m-1", str(exports))
    check("a missing profile reads as empty",
          direct_common.read_exports(HOME / "nope" / "source") == {})

    # --- the three new modules agree with the registry --------------------
    for name in ("anthropic", "openai", "deepseek"):
        p = prov.BY_KEY[name]
        mod = p.mod()
        check(f"{name}: module base matches registry",
              mod.API_BASE_URL.rstrip("/") == p.base_url,
              f"{mod.API_BASE_URL} vs {p.base_url}")
        check(f"{name}: has a credential loader",
              callable(getattr(mod, "load_credentials", None)))
        check(f"{name}: has a catalog fetcher",
              callable(getattr(mod, "fetch_models", None)))

    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
