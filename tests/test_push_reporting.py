"""OBS-0002: push_changes must never leak a raw PushInfo repr, and must say
something actionable. Driven against real local git repos -- a bare repo as
origin, a real clone, and a genuine non-fast-forward divergence."""
import os, subprocess, sys, tempfile
from pathlib import Path

tmp = Path(tempfile.mkdtemp())
os.environ["CAPELLA_SESSION_ROOT"] = str(tmp / "sessions")
sys.path.insert(0, r"c:\apps\Git_VSCode\Capella_Fabric_Generator")

import capella_service as svc
import git_service as gs


def run(*args, cwd=None):
    subprocess.run(args, cwd=cwd, check=True, capture_output=True)


# --- a bare repo playing the role of GitHub -----------------------------
bare = tmp / "origin.git"
run("git", "init", "--bare", "-b", "main", str(bare))

seed = tmp / "seed"
run("git", "init", "-b", "main", str(seed))
(seed / "model.txt").write_text("v1", encoding="utf-8")
run("git", "-C", str(seed), "add", "-A")
run("git", "-C", str(seed), "-c", "user.email=t@e.com", "-c", "user.name=t", "commit", "-m", "init")
run("git", "-C", str(seed), "remote", "add", "origin", bare.as_uri())
run("git", "-C", str(seed), "push", "-u", "origin", "main")

# --- a session clone, the way clone_capella_repo makes one --------------
sid = svc.create_session()
clone_dir = svc._session_dir(sid) / "unpacked"
clone_dir.mkdir(parents=True, exist_ok=True)
run("git", "clone", bare.as_uri(), str(clone_dir))
run("git", "-C", str(clone_dir), "config", "user.email", "t@e.com")
run("git", "-C", str(clone_dir), "config", "user.name", "t")

FORBIDDEN = "git.remote.PushInfo object at"


def check(res, *, status):
    assert res.get("status") == status, res
    blob = repr(res)
    assert FORBIDDEN not in blob, f"raw PushInfo repr leaked: {res}"
    assert res.get("message"), f"no message at all: {res}"
    return res


# --- 1. clean push ------------------------------------------------------
(clone_dir / "model.txt").write_text("v2", encoding="utf-8")
run("git", "-C", str(clone_dir), "add", "-A")
run("git", "-C", str(clone_dir), "commit", "-m", "change")
r = check(gs.push_changes(sid), status="ok")
assert "Pushed to" in r["message"], r
print("clean push:      ", r["message"])

# --- 2. up-to-date no-op -----------------------------------------------
r = check(gs.push_changes(sid), status="ok")
assert "up to date" in r["message"].lower(), r
assert "nothing was pushed" in r["message"], r
print("up-to-date no-op:", r["message"])

# --- 3. genuine non-fast-forward ---------------------------------------
# Someone else pushes to origin, then this session commits on its stale base.
other = tmp / "other"
run("git", "clone", bare.as_uri(), str(other))
run("git", "-C", str(other), "config", "user.email", "o@e.com")
run("git", "-C", str(other), "config", "user.name", "o")
(other / "model.txt").write_text("theirs", encoding="utf-8")
run("git", "-C", str(other), "add", "-A")
run("git", "-C", str(other), "commit", "-m", "their change")
run("git", "-C", str(other), "push")

(clone_dir / "model.txt").write_text("mine", encoding="utf-8")
run("git", "-C", str(clone_dir), "add", "-A")
run("git", "-C", str(clone_dir), "commit", "-m", "my diverged change")
r = check(gs.push_changes(sid), status="error")
msg = r["message"]
assert "rejected" in msg.lower(), msg
# The remedy changed with OBS-0003's pull: it used to say "Re-clone", which on
# the credential-free path cost a new browser authorization. It now points at
# pull_model_changes, which picks up the remote's commits in the same session.
assert "pull_model_changes" in msg, f"the actionable remedy is missing: {msg}"
assert "Re-clone" not in msg, "the old remedy costs an authorization -- it must not come back"
print("non-fast-forward:", msg)

# --- 4. the exact OBS-0002 regression ----------------------------------
# Whatever happens, the caller must never receive a bare object repr.
for res in (gs.push_changes(sid),):
    assert FORBIDDEN not in repr(res)
print("regression:       no PushInfo repr on a repeated failure")

print("\nALL PUSH-REPORTING CHECKS PASSED")
