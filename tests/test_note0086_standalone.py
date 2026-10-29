"""note-0086: standalone capella-fabric (kp-auth NOT installed) is unchanged.

Simulates the absent dependency by refusing to import `auth.*` at all -- the
sys.path for se-knowledge-partner/kp is deliberately never added.
"""
import os, sys, subprocess, tempfile
from pathlib import Path

tmp = tempfile.mkdtemp()
sys.path.insert(0, r"c:\apps\Git_VSCode\Capella_Fabric_Generator")


class _BlockAuth:
    def find_module(self, name, path=None):
        return self if name == "auth" or name.startswith("auth.") else None

    def find_spec(self, name, path=None, target=None):
        if name == "auth" or name.startswith("auth."):
            raise ImportError(f"No module named {name!r} (simulated: kp-auth not installed)")
        return None


sys.meta_path.insert(0, _BlockAuth())


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

import mcp_server as ms
import git_service as gs
gs._inject_pat = lambda url, pat: url

assert ms._AUTH_AVAILABLE is False, "kp-auth must be treated as absent here"
print("import: mcp_server loads cleanly with kp-auth absent")

_unwrap = lambda t: getattr(t, "fn", t)
clone, begin, add_dep = map(_unwrap, (ms.clone_capella_repo, ms.begin_connect, ms.add_dependency_repo))

r = begin("a1", MAIN, "main")
assert "error" in r and "pass github_pat directly" in r["error"], r
print("begin_connect: degrades with a clear, actionable message")

r = clone(connect_code="anything")
assert "error" in r and "pass github_pat directly" in r["error"], r
print("clone: connect_code degrades the same way")

r = clone(repo_url=MAIN, github_pat="ghp_direct")
assert "error" not in r, r
sid = r["session_id"]
assert r["aird_file"] == "MainModel.aird"
print("clone: the existing PAT flow is untouched")

d = add_dep(session_id=sid, repo_url=DEP, resource_name="Lib", github_pat="ghp_direct")
assert "error" not in d, d
print("add_dependency_repo: the existing PAT flow is untouched")

d = add_dep(session_id=sid, repo_url=DEP, resource_name="Lib2")
assert "error" in d and "no reusable GitHub authorization" in d["error"], d
print("add_dependency_repo: omitting github_pat says what to do instead")

print("\nALL NOTE-0086 STANDALONE CHECKS PASSED")
