"""One permanent clone per model, against real git repositories.

Step 4 of the stdio plan. Nothing here is mocked: every section runs against a
real bare origin and real clones over ``file://``, because what is being
tested is an inversion of the hosted session model and git's own answers are
the assertions.

The cases, and why each is here rather than assumed:

1. **The adapter is ``_session_dir``, not ``_TEMP_BASE``.** Replacing the
   function rather than reassigning the base is what lets a model live
   anywhere, and it is a dict read rather than a module global flipped per
   call -- which matters because a harness can issue parallel tool calls. The
   control is checked too: an unregistered id still resolves the way the
   hosted server resolves it, so importing this package cannot change how the
   hosted front end behaves.
2. **The session id is the model directory's name**, and stable across
   restarts. Forced rather than chosen: ``_session_dir`` joins the id onto a
   base, so the session directory and the permanent model directory can only
   be one directory if the id is that directory's name. The collision that
   buys -- two directories with the same name in different parents -- is
   refused rather than silently resolved.
3. **``session.json`` is field-for-field the hosted record**, and
   ``capella_service``'s own readers find the model through it. If this
   drifts, every tool downstream reads a record it half understands.
4. **The agent never clones.** ``open_model`` on a model that is not set up
   reports it and leaves the disk alone; ``prepare`` is the only thing that
   clones, and it is the setup page's.
5. **Divergence is reported, never replayed.** This is the one judgement that
   differs from the notebook's, and the test proves both halves: that nothing
   moved, and that the report names the model file rather than relaying a git
   error.
6. **A stale clone refuses a write.** The measured half of the ceiling, and
   the Capella-specific one -- a model is a single file, so patching a stale
   copy and pushing it reverts whatever landed in between without ever
   conflicting.
7. **Closing a session never deletes the clone.** The hosted
   ``cleanup_session`` removes its temp directory, which is right there and
   would be data destruction here.
8. **The credential reaches neither ``session.json`` nor any response.**
"""

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, r"c:\apps\Git_VSCode\Capella_Fabric_Generator")

import capella_service as svc
import git_service

from capella_bundle import config, settings, session
from capella_bundle.config import Dependency, ModelConfig

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if ok else 'FAIL'} {label}" + (f": {detail}" if detail and not ok else ""))
    if not ok:
        failures.append(label)


def raises(label: str, fn, *, saying: str = "") -> None:
    try:
        fn()
    except Exception as exc:
        if saying and saying.lower() not in str(exc).lower():
            check(label, False, f"raised but did not say {saying!r}: {exc}")
        else:
            check(label, True)
        return
    check(label, False, "did not raise")


ROOT = Path(tempfile.mkdtemp(prefix="bundle_session_"))
settings.default_state_root = lambda: ROOT
config.default_state_root = lambda: ROOT


def git(repo: Path, *args: str) -> str:
    """Plain git for the fixtures, deliberately not the code under test.

    If a defect in the library's ``run_git`` ever made a command a no-op,
    fixtures built with it would stop setting up the situation while every
    assertion still passed.
    """
    done = subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True)
    assert done.returncode == 0, f"git {' '.join(args)}: {done.stderr}"
    return done.stdout.strip()


def origin(name: str, *, aird: bool = True) -> str:
    """A bare repository with a Capella-shaped model in it, as a file:// URI."""
    work = ROOT / "fixtures" / name
    (work / "model").mkdir(parents=True)
    if aird:
        (work / "model" / f"{name}.aird").write_text("<aird/>", encoding="utf-8")
        (work / "model" / f"{name}.capella").write_text("<capella/>", encoding="utf-8")
    else:
        (work / "model" / "README.md").write_text("no model here\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", "-b", "main", str(work)],
                   check=True, capture_output=True)
    git(work, "add", "-A")
    git(work, "-c", "user.email=f@x", "-c", "user.name=f", "commit", "-qm", "init")
    bare = ROOT / "fixtures" / f"{name}.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(work), str(bare)],
                   check=True, capture_output=True)
    return bare.as_uri()


def cfg(name: str, uri: str, **kw) -> ModelConfig:
    return ModelConfig(name=name, remote_url=uri, branch="main",
                       pat=kw.pop("pat", ""), access=kw.pop("access", "write"),
                       model_dir=kw.pop("model_dir", ROOT / "models" / name),
                       views_dir=kw.pop("views_dir", ROOT / "views" / name), **kw)


MAIN = origin("Residential_AC")

