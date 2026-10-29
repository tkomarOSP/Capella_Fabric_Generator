"""note-0086 Parts 2/3: capella-fabric's credential-free path, against a real
seeded temp DB and real local git repos as clone targets."""
import os, sys, subprocess, tempfile
from pathlib import Path
from datetime import timedelta

tmp = tempfile.mkdtemp()
os.environ["CARTENZA_DB_PATH"] = os.path.join(tmp, "test.db")
from cryptography.fernet import Fernet
os.environ["CARTENZA_CONNECT_SECRET_KEY"] = Fernet.generate_key().decode()
os.environ["CARTENZA_SECRET_KEY"] = "test-secret-not-real-0123456789abcdef"
os.environ["KP_CONNECT_BASE_URL"] = "https://dev.connect.cartenza.ai"
os.environ["CAPELLA_SESSION_ROOT"] = os.path.join(tmp, "sessions")

sys.path.insert(0, r"c:\apps\Git_VSCode\se-knowledge-partner\kp")
sys.path.insert(0, r"c:\apps\Git_VSCode\Capella_Fabric_Generator")


def make_repo(name):
    d = Path(tmp) / name
    (d / "model").mkdir(parents=True)
    (d / "model" / f"{name}.aird").write_text("<aird/>", encoding="utf-8")
    subprocess.run(["git", "init", "-b", "main", str(d)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(d), "add", "-A"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(d), "-c", "user.email=t@e.com", "-c", "user.name=t",
                    "commit", "-m", "init"], check=True, capture_output=True)
    return d.as_uri()


MAIN = make_repo("MainModel")
DEP = make_repo("LibModel")

from auth.db import Base, engine, SessionLocal
import auth.models as m
Base.metadata.create_all(engine)
from auth.secrets import encrypt_pat
from auth.connect import utcnow

db = SessionLocal()
db.add_all([
    m.User(id="owner", email="owner@example.com"),
    m.Agent(id="a1", owner_user_id="owner", name="Test Agent"),
    m.AgentScope(id="s1", agent_id="a1", remote_url=MAIN, branch="main", permission="write"),
    m.OAuthConnection(id="oc1", user_id="owner", provider="github",
                      access_token_encrypted=encrypt_pat("gho_live_token")),
])
db.commit(); db.close()

import mcp_server as ms
import git_service as gs
gs._inject_pat = lambda url, pat: url  # file:// remotes take no credential

assert ms._AUTH_AVAILABLE, "kp-auth must be importable for this test"
_unwrap = lambda t: getattr(t, "fn", t)
clone = _unwrap(ms.clone_capella_repo)
begin = _unwrap(ms.begin_connect)
add_dep = _unwrap(ms.add_dependency_repo)

# --- begin_connect scope enforcement -----------------------------------
bad = begin("a1", "https://github.com/x/not-scoped", "main")
assert "error" in bad and "no registered scope" in bad["error"]
print("begin_connect: out-of-scope repo rejected")

out = begin("a1", MAIN, "main")
assert "error" not in out, out
assert out["connect_url"].startswith("https://dev.connect.cartenza.ai/connect?code=")
code = out["connect_url"].split("code=")[1]
print("begin_connect: issues a plain non-secret URL ->", out["connect_url"][:60] + "...")

# --- clone with an unfulfilled code ------------------------------------
r = clone(connect_code=code)
assert "error" in r and "hasn't been connected yet" in r["error"]
print("clone: unfulfilled code refused")

# --- argument validation -----------------------------------------------
assert "error" in clone(), "no args must be refused"
assert "error" in clone(repo_url=MAIN), "repo_url without pat must be refused"
print("clone: requires either (repo_url+github_pat) or connect_code")

# --- human fulfils via Connect via GitHub -----------------------------
db = SessionLocal()
row = db.query(m.ConnectCode).filter(m.ConnectCode.code == code).first()
row.oauth_connection_id = "oc1"
row.connected_at = utcnow()
row.user_id = "owner"
db.commit(); db.close()

r = clone(connect_code=code)
assert "error" not in r, r
sid = r["session_id"]
assert r["aird_file"] == "MainModel.aird"
print("clone: OAuth-fulfilled code clones with no PAT in any argument")

db = SessionLocal()
burned = db.query(m.ConnectCode).filter(m.ConnectCode.code == code).first()
assert burned.consumed_at is not None, "code must be burned after a successful clone"
db.close()
print("clone: code burned only after the clone and .aird discovery succeeded")

sess = __import__("capella_service").load_session(sid)
assert sess["oauth_connection_id"] == "oc1"
print("clone: session.json carries the connection id (not a secret)")

