"""note-0104, capella-fabric half: clone_capella_repo takes the same path.

capella-fabric doesn't go through mcp_common -- it calls auth.connect itself
(mcp_server.py) -- so the wiring there is separate code and needs its own
check.

Deliberately NOT run under capella-fabric's own venv, unlike the model tests:
that venv has no sqlalchemy, because kp-auth is an optional dependency there
and only the Cartenza deployment installs it. Nothing here touches capellambse,
so the interpreter that has the database libraries is the right one.

The refusals are what's testable without reaching GitHub, and they're the half
that decides whether a user gets interrupted: a wrong message here sends them
to a browser they didn't need.
"""
import os
import sys
import tempfile
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
tmp = Path(tempfile.mkdtemp())
os.environ["CARTENZA_DB_PATH"] = str(tmp / "test.db")
os.environ["CAPELLA_SESSION_ROOT"] = str(tmp / "sessions")
os.environ["CAPELLA_TOOLS_PATH"] = r"c:\apps\Git_VSCode\Capella_Tools"
from cryptography.fernet import Fernet
os.environ["CARTENZA_SECRET_KEY"] = Fernet.generate_key().decode()

sys.path.insert(0, r"c:\apps\Git_VSCode\Capella_Tools")
sys.path.insert(0, r"c:\apps\Git_VSCode\se-knowledge-partner\kp")
sys.path.insert(0, r"c:\apps\Git_VSCode\Capella_Fabric_Generator")

from auth.db import Base, SessionLocal, engine
from auth.models import Agent, AgentScope, ConnectCode, OAuthConnection, User, UserRepo
from auth.secrets import encrypt_pat

Base.metadata.create_all(engine)

import mcp_server as ms

assert ms._AUTH_AVAILABLE, "kp-auth must be importable, or the whole path is dead here"
clone = getattr(ms.clone_capella_repo, "fn", ms.clone_capella_repo)

MODEL = "https://github.com/tkSDISW/After_Treatment_System"
PAT_REPO = "https://github.com/tkSDISW/pat_model"
TOKEN = "gho_capella_token_value"

db = SessionLocal()
db.add_all([
    User(id="u1", email="tony@example.com"),
    Agent(id="a1", owner_user_id="u1", name="Cousin"),
    OAuthConnection(id="oc1", user_id="u1", provider="github",
                    access_token_encrypted=encrypt_pat(TOKEN), expires_at=None),
])
db.add_all([
    UserRepo(id="r1", user_id="u1", repo_url=MODEL, repo_name="After_Treatment_System",
             platform="github", variable_name="ats_model", branch="master",
             auth_method="oauth", oauth_connection_id="oc1"),
    UserRepo(id="r2", user_id="u1", repo_url=PAT_REPO, repo_name="pat_model", platform="github",
             variable_name="pat_model", branch="master", auth_method="pat"),
])
db.add_all([
    AgentScope(id="s1", agent_id="a1", remote_url=MODEL, branch="master", permission="write"),
    AgentScope(id="s2", agent_id="a1", remote_url=PAT_REPO, branch="master", permission="write"),
])
db.commit()

# ===== 1. The credential resolves, and it's the stored one =====
from auth.connect import resolve_scoped_credential
r = resolve_scoped_credential("a1", MODEL, "master")
assert r["pat"] == TOKEN and r["oauth_connection_id"] == "oc1", r
# Omitted branch: Capella repos are usually master, and the agent often doesn't
# say. The scope decides, rather than a hardcoded guess.
assert resolve_scoped_credential("a1", MODEL, "")["branch"] == "master"
print("capella resolve:   stored authorization, branch taken from the scope")

# ===== 2. Refusals come back from clone_capella_repo itself, before any git =====
res = clone(repo_url="https://github.com/tkSDISW/not_scoped", agent_id="a1")
assert "error" in res and "/onboarding/repos" in res["error"], res
print("out of scope:      refused by the tool ->", res["error"][:70])

res = clone(repo_url=PAT_REPO, agent_id="a1")
assert "error" in res and "Connect via GitHub" in res["error"], res
print("PAT-linked:        told how to make it free ->", res["error"][:70])

# No agent_id and no credential: the old error, now naming the third way in.
res = clone(repo_url=MODEL)
assert "error" in res and "agent_id" in res["error"], res
print("no credential:     error names all three ways in")

# Nothing above may mint or consume a connect code.
assert db.query(ConnectCode).count() == 0, "a silent path must not touch connect codes"
print("connect codes:     none minted, none consumed")

db.close()
print("\nALL NOTE-0104 CAPELLA CHECKS PASSED")