# ---------------------------------------------------------------------------
print("1. The adapter replaces _session_dir, and falls through otherwise")
# ---------------------------------------------------------------------------

# The control first: before anything is registered, an id resolves exactly as
# the hosted server resolves it. Importing this package must not change how
# capella_service behaves for somebody else's session.
check("an unregistered id resolves the hosted way",
      session._session_dir("whatever") == session._ORIGINAL_SESSION_DIR("whatever"),
      str(session._session_dir("whatever")))
check("...which is under the system temp directory",
      "capella_fabric" in str(session._ORIGINAL_SESSION_DIR("whatever")),
      str(session._ORIGINAL_SESSION_DIR("whatever")))

main = cfg("Residential_AC", MAIN)
built = session.prepare(main)
sid = built["session_id"]

check("the session id is the model directory's name", sid == "Residential_AC", sid)
check("capella_service now resolves it to the model directory",
      svc._session_dir(sid) == main.model_dir, str(svc._session_dir(sid)))
check("**git_service sees the same function**, because it looks it up on the module",
      git_service.svc._session_dir is session._session_dir)
check("...and git_service's svc is the module we patched", git_service.svc is svc)
check("an unknown id still falls through after the patch",
      svc._session_dir("not-ours") == session._ORIGINAL_SESSION_DIR("not-ours"))
check("the clone is where the service expects it",
      (main.model_dir / "unpacked" / ".git").is_dir(), str(main.clone_dir))
check("capella_service.find_aird_file finds the model through the adapter",
      svc.find_aird_file(sid) is not None and
      svc.find_aird_file(sid).suffix == ".aird", str(svc.find_aird_file(sid)))

# ---------------------------------------------------------------------------
print("\n2. The id is derived, so a restart hands back the same one")
# ---------------------------------------------------------------------------

check("a fresh config object derives the same id",
      session.session_id_for(cfg("Residential_AC", MAIN)) == sid)
check("...and opening again returns it", session.open_model(main)["session_id"] == sid)

clash = cfg("Other", MAIN, model_dir=ROOT / "elsewhere" / "Residential_AC",
            views_dir=ROOT / "views" / "Other")
try:
    session._register(clash)
    collision = ""
except Exception as exc:
    collision = str(exc)
check("two directories with one name are refused, not silently merged",
      bool(collision), "no refusal")
check("...and the refusal names both directories",
      str(main.model_dir) in collision and str(clash.model_dir) in collision,
      collision)
check("...and says what would happen otherwise",
      "wrong clone" in collision, collision)
check("the first model's registration is untouched",
      session.registered_dirs()[sid] == main.model_dir,
      str(session.registered_dirs().get(sid)))

# ---------------------------------------------------------------------------
print("\n3. session.json is the hosted record, field for field")
# ---------------------------------------------------------------------------

HOSTED_FIELDS = {"session_id", "archive_name", "aird_path", "resolved_uuids",
                 "include_realized", "include_realizing", "yaml_path",
                 "resources", "oauth_connection_id"}
record = json.loads((main.model_dir / "session.json").read_text(encoding="utf-8"))
check("every field the hosted server writes is present",
      HOSTED_FIELDS <= set(record), str(sorted(set(record) ^ HOSTED_FIELDS)))
check("and no extra field was invented",
      set(record) == HOSTED_FIELDS, str(sorted(set(record) - HOSTED_FIELDS)))
check("capella_service.load_session reads it", svc.load_session(sid)["session_id"] == sid)
check("the aird path in it exists", Path(record["aird_path"]).is_file())
check("oauth_connection_id is present and empty, since there is no database",
      record["oauth_connection_id"] is None)
check("the realization flags came from the model's own configuration",
      record["include_realized"] is False and record["include_realizing"] is False)

flagged = session.prepare(cfg("Flags", origin("Flags"), include_realized=True,
                              include_realizing=True))
flags = json.loads((ROOT / "models" / "Flags" / "session.json").read_text(encoding="utf-8"))
check("...and follow the configuration when it is set",
      flags["include_realized"] is True and flags["include_realizing"] is True,
      str(flags))

# ---------------------------------------------------------------------------
print("\n4. The agent never clones")
# ---------------------------------------------------------------------------

unset = cfg("NotSetUp", origin("NotSetUp"))
raises("open_model on a model that is not set up is refused",
       lambda: session.open_model(unset), saying="--setup")
