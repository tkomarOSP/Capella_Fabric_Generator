"""A missing kp-auth must blame the server, not the user.

A production user could not connect their Capella model. Their agent reported
two failures and drew the obvious conclusion from them:

  * the agent_id reconnect "didn't work -- the server has no Connect via GitHub
    authorization stored for this repo yet", so: go authorize it on the site
  * begin_connect said "Credential-free connect isn't configured on this server
    -- pass github_pat directly instead", so: ask the server operator

Both symptoms had ONE cause -- kp-auth was not importable -- and the first
piece of advice was useless. Authorizing the repo on the Cartenza site could
not have helped, because the path that consumes that authorization is disabled
by the very same missing import. The user would have authorized, retried, and
failed in exactly the same way.

The cause of the first message is structural: `clone_capella_repo`'s agent_id
branch is guarded by `and _AUTH_AVAILABLE`, so without kp-auth it is skipped
and control reaches the generic "provide one of these three things" error --
which lists `(repo_url and agent_id)`, the very combination the agent had just
supplied. That reads as "your authorization is missing" when the truth is "that
route does not exist on this server".

What these checks pin:

1. repo_url + agent_id with no kp-auth says the credential-free path is
   unavailable -- it does NOT list agent_id back as an option.
2. On a server configured as a Cartenza co-deploy, the message says the fault
   is server-side, names PYTHONPATH and the empty-wheel cause, and explicitly
   tells the agent not to send the user off to authorize anything.
3. On a standalone server, "pass github_pat" is still the right answer and must
   not be turned into a bug report. This is the sentence CARTENZA_DEPLOY.md
   warns about: the same message is correct on one droplet and a fault on the
   other, so check which before "fixing" it.
"""
import importlib
import os
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

# Cleared before every load, so no case can pass because of a leftover value
# from the previous one.
CARTENZA_VARS = ("KP_CONNECT_BASE_URL", "CARTENZA_DB_PATH", "KP_TREE_PATH")


def load_server(**env):
    """Import mcp_server with a chosen environment, auth forced unavailable."""
    saved = {k: os.environ.get(k) for k in CARTENZA_VARS}
    for k in CARTENZA_VARS:
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


# ===== 1. a Cartenza co-deploy with kp-auth missing is a SERVER fault =======
srv = load_server(KP_CONNECT_BASE_URL="https://connect.cartenza.ai")
srv._AUTH_AVAILABLE = False
msg = srv._no_auth_msg()

assert "server-side fault" in msg, msg
assert "PYTHONPATH" in msg, "the message must name the actual remedy"
assert "empty wheel" in msg, "and why pip install -e looks like it worked"
assert "BROKEN" in msg and "not unconfigured" in msg
# The specific wrong turn this is here to prevent.
assert "Do NOT tell the user to authorize" in msg, (
    "the whole point: the user's authorization cannot help when the path that "
    "would consume it is the one that is disabled"
)
assert "pass github_pat" not in msg, (
    "impossible advice for the client that needs this path most -- an agent "
    "that refuses to carry a raw PAT is why begin_connect exists"
)
print("co-deploy:  blames the server, names PYTHONPATH, forbids the wrong advice")


# ===== 2. the clone error must not hand agent_id back as an option ==========
# Reaching the real tool function through FastMCP's decorator.
clone = srv.clone_capella_repo
fn = getattr(clone, "fn", None) or getattr(clone, "__wrapped__", clone)

out = fn(repo_url="https://github.com/x/Distiller_Project",
         agent_id="some-registered-agent", branch="master")
err = out.get("error", "")
assert err, f"expected an error, got {out!r}"
assert "server-side fault" in err, (
    "repo_url + agent_id with no kp-auth must report the missing auth, not the "
    f"generic provide-one-of-three message. Got: {err[:200]}"
)
assert "or (repo_url and agent_id)" not in err, (
    "this is the exact text that misled a production user: it lists back the "
    "combination the agent had just supplied, which reads as 'your "
    "authorization is not stored'"
)
print("clone:      repo_url+agent_id with no auth reports the real cause")

