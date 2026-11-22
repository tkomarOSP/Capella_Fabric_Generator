"""The local stdio server: its tool surface, its words, and its boundaries.

Step 5 of the stdio plan. What is being tested is a *front end*, so most of
these assertions are about the interface rather than about the model layer
underneath it -- which step 4 already covers against real git repositories.

The cases, and why each is here rather than assumed:

1. **The 14 -> 16 derivation, computed rather than restated.** The hosted
   server's tool count is read out of its source, so the arithmetic in this
   server's own docstring cannot quietly stop matching the server it is
   derived from. A hosted tool added later breaks this test, which is the
   point: it is the moment to decide whether the local server wants it too.
2. **The instructions were written, not trimmed.** Every hosted-only phrase
   is checked twice -- that the hosted string really contains it, and that
   this one does not. Without the first half, "no mention of connect codes"
   passes for a string that never had any and proves nothing about the
   adaptation.
3. **The import graph.** The hosted server, the Flask site, and the optional
   Cartenza ``auth`` package must not be in this process. Proved in an
   isolated subprocess with a positive control, because a probe that imports
   nothing passes every absence check.
4. **A generated artifact leaves the model directory.** The step-0 finding:
   the clone's ``.git/config`` holds this model's token for as long as the
   directory exists, and here it is never deleted. So nothing servable may
   remain beside it. Checked with a control that the file was there first.
5. **``generate_viewer_link`` refuses a path.** A file name, never a path --
   because the views directory is outside the model directory precisely so
   that no amount of ``..`` reaches a credential.
6. **The write ceiling holds at the tool boundary**, both halves, each with a
   positive control that a writable and current model gets *past* it. A
   refusal test passes for free until the thing it refuses is real.
7. **An unknown session says the one useful thing**, and closing one keeps
   the clone.
8. **A real stdio handshake.** ``initialize`` and ``tools/list`` over actual
   pipes, against the launcher the setup page prints -- which is the only
   assertion that distinguishes "sixteen tools are registered" from "a
   working stdio server". It also proves nothing but JSON-RPC reaches stdout:
   every line is parsed, so a banner printed to the wrong stream fails here
   rather than in a harness.
"""

import ast
import asyncio
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(r"c:\apps\Git_VSCode\Capella_Fabric_Generator")
sys.path.insert(0, str(REPO))

import capella_service as svc

from capella_bundle import config, session, settings

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if ok else 'FAIL'} {label}" + (f": {detail}" if detail and not ok else ""))
    if not ok:
        failures.append(label)


def raises(label: str, fn, *, saying: str = "") -> str:
    """Assert a call fails, and return what it said, so a caller can read it."""
    try:
        fn()
    except Exception as exc:
        if saying and saying.lower() not in str(exc).lower():
            check(label, False, f"raised but did not say {saying!r}: {exc}")
        else:
            check(label, True)
        return str(exc)
    check(label, False, "did not raise")
    return ""


ROOT = Path(tempfile.mkdtemp(prefix="stdio_tools_"))
settings.default_state_root = lambda: ROOT
config.default_state_root = lambda: ROOT


class Files(settings.FileStore):
    """The file credential backend, forced: a test must not write a keychain."""

    def __init__(self) -> None:
        super().__init__(ROOT / "credentials")


STORE = Files()
settings.credential_store = lambda prefer_file=False: STORE

# Imported after the redirection above, so that nothing it does at import
# time reaches the engineer's real profile directory.
import stdio_server as server  # noqa: E402

HOSTED = (REPO / "mcp_server.py").read_text(encoding="utf-8")

# ---------------------------------------------------------------------------
print("1. Sixteen tools, and the derivation from the hosted fourteen")
# ---------------------------------------------------------------------------

LOCAL = {
    "setup_model", "use_profile", "list_my_scopes", "open_model",
    "close_model", "list_object_types", "browse_model",
    "search_model_objects", "resolve_model_uuids", "verify_model",
    "generate_fabric", "render_diagram_view", "generate_viewer_link",
    "apply_model_patch", "push_model_changes", "pull_model_changes",
}