check("**and nothing was cloned**", not unset.clone_dir.exists(), str(unset.clone_dir))
check("is_prepared says so too", session.is_prepared(unset) is False)
check("prepare is idempotent -- a prepared model is just opened",
      session.prepare(main)["first_use"] is False)

# A clone of a different repository in this model's directory must not be
# touched: two models sharing one directory would overwrite each other's .aird.
other = origin("Different")
wrong = cfg("Residential_AC", other, model_dir=main.model_dir,
            views_dir=ROOT / "views" / "w")
raises("a directory holding another repository is refused",
       lambda: session.open_model(wrong), saying="Refusing to touch it")

# ---------------------------------------------------------------------------
print("\n5. Divergence is reported, never replayed")
# ---------------------------------------------------------------------------

theirs = ROOT / "theirs"
subprocess.run(["git", "clone", "-q", MAIN, str(theirs)], check=True, capture_output=True)
(theirs / "model" / "Residential_AC.aird").write_text("<aird theirs='1'/>", encoding="utf-8")
git(theirs, "add", "-A")
git(theirs, "-c", "user.email=t@x", "-c", "user.name=t", "commit", "-qm", "their edit")
git(theirs, "push", "-q", "origin", "main")

(main.clone_dir / "model" / "Residential_AC.aird").write_text(
    "<aird ours='1'/>", encoding="utf-8")
git(main.clone_dir, "add", "-A")
git(main.clone_dir, "-c", "user.email=o@x", "-c", "user.name=o", "commit", "-qm", "our edit")
before = git(main.clone_dir, "rev-parse", "HEAD")

opened = session.open_model(main)
check("reconciling is off for this product", session.RECONCILE is False)
check("the divergence is reported", opened.get("diverged") is True, str(opened))
check("...as not synced", opened.get("synced") is False)
check("**nothing was replayed**",
      git(main.clone_dir, "rev-parse", "HEAD") == before)
check("...so our own edit is still on disk",
      "ours" in (main.clone_dir / "model" / "Residential_AC.aird").read_text(encoding="utf-8"))
check("the report says it does not reconcile on its own",
      "does not reconcile" in opened.get("sync_note", ""), opened.get("sync_note", ""))
check("**...and names the Capella model as the file that disagrees**",
      "Capella" in opened.get("sync_note", ""), opened.get("sync_note", ""))
check("...and tells the agent nothing was changed",
      "Nothing has been changed" in opened.get("sync_note", ""), opened.get("sync_note", ""))
check("the unpushed commit is still counted", opened.get("ahead") == 1, str(opened))

# ---------------------------------------------------------------------------
print("\n6. A diverged or stale clone refuses a write")
# ---------------------------------------------------------------------------

check("the open carried the refusal with it",
      "write_refusal" in opened, str(sorted(opened)))
check("...which says a person is needed",
      "needs a person" in opened.get("write_refusal", ""),
      opened.get("write_refusal", ""))
check("...and names the clone to open", str(main.clone_dir)
      in opened.get("write_refusal", ""), opened.get("write_refusal", ""))

# Reset to the remote and confirm the refusal lifts -- a refusal that never
# lifts is indistinguishable from a broken product.
git(main.clone_dir, "reset", "-q", "--hard", "origin/main")
clean = session.open_model(main)
check("after resetting, the clone is synced", clean.get("synced") is True, str(clean))
check("**and the write refusal is gone**", "write_refusal" not in clean, str(sorted(clean)))

# Behind only: the remote moved, we did not. That fast-forwards, so it is not
# a refusal either -- the refusal is for a clone that is behind and could not
# be brought forward.
(theirs / "model" / "Residential_AC.aird").write_text("<aird theirs='2'/>", encoding="utf-8")
git(theirs, "add", "-A")
git(theirs, "-c", "user.email=t@x", "-c", "user.name=t", "commit", "-qm", "their second")
git(theirs, "push", "-q", "origin", "main")
caught_up = session.open_model(main)
check("a remote-only change fast-forwards", caught_up.get("synced") is True, str(caught_up))
check("...and is reported as a fast-forward",
      any("fast-forward" in d for d in caught_up.get("sync_detail", [])),
      str(caught_up.get("sync_detail")))
check("...with no write refusal", "write_refusal" not in caught_up)

check("a read-only model refuses a write even when perfectly synced",
      bool(cfg("ro", MAIN, access="read").write_refusal(caught_up)))

# ---------------------------------------------------------------------------
print("\n7. A library repository is cloned by prepare, not by a tool")
# ---------------------------------------------------------------------------

