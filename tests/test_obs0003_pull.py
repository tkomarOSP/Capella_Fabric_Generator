"""OBS-0003: two people take turns on one Capella model without re-authorizing.

Tony edits in Capella Studio and pushes; the agent picks it up, patches, and
pushes back. Before this, the agent's only way to see a push was a brand new
clone -- on the credential-free path, a brand new browser authorization every
turn.

Acted out with real git: a bare repo plays GitHub, a second clone plays Tony in
Capella Studio, and a real capella-fabric session plays the agent. Every call
goes through the actual tool functions.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import warnings

_VENV_PY = r"c:\apps\Git_VSCode\Capella_Fabric_Generator\.venv\Scripts\python.exe"
if os.path.exists(_VENV_PY) and os.path.normcase(sys.executable) != os.path.normcase(_VENV_PY):
    sys.exit(subprocess.run([_VENV_PY, __file__]).returncode)

warnings.filterwarnings("ignore")
os.environ["CAPELLA_TOOLS_PATH"] = r"c:\apps\Git_VSCode\Capella_Tools"
sys.path.insert(0, r"c:\apps\Git_VSCode\Capella_Tools")
sys.path.insert(0, r"c:\apps\Git_VSCode\se-knowledge-partner\kp")
sys.path.insert(0, r"c:\apps\Git_VSCode\Capella_Fabric_Generator")

from pathlib import Path

import capella_service as svc
import git_service as gs
import mcp_server as ms

gs._inject_pat = lambda url, pat: url  # file:// remotes carry no credential

_fn = lambda t: getattr(t, "fn", t)
pull = _fn(ms.pull_model_changes)
push = _fn(ms.push_model_changes)

tmp = Path(tempfile.mkdtemp())


def git(*args, cwd):
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    assert r.returncode == 0, f"git {' '.join(args)}: {r.stderr}"
    return r.stdout.strip()


# ---- GitHub (bare) and Tony's Capella Studio checkout ----------------------
remote = tmp / "remote.git"
git("init", "--bare", "-b", "master", str(remote), cwd=tmp)
tony = tmp / "tony"
git("clone", str(remote), str(tony), cwd=tmp)
git("config", "user.name", "Tony", cwd=tony)
git("config", "user.email", "tony@example.com", cwd=tony)
(tony / "model").mkdir()
(tony / "model" / "AfterTreatment.aird").write_text("<aird v1/>")
(tony / "model" / "AfterTreatment.capella").write_text("<capella v1/>")
git("add", "-A", cwd=tony)
git("commit", "-m", "Initial model", cwd=tony)
git("push", "origin", "master", cwd=tony)


def tony_commits(message, content, filename="AfterTreatment.capella"):
    (tony / "model" / filename).write_text(content)
    git("add", "-A", cwd=tony)
    git("commit", "-m", message, cwd=tony)
    git("push", "origin", "master", cwd=tony)


# ---- the agent's capella-fabric session -------------------------------------
sid = svc.create_session()
gs.clone_repo(remote.as_uri(), "", sid, branch="master")
unpacked = svc._session_dir(sid) / "unpacked"
svc.save_session(sid, {
    "session_id": sid, "archive_name": "remote",
    "aird_path": str(unpacked / "model" / "AfterTreatment.aird"),
    "resolved_uuids": [], "include_realized": False, "include_realizing": False,
    "yaml_path": None, "resources": {}, "oauth_connection_id": None,
})
agent_file = unpacked / "model" / "AfterTreatment.capella"


def agent_commits(message, content):
    agent_file.write_text(content)
    gs.commit_changes(sid, message, author_name="Cousin", author_email="cousin@example.com")


# ===== 1. Nothing new =====
r = pull(sid)
assert r["status"] == "up_to_date", r
print("nothing new:        up_to_date")

# ===== 2. Tony pushes; the agent picks it up in the SAME session =====
tony_commits("Rename Transfer Exhaist", "<capella v2 tony/>")
r = pull(sid)
assert r["status"] == "pulled", r
assert len(r["incoming"]) == 1
assert r["incoming"][0]["author"] == "Tony"
assert r["incoming"][0]["message"] == "Rename Transfer Exhaist"
assert agent_file.read_text() == "<capella v2 tony/>", "the model on disk must be Tony's version"
assert "re-browse" in r["message"].lower(), "must warn that UUIDs may have changed"
print("Tony pushes:        pulled into the existing session -- author and message reported")

# ===== 3. The agent patches and pushes back =====
agent_commits("Allocate Ambient Air", "<capella v3 cousin/>")
r = pull(sid)
assert r["status"] == "up_to_date" and len(r["local"]) == 1, r
assert "not yet pushed" in r["message"]
r = push(sid)
assert r["status"] == "ok", r
git("pull", "origin", "master", cwd=tony)
assert (tony / "model" / "AfterTreatment.capella").read_text() == "<capella v3 cousin/>"
print("agent pushes back:  Tony receives it; pull noted the unpushed commit first")

# ===== 4. Both changed: stop, change nothing, explain =====
tony_commits("Tony's concurrent edit", "<capella v4 tony/>")
agent_commits("Cousin's concurrent edit", "<capella v4 cousin/>")
head_before = git("rev-parse", "HEAD", cwd=unpacked)
r = pull(sid)
assert r["status"] == "diverged", r
assert len(r["incoming"]) == 1 and len(r["local"]) == 1
assert git("rev-parse", "HEAD", cwd=unpacked) == head_before, "a diverged pull must change nothing"
assert agent_file.read_text() == "<capella v4 cousin/>", "the agent's work must still be on disk"
assert "Nothing was changed" in r["message"] and "discard_local_changes=True" in r["message"]
print("both changed:       diverged -- nothing touched, both sides listed, the way out named")

# ===== 5. Push while diverged points at pull, not a re-clone =====
r = push(sid)
assert r["status"] == "error", r
assert "pull_model_changes" in r["message"], r["message"]
assert "Re-clone" not in r["message"], "the old advice cost an authorization"
print("push while behind:  rejected, and told to pull -- not to re-clone")

# ===== 6. Explicitly taking Tony's version =====
r = pull(sid, discard_local_changes=True)
assert r["status"] == "pulled", r
assert [c["message"] for c in r["discarded"]] == ["Cousin's concurrent edit"], r["discarded"]
assert agent_file.read_text() == "<capella v4 tony/>"
assert git("rev-parse", "HEAD", cwd=unpacked) == git("rev-parse", "HEAD", cwd=tony)
print("take remote:        reset to Tony's version, discarded commit named for reapplying")

# ===== 7. Opting in never discards anything when there's nothing to discard =====
tony_commits("Another Tony edit", "<capella v5 tony/>")
r = pull(sid, discard_local_changes=True)
assert r["status"] == "pulled" and "discarded" not in r, r
print("discard, no local:  plain fast-forward, nothing reported as discarded")

# ===== 8. The model file moves on the remote =====
git("mv", "model/AfterTreatment.aird", "model/ATS.aird", cwd=tony)
git("commit", "-m", "Rename model file", cwd=tony)
git("push", "origin", "master", cwd=tony)
r = pull(sid)
assert r["status"] == "pulled", r
assert "moved" in r.get("warning", ""), r
assert svc.load_session(sid)["aird_path"].endswith("ATS.aird")
print("model file renamed: session repointed at the new .aird")

# ===== 9. Use keeps a session alive =====
# cleanup_sessions.sh removes session dirs whose mtime is over 4 hours old.
d = svc._session_dir(sid)
five_hours_ago = time.time() - 5 * 3600
os.utime(d, (five_hours_ago, five_hours_ago))
assert time.time() - os.stat(d).st_mtime > 4 * 3600, "precondition: looks stale to the sweep"
svc.load_session(sid)
assert time.time() - os.stat(d).st_mtime < 60, "using the session must reset the sweep clock"
print("sweep clock:        any tool use resets it -- swept after 4h idle, not 4h after creation")

# ===== 10. No credential leaks into error messages =====
scrubbed = ms._scrub_credential("fatal: unable to access 'https://oauth2:ghp_SECRET123@github.com/x/y'")
assert "ghp_SECRET123" not in scrubbed and "github.com/x/y" in scrubbed, scrubbed
print("errors:             embedded credentials stripped")

svc.cleanup_session(sid)
shutil.rmtree(tmp, ignore_errors=True)
print("\nALL OBS-0003 PULL CHECKS PASSED")