# Read out of the hosted source rather than restated, and WITHOUT importing
# it: importing would start a second FastMCP, register its tools into this
# process and pull in the HTTP transport, which is the very thing section 3
# asserts does not happen.
hosted_names = set(re.findall(r"@mcp\.tool\(\)\s*\ndef (\w+)\(", HOSTED))

TOOLS = asyncio.run(server.mcp.list_tools())
registered = {tool.name for tool in TOOLS}

check("the hosted server still has exactly fourteen tools",
      len(hosted_names) == 14, f"{len(hosted_names)}: {sorted(hosted_names)}")
check("this server registers exactly sixteen",
      len(registered) == 16, f"{len(registered)}: {sorted(registered)}")
check("...and they are the sixteen named in its docstring",
      registered == LOCAL, str(registered ^ LOCAL))

HOSTED_ONLY = {"begin_connect"}
MOVED_TO_SETUP = {"add_dependency_repo"}
RENAMED = {"clone_capella_repo": "open_model", "cleanup_session": "close_model"}
LOCAL_ONLY = {"setup_model", "use_profile", "list_my_scopes",
              "generate_viewer_link"}

keepers = hosted_names - HOSTED_ONLY - MOVED_TO_SETUP
check("fourteen minus one hosted-only minus one moved leaves twelve keepers",
      len(keepers) == 12, f"{len(keepers)}: {sorted(keepers)}")
check("twelve keepers plus four local-only is sixteen",
      len(keepers) + len(LOCAL_ONLY) == 16)
for old, new in RENAMED.items():
    check(f"{old} is absent and {new} is present",
          old not in registered and new in registered)
check("every renamed tool was a hosted one",
      all(old in hosted_names for old in RENAMED))
check("every local-only tool is new, with no hosted counterpart",
      not (LOCAL_ONLY & hosted_names), str(LOCAL_ONLY & hosted_names))

# Absent, not erroring: a tool that exists and answers "that needs the
# Cartenza website" is worse than one never advertised, because a harness
# choosing tools cannot tell a broken capability from a missing one.
for absent in sorted(HOSTED_ONLY | MOVED_TO_SETUP):
    check(f"{absent} is not registered at all", absent not in registered)
    check(f"...and {absent} is not even a stub in the module",
          not hasattr(server, absent))

undocumented = [tool.name for tool in TOOLS
                if not (tool.description or "").strip()]
check("every tool carries a description for the agent to read",
      not undocumented, str(undocumented))

# No credential-shaped parameter anywhere on this interface. The hosted
# clone_capella_repo takes github_pat; nothing here takes a secret.
secretish = sorted(
    f"{tool.name}.{arg}"
    for tool in TOOLS
    for arg in (tool.inputSchema.get("properties") or {})
    # Whole words, with the trailing boundary: a substring match reports
    # apply_model_patch's own `patch_yaml` as a credential.
    if re.search(r"\b(pat|token|secret|password|api_key|credential)\b", arg, re.I)
)
check("no tool takes a credential as an argument", not secretish, str(secretish))

# ---------------------------------------------------------------------------
print("\n2. The instructions were written for here, not trimmed from hosted")
# ---------------------------------------------------------------------------

TEXT = server.INSTRUCTIONS
lower = TEXT.lower()

# Each phrase is checked against the HOSTED string first. That half is what
# makes the second half mean something: "no mention of connect codes" is
# satisfied for free by any text that never had any.
HOSTED_ONLY_PHRASES = [
    "connect_code", "begin_connect", "cartenza.ai", "/onboarding/agents",
    "add_dependency_repo", "connect via github", "4 hours",
    "workspace-manager", "write_workspace_artifact",
    "generate_artifact_viewer_link", "github_pat",
]
for phrase in HOSTED_ONLY_PHRASES:
    check(f"the hosted string really does advertise {phrase!r}",
          phrase in HOSTED.lower(), "not in mcp_server.py")
    check(f"...and this one does not mention it", phrase not in lower)

for named in ["list_my_scopes", "use_profile", "setup_model", "open_model",
              "push_model_changes", "apply_model_patch",
              "generate_viewer_link", "list_object_types"]:
    check(f"{named} is named, so an agent knows the sequence",
          named in TEXT)