# --- dependency reuse, no credential -----------------------------------
d = add_dep(session_id=sid, repo_url=DEP, resource_name="Lib")
assert "error" not in d, d
assert d["resource_name"] == "Lib" and d["aird_file"] == "LibModel.aird"
print("add_dependency_repo: reuses the session's GitHub authorization, no github_pat")

# --- burn ordering: a failing clone must not consume --------------------
out2 = begin("a1", MAIN, "main")
code2 = out2["code"] if "code" in out2 else out2["connect_url"].split("code=")[1]
db = SessionLocal()
row = db.query(m.ConnectCode).filter(m.ConnectCode.code == code2).first()
row.oauth_connection_id = "oc1"; row.connected_at = utcnow()
row.remote_url = "file:///definitely/not/a/repo"
db.commit(); db.close()
r = clone(connect_code=code2)
assert "error" in r, "clone of a bogus remote must fail"
db = SessionLocal()
assert db.query(m.ConnectCode).filter(m.ConnectCode.code == code2).first().consumed_at is None
db.close()
print("clone: a failed clone does NOT spend the human's one authorization")

# --- PAT-fulfilled session: dependency asks for its own credential ------
r = clone(repo_url=MAIN, github_pat="ghp_direct")
assert "error" not in r, r
sid2 = r["session_id"]
assert __import__("capella_service").load_session(sid2)["oauth_connection_id"] is None
d = add_dep(session_id=sid2, repo_url=DEP, resource_name="Lib")
assert "error" in d and "no reusable GitHub authorization" in d["error"], d
d = add_dep(session_id=sid2, repo_url=DEP, resource_name="Lib", github_pat="ghp_direct")
assert "error" not in d, d
print("add_dependency_repo: PAT sessions get a clear message, and github_pat still works")

# --- audience isolation -------------------------------------------------
from auth.connect import redeem_connect_code
out3 = begin("a1", MAIN, "main")
c3 = out3["connect_url"].split("code=")[1]
db = SessionLocal()
row = db.query(m.ConnectCode).filter(m.ConnectCode.code == c3).first()
row.oauth_connection_id = "oc1"; row.connected_at = utcnow()
db.commit(); db.close()
assert "issued for 'capella'" in redeem_connect_code(c3, audience="knowledge_repo")["error"]
print("audience: a capella-minted code is refused by the knowledge_repo server")

print("\nALL NOTE-0086 CAPELLA CHECKS PASSED")

# --- OBS-0003: a failed clone must SAY the code is still reusable --------
out4 = begin("a1", MAIN, "main")
c4 = out4["connect_url"].split("code=")[1]
db = SessionLocal()
row = db.query(m.ConnectCode).filter(m.ConnectCode.code == c4).first()
row.oauth_connection_id = "oc1"; row.connected_at = utcnow()
row.remote_url = "file:///no/such/repo"      # clone will fail
db.commit(); db.close()

r = clone(connect_code=c4)
assert "error" in r
assert "NOT consumed" in r["error"], f"failure must state the code survives: {r['error']}"
assert "same code" in r["error"], r["error"]
print("clone failure: error tells the caller to retry with the same code")

# ...and that advice must actually be true: repoint at a real repo and retry.
db = SessionLocal()
db.query(m.ConnectCode).filter(m.ConnectCode.code == c4).first().remote_url = MAIN
db.commit(); db.close()
r = clone(connect_code=c4)
assert "error" not in r, r
print("clone retry:   the same code then works, exactly as the message promised")

# --- no-.aird failure carries the same hint -----------------------------
import subprocess as _sp
empty = Path(tmp) / "NoAird"
empty.mkdir()
(empty / "readme.md").write_text("no model here", encoding="utf-8")
_sp.run(["git", "init", "-b", "main", str(empty)], check=True, capture_output=True)
_sp.run(["git", "-C", str(empty), "add", "-A"], check=True, capture_output=True)
_sp.run(["git", "-C", str(empty), "-c", "user.email=t@e.com", "-c", "user.name=t",
         "commit", "-m", "init"], check=True, capture_output=True)

out5 = begin("a1", MAIN, "main")
c5 = out5["connect_url"].split("code=")[1]
db = SessionLocal()
row = db.query(m.ConnectCode).filter(m.ConnectCode.code == c5).first()
row.oauth_connection_id = "oc1"; row.connected_at = utcnow()
row.remote_url = empty.as_uri()
db.commit(); db.close()

r = clone(connect_code=c5)
assert "error" in r and "No .aird" in r["error"], r
assert "NOT consumed" in r["error"], r["error"]
db = SessionLocal()
assert db.query(m.ConnectCode).filter(m.ConnectCode.code == c5).first().consumed_at is None
db.close()
print("no-.aird:      same hint, and the code really is still unconsumed")