# begin_connect, the other half of what that user saw.
begin = srv.begin_connect
bfn = getattr(begin, "fn", None) or getattr(begin, "__wrapped__", begin)
berr = bfn(agent_id="some-registered-agent",
           repo_url="https://github.com/x/Distiller_Project",
           branch="master").get("error", "")
assert "server-side fault" in berr, berr
print("begin:      begin_connect reports the same single cause")


# ===== 2b. the ACTUAL production shape: env file never populated ============
# capella.cartenza.ai was an open item at the prod cutover, and the unit uses
# `EnvironmentFile=-`, so a missing file is not a startup error. That leaves a
# box serving capella.cartenza.ai with NO Cartenza env vars set -- which an
# env-var-only test calls "standalone" and declares healthy. The kp tree being
# on disk while the import fails is what distinguishes it.
import tempfile  # noqa: E402

fake_tree = pathlib.Path(tempfile.mkdtemp(prefix="kptree_"))
(fake_tree / "auth").mkdir()
(fake_tree / "auth" / "__init__.py").write_text("", encoding="utf-8")

srv_prod = load_server(KP_TREE_PATH=str(fake_tree))   # no Cartenza vars at all
srv_prod._AUTH_AVAILABLE = False
prod = srv_prod._no_auth_msg()

assert "visible on disk" in prod, prod
assert str(fake_tree) in prod, "the message must name the path it found"
assert f"PYTHONPATH={fake_tree}" in prod, "and the exact fix, not a general gesture"
assert "server-side" in prod and "NOT a missing user authorization" in prod
assert "expected answer on a standalone" not in prod, (
    "this is the production failure: code on disk, unimportable, and no env "
    "vars set. Calling it a healthy standalone server is the whole bug"
)
print("prod shape:  code on disk + no env vars -> names the path and the fix")

# And the clone path reports the same thing, since that is what the user hits.
pfn = getattr(srv_prod.clone_capella_repo, "fn", srv_prod.clone_capella_repo)
perr = pfn(repo_url="https://github.com/x/Distiller_Project",
           agent_id="some-registered-agent", branch="master").get("error", "")
assert "visible on disk" in perr, perr
print("prod shape:  clone_capella_repo reports it too")


# ===== 3. standalone: "pass github_pat" is CORRECT, not a bug ==============
srv2 = load_server()           # neither Cartenza var set
srv2._AUTH_AVAILABLE = False
solo = srv2._no_auth_msg()

assert "pass github_pat" in solo, "on a standalone server this really is the route"
assert "expected answer on a standalone" in solo
assert "server-side fault" not in solo, (
    "a standalone droplet is not misconfigured -- reporting it as broken sends "
    "its operator hunting a bug that does not exist (CARTENZA_DEPLOY.md says "
    "check which droplet you are on before 'fixing' this)"
)
assert "PYTHONPATH" not in solo, "irrelevant advice for a server with no auth DB"
# It should still tell the agent what to do when it cannot carry a credential.
assert "Cartenza-hosted" in solo and "rather than asking them to authorize" in solo
print("standalone: still says pass github_pat, and is not called a fault")


# ===== 4. with auth present, nothing above fires ============================
srv3 = load_server(KP_CONNECT_BASE_URL="https://connect.cartenza.ai")
if srv3._AUTH_AVAILABLE:
    cfn = getattr(srv3.clone_capella_repo, "fn", srv3.clone_capella_repo)
    out3 = cfn(repo_url="https://github.com/x/Distiller_Project",
               agent_id="definitely-not-a-real-agent", branch="master")
    e3 = out3.get("error", "")
    assert "server-side fault" not in e3, (
        "with kp-auth importable this must reach the real scope lookup and "
        f"report what it found, not the deployment message. Got: {e3[:160]}"
    )
    print("auth ok:    falls through to the real scope lookup")
else:
    print("auth ok:    skipped -- kp-auth not importable in this interpreter")

import shutil  # noqa: E402
shutil.rmtree(fake_tree, ignore_errors=True)

print("\nALL NO-AUTH DIAGNOSIS CHECKS PASSED")