for truth in [
    ("open_model never clones", "never clones" in lower),
    ("the sync field is explained", "synced" in lower),
    ("a restart is survivable", "restarted" in lower),
    ("the single-file hazard is stated", "single file" in lower
     or "one file" in lower),
    ("a divergence is handed to the engineer", "diverged" in lower),
    ("nothing reaches the remote before a push",
     "until push_model_changes" in lower),
    ("the empty requirement name is warned about", "long_name" in TEXT),
    ("actors are explained", "is_actor" in TEXT),
    ("layer is distinguished from viewpoint", "viewpoint" in lower),
]:
    check(f"it says {truth[0]}", truth[1])

# The patch rules live on the one tool that uses them, and are NOT paid for
# twice in a budget every client freezes at connect.
check("the declarative-patch rules are not in the instructions",
      "owned_components" not in TEXT and "!promise" not in TEXT)
check("...they are in apply_model_patch's own docstring",
      "owned_components" in (server.apply_model_patch.__doc__ or "")
      and "!promise" in (server.apply_model_patch.__doc__ or ""))
check("the instructions are a fraction of the hosted string's size",
      len(TEXT) < 9000, str(len(TEXT)))

# ---------------------------------------------------------------------------
print("\n3. What this server does not import")
# ---------------------------------------------------------------------------

probe = ROOT / "probe_imports.py"
probe.write_text(
    "import sys\n"
    f"sys.path.insert(0, r'{REPO}')\n"
    "import stdio_server\n"
    "print(repr(sorted(sys.modules)))\n", encoding="utf-8")
# -I: no PYTHONPATH and no user site directory, so nothing reaches the child
# by a route this test did not intend. The path is inserted inside the child
# because -I also drops the script's own directory.
done = subprocess.run([sys.executable, "-I", str(probe)],
                      capture_output=True, text=True, cwd=str(ROOT))
check("the server imports in an isolated interpreter",
      done.returncode == 0, done.stderr[-600:])
loaded = set(ast.literal_eval(done.stdout.strip() or "[]"))

# The control. Every absence below is satisfied by a probe that imported
# nothing at all, so first prove it imported the thing under test.
check("**the control: capella_service really was imported**",
      "capella_service" in loaded)
check("...and so was the bundle", "capella_bundle.session" in loaded)
check("...and the foundation library it is built on", "mcp_desktop" in loaded)

for forbidden, why in [
    ("mcp_server", "the hosted server would register its own fourteen tools"),
    ("app", "the Flask site has no business in an MCP process"),
    ("flask", "same"),
    ("auth", "the optional Cartenza auth package is a hosted dependency"),
    ("auth.connect", "same"),
    ("capella_bundle.setup_app", "a web application must stay out of the "
                                 "server's import graph"),
]:
    check(f"{forbidden} is not imported ({why})", forbidden not in loaded)

# ---------------------------------------------------------------------------
print("\n4. A generated artifact leaves the model directory")
# ---------------------------------------------------------------------------


def git(repo: Path, *args: str) -> str:
    done = subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True)
    assert done.returncode == 0, f"git {' '.join(args)}: {done.stderr}"
    return done.stdout.strip()


def origin(name: str) -> str:
    """A bare repository holding a Capella-shaped model, as a file:// URI."""
    work = ROOT / "fixtures" / name
    (work / "model").mkdir(parents=True)
    (work / "model" / f"{name}.aird").write_text("<aird/>", encoding="utf-8")
    subprocess.run(["git", "init", "-q", "-b", "main", str(work)],
                   check=True, capture_output=True)
    git(work, "add", "-A")
    git(work, "-c", "user.email=f@x", "-c", "user.name=f", "commit", "-qm", "init")
    bare = ROOT / "fixtures" / f"{name}.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(work), str(bare)],
                   check=True, capture_output=True)
    return bare.as_uri()


AC = origin("ac")
STALE = origin("stale")