# --- the PAT path must NOT claim anything about connect codes -----------
r = clone(repo_url="file:///no/such/repo", github_pat="ghp_x")
assert "error" in r and "connect_code" not in r["error"], r
print("PAT path:      failure message says nothing about connect codes")

print("\nALL OBS-0003 MESSAGING CHECKS PASSED")

# --- note-0093: push re-resolves an expired OAuth token -----------------
# clone_capella_repo bakes the credential into origin. GitHub's access tokens
# last 8 hours, so a session that outlives one used to fail at push -- after the
# model edits were already committed locally, the most expensive moment to fail.
push = _unwrap(ms.push_model_changes)

# A bare remote so a push can actually land, reached through the connect flow.
bare = Path(tmp) / "pushable.git"
_sp.run(["git", "init", "--bare", "-b", "main", str(bare)], check=True, capture_output=True)
_sp.run(["git", "-C", str(Path(tmp) / "MainModel"), "push", bare.as_uri(), "main"],
        check=True, capture_output=True)

db = SessionLocal()
db.add(m.AgentScope(id="s-push", agent_id="a1", remote_url=bare.as_uri(),
                    branch="main", permission="write"))
# An expired connection whose refresh token the stub below will honour.
db.add(m.OAuthConnection(id="oc-exp", user_id="owner", provider="github",
                         access_token_encrypted=encrypt_pat("gho_expired"),
                         refresh_token_encrypted=encrypt_pat("refresh-1"),
                         expires_at=utcnow() - timedelta(minutes=1)))
db.commit(); db.close()

out = begin("a1", bare.as_uri(), "main")
cpush = out["connect_url"].split("code=")[1]
db = SessionLocal()
row = db.query(m.ConnectCode).filter(m.ConnectCode.code == cpush).first()
row.oauth_connection_id = "oc-exp"; row.connected_at = utcnow()
db.commit(); db.close()

os.environ["GITHUB_OAUTH_CLIENT_ID"] = "cid"
os.environ["GITHUB_OAUTH_CLIENT_SECRET"] = "csecret"
import httpx
_real_post = httpx.post
refreshes = []


class _R:
    def __init__(self, p): self._p = p
    def json(self): return self._p


def _stub(payload):
    def _post(url, **kw):
        refreshes.append(kw.get("data", {}))
        return _R(payload)
    httpx.post = _post


_stub({"access_token": "gho_fresh", "refresh_token": "refresh-2", "expires_in": 28800})
r = clone(connect_code=cpush)
assert "error" not in r, r
sid = r["session_id"]
# The clone already refreshes -- redeem_connect_code goes through the same
# helper -- so this covers the whole ladder, not only the push.
assert refreshes, "redeeming an expired connection must refresh"
print("capella clone: expired connection refreshed at redemption")

# Age the connection again, commit some work, then push.
db = SessionLocal()
db.query(m.OAuthConnection).filter(
    m.OAuthConnection.id == "oc-exp").first().expires_at = utcnow() - timedelta(minutes=1)
db.commit(); db.close()

import capella_service as _svc
unpacked = _svc._session_dir(sid) / "unpacked"
(unpacked / "note.txt").write_text("edited")
_sp.run(["git", "-C", str(unpacked), "add", "-A"], check=True, capture_output=True)
_sp.run(["git", "-C", str(unpacked), "-c", "user.email=t@e.com", "-c", "user.name=t",
         "commit", "-m", "edit"], check=True, capture_output=True)

refreshes.clear()
_stub({"access_token": "gho_fresher", "refresh_token": "refresh-3", "expires_in": 28800})
res = push(sid)
assert res.get("status") == "ok", res
assert len(refreshes) == 1, "push must re-resolve exactly once, got " + str(len(refreshes))
print("capella push:  re-resolved the expired token and pushed")

# A revoked authorization is reported, not pushed through.
db = SessionLocal()
db.query(m.OAuthConnection).filter(
    m.OAuthConnection.id == "oc-exp").first().expires_at = utcnow() - timedelta(minutes=1)
db.commit(); db.close()
(unpacked / "note.txt").write_text("edited again")
_sp.run(["git", "-C", str(unpacked), "add", "-A"], check=True, capture_output=True)
_sp.run(["git", "-C", str(unpacked), "-c", "user.email=t@e.com", "-c", "user.name=t",
         "commit", "-m", "edit2"], check=True, capture_output=True)
_stub({"error": "bad_refresh_token"})
res = push(sid)
assert res["status"] == "error" and "expired" in res["message"], res
assert "committed locally" in res["message"], "must say the work is not lost"
print("capella push:  revoked authorization reported, work reported as safe")

httpx.post = _real_post
print()
print("ALL NOTE-0093 CAPELLA PUSH CHECKS PASSED")
