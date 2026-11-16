"""The server must say at boot whether its credential-free path came up.

kp-auth is an optional dependency, imported under `except ImportError`. When it
is missing the server starts cleanly, serves every other tool, and says nothing
at all -- `_AUTH_AVAILABLE` quietly becomes False. systemd reports active, pip
reported success when it installed an empty wheel, and `EnvironmentFile=-` means
a missing env file is not a startup error either. Four layers each correctly
reporting "fine" about their own narrow concern.

On 2026-11-15 the result was that a customer discovered the credential-free
connect path was dead before the operator did, and the cause had already been
diagnosed in writing months earlier -- the knowledge existed and nothing on the
box could act on it, because documentation does not fire.

These checks pin the thing that does fire:

1. kp-auth missing -> DISABLED, with the real cause, at boot.
2. kp-auth present but KP_CONNECT_BASE_URL unset -> DISABLED. This is the
   half-configured state the error messages hide: begin_connect then fails with
   an unrelated "env var not set" message, so the server looks healthy until
   someone calls the tool.
3. Fully configured -> ENABLED, naming the connect base URL.
4. Every line names the host, because two deployments answer a failed connect
   with near-identical sentences and a stale URL in a user's system prompt is
   indistinguishable from a broken server without it.
"""
import importlib
import os
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

VARS = ("KP_CONNECT_BASE_URL", "CARTENZA_DB_PATH", "KP_TREE_PATH",
        "CAPELLA_SERVER_LABEL")


def load_server(**env):
    saved = {k: os.environ.get(k) for k in VARS}
    for k in VARS:
        os.environ.pop(k, None)
    os.environ.update({k: v for k, v in env.items() if v is not None})
    try:
        import mcp_server
        mod = importlib.reload(mcp_server)
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    return mod


LABEL = "capella.cartenza.ai"

# ===== 1. kp-auth missing, code on disk: the production shape ===============
fake_tree = pathlib.Path(tempfile.mkdtemp(prefix="kptree_"))
(fake_tree / "auth").mkdir()
(fake_tree / "auth" / "__init__.py").write_text("", encoding="utf-8")

srv = load_server(KP_TREE_PATH=str(fake_tree), CAPELLA_SERVER_LABEL=LABEL)
srv._AUTH_AVAILABLE = False
report = srv._startup_report()
blob = "\n".join(report)

assert report, "a server that says nothing at boot is the bug"
assert LABEL in report[0], f"the first line must name the host: {report[0]}"
assert "DISABLED" in blob, blob
assert "kp-auth not importable" in blob
assert f"PYTHONPATH={fake_tree}" in blob, (
    "the boot line must carry the actual remedy, not just a complaint -- the "
    "operator reading the journal should not have to go looking"
)
assert "ENABLED" not in blob
print("1. kp-auth missing:      DISABLED, names the host and the fix")


# ===== 2. kp-auth present, KP_CONNECT_BASE_URL unset ========================
# The half-configured state. begin_connect would fail here with "env var not
# set" -- a different message from every case above, reached only by calling
# the tool. Nothing else on the box reports it.
srv2 = load_server(CAPELLA_SERVER_LABEL=LABEL)
srv2._AUTH_AVAILABLE = True
srv2._CONNECT_BASE_URL = ""
blob2 = "\n".join(srv2._startup_report())

assert "DISABLED" in blob2, blob2
assert "KP_CONNECT_BASE_URL" in blob2, (
    "a server with kp-auth importable but no connect base URL cannot mint a "
    "connect URL, and must not be reported as healthy"
)
assert "kp-auth not importable" not in blob2, (
    "wrong cause -- the import worked; blaming it sends the operator to fix "
    "something that is not broken"
)
print("2. half-configured:      DISABLED, names the missing env var")


# ===== 3. fully configured ==================================================
srv3 = load_server(KP_CONNECT_BASE_URL="https://connect.cartenza.ai",
                   CAPELLA_SERVER_LABEL=LABEL)
srv3._AUTH_AVAILABLE = True
srv3._CONNECT_BASE_URL = "https://connect.cartenza.ai"
blob3 = "\n".join(srv3._startup_report())

assert "ENABLED" in blob3, blob3
assert "https://connect.cartenza.ai" in blob3
assert "DISABLED" not in blob3
print("3. fully configured:     ENABLED, names the connect endpoint")


# ===== 4. the host is named in the tool error too, not only at boot =========
# A user reading a failed begin_connect sees the message, never the journal.
# On 2026-11-15 a stale URL copied from an old system prompt reached the
# standalone droplet and produced a sentence identical to the co-deploy fault
# that had just been fixed. Naming the responder is what tells them apart.
solo = load_server(CAPELLA_SERVER_LABEL="mcp.innovatingwithcapella.com")
solo._AUTH_AVAILABLE = False
msg = solo._no_auth_msg()
assert "mcp.innovatingwithcapella.com" in msg, (
    "the standalone's message is the one a wrong-endpoint paste lands on, so "
    f"it above all must say who answered. Got: {msg}"
)
assert "expected answer on a standalone" in msg
print("4. tool error:           names the host that answered")


# ===== 5. an unlabelled deployment still boots, and asks to be labelled =====
plain = load_server()
plain._AUTH_AVAILABLE = True
plain._CONNECT_BASE_URL = "https://connect.cartenza.ai"
first = plain._startup_report()[0]
assert "CAPELLA_SERVER_LABEL" in first, (
    f"an unnamed server should say how to name itself. Got: {first}"
)
assert plain._server_phrase() == "this server", (
    "no label must read as prose -- 'BROKEN on this server', never "
    "'BROKEN on ' with a hole where the name should be"
)

print("5. unlabelled:           boots, and says how to name itself")

# ===== 6. the boot report does not repeat the host it just named ============
# Its first line says `server: <host>`; the detail line below it must not say
# so again. A journal entry that repeats itself gets skimmed, and the whole
# point of this line is that someone reads it.
dup = load_server(KP_TREE_PATH=str(fake_tree), CAPELLA_SERVER_LABEL=LABEL)
dup._AUTH_AVAILABLE = False
rep = dup._startup_report()
assert LABEL in rep[0]
assert sum(LABEL in line for line in rep) == 1, (
    "the host should be named exactly once in the boot report: " + str(rep)
)
# ...but a tool error, whose reader has no other context, always names it.
assert LABEL in dup._no_auth_msg(), "the tool error must still name the host"
print("6. boot report:          names the host once; tool error always does")

import shutil  # noqa: E402
shutil.rmtree(fake_tree, ignore_errors=True)

print("\nALL STARTUP REPORT CHECKS PASSED")