settings.save_profile(settings.Profile(
    name="work",
    models=(
        settings.ModelEntry(nickname="ac", remote_url=AC, branch="main",
                            access="write"),
        settings.ModelEntry(nickname="locked", remote_url=AC, branch="main",
                            access="read",
                            model_dir=ROOT / "models" / "locked",
                            views_dir=ROOT / "views" / "locked"),
        settings.ModelEntry(nickname="stale", remote_url=STALE, branch="main",
                            access="write"),
    )))

server.SELECTED_PROFILE = "work"
server._PROFILE = None

profile = settings.load_profile("work")
for nickname in ("ac", "locked", "stale"):
    session.prepare(settings.to_config(profile, nickname))

opened = server.open_model("ac")
AC_SID = opened["session_id"]
ac_cfg, _ = server._OPEN[AC_SID]

check("open_model returns a session the service resolves",
      svc._session_dir(AC_SID) == ac_cfg.model_dir, str(svc._session_dir(AC_SID)))
check("...and reports no write refusal for a writable, current model",
      "write_refusal" not in opened, str(opened.get("write_refusal")))

# capella_service writes a rendered view into the session directory, which
# under this server's adapter IS the model directory. Plant one exactly where
# it lands and publish it, rather than rendering -- the fixture .aird is not a
# real model, and what is under test here is the relocation, not capellambse.
planted = ac_cfg.model_dir / "view_fake__context.html"
planted.write_text("<html>a view</html>", encoding="utf-8")
check("**the control: the artifact starts inside the model directory**",
      planted.is_file() and config.state.inside(ac_cfg.model_dir, planted))

published = server._publish(ac_cfg, planted, move=True)
check("it is published into the views directory",
      published == ac_cfg.views_dir / planted.name, str(published))
check("...with its content intact",
      published.read_text(encoding="utf-8") == "<html>a view</html>")
check("...and nothing servable is left in the model directory",
      not planted.exists() and not list(ac_cfg.model_dir.glob("*.html")),
      str(list(ac_cfg.model_dir.glob('*'))))
check("the views directory is outside the model directory",
      not config.state.inside(ac_cfg.model_dir, ac_cfg.views_dir),
      f"{ac_cfg.views_dir} vs {ac_cfg.model_dir}")
check("...so the clone's credential file is not reachable from it",
      not config.state.inside(ac_cfg.views_dir,
                              ac_cfg.clone_dir / ".git" / "config"))

# A fabric is copied rather than moved: the path capella_service returned is
# what the tool reports and may re-read.
fabric = ac_cfg.model_dir / "ac_fabric.txt"
fabric.write_text("objects: []\n", encoding="utf-8")
copied = server._publish(ac_cfg, fabric, move=False)
check("a fabric is copied, and the original stays put",
      copied.is_file() and fabric.is_file())

links = server._links(ac_cfg, published)
# This block asserted the *absence* of a url when it was written, because the
# page server did not exist yet. It does now, so the assertion that survives
# is the one about what `_links` always promises: the file's own path beside
# whatever link there is. The degradation path -- no page module at all -- is
# tested where it belongs, in test_views_loopback.py, by removing the module.
check("the file's own path is always reported",
      links["path"] == str(published), str(links))
check("...with a loopback link beside it now that pages are served",
      links.get("url", "").startswith("http://127.0.0.1:"), str(links)[:200])
check("...and markdown naming the file rather than the path",
      links.get("viewer_markdown", "").startswith(f"[{published.name}](http://"),
      links.get("viewer_markdown", ""))
check("...and no credential anywhere in it",
      "oauth2" not in str(links) and ac_cfg.pat not in str(links)
      if ac_cfg.pat else "oauth2" not in str(links), str(links)[:200])

# ---------------------------------------------------------------------------
print("\n5. generate_viewer_link takes a name, never a path")
# ---------------------------------------------------------------------------

listed = server.generate_viewer_link(AC_SID)
check("with no filename it lists what the model produced",
      set(listed["produced"]) == {published.name, copied.name},
      str(listed["produced"]))
check("...and says what to do next", "generate_viewer_link" in listed["then"])

one = server.generate_viewer_link(AC_SID, published.name)
check("a known file resolves", one["filename"] == published.name)
check("...inside the views directory", one["path"] == str(published))