LIB = origin("Brake_Lib")
withlib = cfg("WithLib", origin("WithLib"),
              dependencies=(Dependency(resource_name="Brake Library",
                                       remote_url=LIB, branch="main"),))
libbuilt = session.prepare(withlib)
check("the library was cloned", "dependencies_cloned" in libbuilt, str(sorted(libbuilt)))
check("...under the model directory, where the service looks",
      (withlib.model_dir / "deps" / "Brake_Library" / ".git").is_dir())
check("resources points at the folder holding its .aird",
      list(withlib.resources()) == ["Brake Library"], str(withlib.resources()))
check("...and that folder really holds one",
      any(Path(withlib.resources()["Brake Library"]["path"]).glob("*.aird")))
check("the record carries resources for capella_service.open_model",
      list(svc.load_session(libbuilt["session_id"])["resources"]) == ["Brake Library"],
      str(svc.load_session(libbuilt["session_id"])["resources"]))
check("nothing is reported missing", not withlib.missing_dependencies())

# A library that is declared but absent is reported at open with the action,
# rather than surfacing later as capellambse failing on the main model.
ghost = cfg("Ghost", origin("Ghost"))
session.prepare(ghost)
haunted = cfg("Ghost", ghost.remote_url,
              dependencies=(Dependency(resource_name="Missing Lib",
                                       remote_url=LIB),))
note = session.open_model(haunted).get("dependencies_note", "")
check("a declared library with nothing on disk is reported at open", bool(note))
check("...by name", "Missing Lib" in note, note)
check("...with the page that fixes it", "setup page" in note, note)

no_model = cfg("Empty", origin("Empty", aird=False))
try:
    session.prepare(no_model)
    empty_said = ""
except Exception as exc:
    empty_said = str(exc)
check("a repository with no .aird is refused",
      "no .aird file was found" in empty_said, empty_said)
check("**but the clone is kept**, unlike the hosted server's temp directory",
      no_model.clone_dir.is_dir(), str(no_model.clone_dir))
check("...and the message says so, so the page can offer to switch branch",
      "has been kept" in empty_said, empty_said)
check("...and names the branch, which is the likely cause",
      "'main'" in empty_said, empty_said)

# ---------------------------------------------------------------------------
print("\n8. Closing a session keeps the model; the credential never leaks")
# ---------------------------------------------------------------------------

closed = session.forget(sid)
check("the session is closed", closed["status"] == "closed", str(closed))
check("**the clone is still on disk**", main.clone_dir.is_dir())
check("...and the response says where", closed["model_kept_at"] == str(main.model_dir))
check("...and that it is not a cache", "not a cache" in closed["note"])
check("the registration is gone", sid not in session.registered_dirs())
raises("and the session id is then unknown, with the cheap fix named",
       lambda: session.get_session(sid), saying="costs nothing")
check("closing an unknown session is not an error",
      session.forget("never-existed")["status"] == "no such session")

SECRET = "ghp_SESSIONMUSTNOTLEAKTHISVALUE000000"

# Prepared without a credential, because a file:// remote with one goes down
# the probe path and may legitimately refuse -- which would leave the two
# assertions below silently skipped, and a check that skips itself is the same
# shape as a check that passes for free. So the clone is made credential-free
# and the credential is then attached to the config, which is exactly the
# state every open is in: the token comes from the store at to_config time and
# is on the ModelConfig for the whole call.
tokened_dir = ROOT / "models" / "Tokened"
TOKENED = origin("Tokened")
session.prepare(cfg("Tokened", TOKENED, model_dir=tokened_dir))
tokened = cfg("Tokened", TOKENED, model_dir=tokened_dir, pat=SECRET)
check("the config really is carrying the token", tokened.pat == SECRET)

record = session.write_session_record(
    tokened, session.session_id_for(tokened), session.find_aird(tokened.clone_dir))
check("**session.json does not contain the token**",
      SECRET not in (tokened.model_dir / "session.json").read_text(encoding="utf-8"))
check("...nor does the record that was returned", SECRET not in json.dumps(record))
check("**nor does what open_model reports**",
      SECRET not in json.dumps(session.open_model(tokened), default=str))
check("...nor describe()", SECRET not in json.dumps(tokened.describe(), default=str))

shutil.rmtree(ROOT, ignore_errors=True)

print()
if failures:
    print(f"{len(failures)} CHECK(S) FAILED")
    for name in failures:
        print(f"  - {name}")
    sys.exit(1)
print("bundle session: all checks passed")