traversal = raises(
    "a path is refused rather than resolved",
    lambda: server.generate_viewer_link(
        AC_SID, r"..\unpacked\.git\config"), saying="path")
check("...and the refusal says to pass the reported name",
      "filename" in traversal, traversal)
for attempt in ["../../../../Windows/win.ini", "sub/view.html",
                str(ac_cfg.clone_dir / ".git" / "config")]:
    raises(f"{attempt!r} is refused", lambda a=attempt:
           server.generate_viewer_link(AC_SID, a))
raises("an unknown name says what the model has",
       lambda: server.generate_viewer_link(AC_SID, "nope.html"),
       saying="no 'nope.html'")

# ---------------------------------------------------------------------------
print("\n6. The write ceiling, both halves, at the tool boundary")
# ---------------------------------------------------------------------------

PATCH = "- parent: !uuid 00000000-0000-0000-0000-000000000000\n  set:\n    name: x\n"

locked = server.open_model("locked")
LOCKED_SID = locked["session_id"]
check("a read-only model reports its refusal at open",
      "read" in (locked.get("write_refusal") or "").lower(),
      str(locked.get("write_refusal")))
declared = raises("apply_model_patch refuses a read-only model",
                  lambda: server.apply_model_patch(
                      LOCKED_SID, PATCH, "nope"), saying="reading only")
check("...and says nothing was changed", "nothing has been changed"
      in declared.lower(), declared)
check("...and names the setup page as where it changes",
      "setup page" in declared.lower(), declared)
raises("push_model_changes refuses it too",
       lambda: server.push_model_changes(LOCKED_SID), saying="reading only")
check("nothing was committed to the read-only clone",
      git(settings.to_config(profile, "locked").clone_dir,
          "status", "--porcelain") == "")

# The control for the declared half: the same patch on a WRITABLE model gets
# past the ceiling. It then fails inside capella_service, because the fixture
# .aird is a stub rather than a model -- which is the proof that the refusal
# above was the ceiling and not something every call hits. It comes back as a
# status rather than an exception, because a patch the model layer rejected is
# an answer about the patch; the ceiling is a refusal to try.
try:
    got = server.apply_model_patch(AC_SID, PATCH, "try")
    outcome = json.dumps(got, default=str)
except Exception as exc:
    outcome = f"raised: {exc}"
check("**the control: a writable model gets past the ceiling**",
      "reading only" not in outcome.lower()
      and "behind" not in outcome.lower()
      and "needs a person" not in outcome.lower(), outcome[:300])
check("...and reached capella_service, which rejected the stub model",
      "error" in outcome.lower(), outcome[:300])
check("...so nothing was committed on a patch that did not apply",
      git(ac_cfg.clone_dir, "rev-list", "--count", "HEAD") == "1",
      git(ac_cfg.clone_dir, "log", "--oneline"))

# The measured half, case one: BEHIND the remote. Injected into the stored
# sync state rather than staged on disk, deliberately and with that said out
# loud -- a clone that is merely behind and clean is fast-forwarded by
# open_model, so the state cannot survive an open, and the only honest way to
# exercise the tool's reading of it is to hand it the dict open_model would
# have returned. That the dict is *produced* correctly is step 4's test,
# against real clones; what is tested here is that this tool consults it.
server._OPEN[AC_SID] = (ac_cfg, {"synced": False, "behind": 2,
                                 "sync_note": "two commits on the remote"})
behind = raises("a patch onto a clone behind its remote is refused",
                lambda: server.apply_model_patch(AC_SID, PATCH, "nope"),
                saying="behind")
check("...and the refusal explains that a model is a single file",
      "single file" in behind.lower(), behind)
check("...and that pushing it would revert someone else's work",
      "revert" in behind.lower(), behind)
check("...and says to call open_model again rather than offering a git fix",
      "open_model again" in behind, behind)
server._OPEN[AC_SID] = (ac_cfg, dict(opened))     # put it back as it was

# Case two: DIVERGED, staged for real. A second clone pushes, this one
# commits, and the two histories are then irreconcilable without a person.
stale_cfg = settings.to_config(profile, "stale")
server.open_model("stale")
other = ROOT / "other"
subprocess.run(["git", "clone", "-q", STALE, str(other)],
               check=True, capture_output=True)
(other / "model" / "stale.aird").write_text("<aird newer='1'/>", encoding="utf-8")
git(other, "add", "-A")
git(other, "-c", "user.email=o@x", "-c", "user.name=o", "commit", "-qm", "theirs")
git(other, "push", "-q", "origin", "main")

# Re-open: the fetch is what measures it. A fast-forward would make it
# current, so to hold it BEHIND the remote the local clone gets a commit of
# its own, which makes the two diverge instead.
(stale_cfg.clone_dir / "model" / "stale.aird").write_text(
    "<aird mine='1'/>", encoding="utf-8")
git(stale_cfg.clone_dir, "add", "-A")
git(stale_cfg.clone_dir, "-c", "user.email=m@x", "-c", "user.name=m",
    "commit", "-qm", "mine")
reopened = server.open_model("stale")
check("re-opening a diverged model reports the divergence",
      reopened.get("diverged") is True, str(reopened))
check("...and does not claim to be synced", reopened.get("synced") is not True)
measured = raises("a patch onto a diverged model is refused",
                  lambda: server.apply_model_patch(
                      reopened["session_id"], PATCH, "nope"),
                  saying="needs a person")
check("...and the refusal says a Capella model is one file",
      "one file" in measured.lower(), measured)
check("...and hands the decision to a person, not a git command",
      "capella" in measured.lower() and "git " not in measured.lower(),
      measured)
check("...and says nothing was changed",
      "nothing has been changed" in measured.lower(), measured)
head_before = git(stale_cfg.clone_dir, "rev-parse", "HEAD")
raises("pushing a diverged model is refused as well",
       lambda: server.push_model_changes(reopened["session_id"]),
       saying="needs a person")
check("...and HEAD did not move",
      git(stale_cfg.clone_dir, "rev-parse", "HEAD") == head_before)
check("...and the local edit is still on disk",
      "mine" in (stale_cfg.clone_dir / "model" / "stale.aird").read_text(
          encoding="utf-8"))

# ---------------------------------------------------------------------------
print("\n7. Sessions, and the words used when one is not recognised")
# ---------------------------------------------------------------------------

unknown = raises("an unknown session_id is refused",
                 lambda: server.browse_model("made-up", "SA", "Component"),
                 saying="unknown session_id")
for phrase in ["open_model", "restarted", "costs nothing",
               "already on disk"]:
    check(f"...and the message says {phrase!r}", phrase in unknown, unknown)
# An agent that reads "unknown session" and remembers the hosted rules may
# tell the engineer a browser trip is needed. Nothing here should suggest one.
for absent in ["connect code", "connect_code", "browser", "api_key",
               "re-clone", "authorization"]:
    check(f"...and does not mention {absent!r}",
          absent not in unknown.lower(), unknown)

raises("an unknown object_type names the valid ones",
       lambda: server.browse_model(AC_SID, "SA", "Sprocket"),
       saying="valid types")

# Switching profile while models are open: refused, and for that reason --
# the profile named here exists, so a "no such profile" error would be this
# check passing for the wrong cause.
settings.save_profile(settings.Profile(name="spare"))
check("**the control: the profile being switched to really exists**",
      "spare" in settings.list_profiles(), str(settings.list_profiles()))
switch = raises("switching profile with models open is refused",
                lambda: server.use_profile("spare"), saying="models open")
check("...and it says to close them first",
      "close_model" in switch and "nothing is lost" in switch.lower(), switch)
check("...and the session still holds the profile it had",
      server.SELECTED_PROFILE == "work", server.SELECTED_PROFILE)

closed = server.close_model(AC_SID)
check("closing reports the clone was kept",
      closed["status"] == "closed" and closed["model_kept_at"] == str(
          ac_cfg.model_dir))
check("...and the clone really is still there",
      (ac_cfg.clone_dir / ".git").is_dir())
check("...and the session is gone from this server", AC_SID not in server._OPEN)
raises("a closed session is unknown again",
       lambda: server.generate_viewer_link(AC_SID), saying="unknown session_id")

# No credential in anything this server said. The fixtures are file:// URIs
# with no token, so the assertion that bites is the shape: a profile on disk
# must not carry one either.
profile_text = (ROOT / "profiles" / "work.json").read_text(encoding="utf-8")
check("the profile file holds no credential",
      not re.search(r"\b(pat|token|secret|password)\b", profile_text, re.I),
      profile_text[:200])

# ---------------------------------------------------------------------------
print("\n8. A real stdio handshake, through the launcher")
# ---------------------------------------------------------------------------

LAUNCHER = REPO / "run_capella_fabric.py"
check("the launcher the setup page prints exists", LAUNCHER.is_file())
# Compared case-insensitively: this test spells the repository root with a
# lower-case drive letter and Path.resolve() hands back an upper-case one, so
# a plain substring test fails on a command that is perfectly correct.
check("...and launch_command names it, with a full interpreter path",
      str(LAUNCHER).lower() in server.launch_command().lower()
      and sys.executable.lower() in server.launch_command().lower(),
      server.launch_command())

wire = [
    {"jsonrpc": "2.0", "id": 1, "method": "initialize",
     "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                "clientInfo": {"name": "stdio-test", "version": "0"}}},
    {"jsonrpc": "2.0", "method": "notifications/initialized"},
    {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
]
# A real harness environment, minus anything that would point the child at
# this test's temporary state root: the handshake must work on the engineer's
# own machine configuration, or on none at all.
child_env = {k: v for k, v in os.environ.items()
             if not k.startswith(("PYTHON", "CARTENZA_"))}
spoke = subprocess.run(
    [sys.executable, str(LAUNCHER)],
    input="".join(json.dumps(m) + "\n" for m in wire),
    capture_output=True, text=True, timeout=300,
    cwd=str(ROOT), env=child_env)

lines = [line for line in spoke.stdout.splitlines() if line.strip()]
parsed = []
unparseable = []
for line in lines:
    try:
        parsed.append(json.loads(line))
    except json.JSONDecodeError:
        unparseable.append(line)

check("**nothing but JSON-RPC reached stdout**", not unparseable,
      str(unparseable[:3]))
check("the banner went to stderr instead",
      "Capella Fabric" in spoke.stderr, spoke.stderr[-300:])
check("the server answered the handshake", len(parsed) >= 2,
      f"{len(parsed)} message(s); stderr: {spoke.stderr[-400:]}")

if len(parsed) >= 2:
    init, tools = parsed[0], parsed[1]
    info = (init.get("result") or {}).get("serverInfo") or {}
    check("...naming itself", info.get("name") == "Capella Fabric", str(info))
    served = (init.get("result") or {}).get("instructions") or ""
    check("...and serving its instructions at connect",
          "list_my_scopes" in served, served[:120])
    check("...which are the ones written for here",
          "cartenza.ai" not in served.lower())
    names = sorted(t["name"] for t in (tools.get("result") or {}).get("tools", []))
    check("tools/list returns the sixteen over the wire",
          len(names) == 16 and set(names) == LOCAL, str(names))

# setup_model() hands over a loopback link with a one-time token. The page
# itself, its guards and its forms are test_setup_page.py's subject; what is
# asserted here is the tool's half of the contract -- that it returns a link
# rather than a path or a command, and that the web application is reached
# only from inside the function.
handed = server.setup_model()
check("setup_model returns a loopback url",
      handed.get("url", "").startswith("http://127.0.0.1:"), str(handed)[:200])
check("...carrying a one-time token", "token=" in handed.get("url", ""))
check("...and tells the engineer what to do with it",
      handed["url"] in handed.get("tell_the_engineer", ""), str(handed)[:300])
check("...and does not fall back to a command line now that the page exists",
      "run_this_instead" not in handed, str(handed)[:200])
check("the setup page was imported inside the call, not at module scope",
      "capella_bundle.setup_app" in sys.modules
      and "capella_bundle.setup_app" not in loaded)

print()
if failures:
    print(f"{len(failures)} CHECK(S) FAILED")
    for name in failures:
        print(f"  - {name}")
    sys.exit(1)
print("stdio tools: all checks passed")
