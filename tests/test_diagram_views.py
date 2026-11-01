r"""A view the agent can hand a human to look at, instead of describing it.

Asked for as XHTML generation via eclipse-capella/capella-xhtml-docgen. That
needs a full Eclipse RCP install, a JRE and probably Xvfb on the droplet;
capellambse already renders Capella diagrams to SVG in pure Python, and
capellambse-context-diagrams GENERATES a view for an element that no authored
diagram shows well. Both were already installed.

The escaping hazard here is NOT the YAML one. A bracket-prefixed PMEI name is
harmless in HTML; `<`, `&` and quotes are not, and this is a page a human opens
in a browser. yaml_safe/yaml_str are the wrong tools -- different format,
different escaping -- and reaching for them out of habit is the mistake this
test exists to catch.
"""
import os
import subprocess
import sys

_VENV = r"c:\apps\Git_VSCode\Capella_Fabric_Generator\.venv\Scripts\python.exe"
if os.path.exists(_VENV) and os.path.normcase(sys.executable) != os.path.normcase(_VENV):
    sys.exit(subprocess.run([_VENV, __file__]).returncode)

import warnings
warnings.filterwarnings("ignore")
import logging
logging.disable(logging.CRITICAL)

import base64
import re
from pathlib import Path

TOOLS = r"c:\apps\Git_VSCode\Capella_Tools"
FABRIC = r"c:\apps\Git_VSCode\Capella_Fabric_Generator"
os.environ["CAPELLA_TOOLS_PATH"] = TOOLS
sys.path.insert(0, TOOLS)
sys.path.insert(0, FABRIC)

import capella_service as svc

AIRD = TOOLS + r"\Trail_Power\Trail Power.aird"
SID = "t-views"
Path(svc._session_dir(SID)).mkdir(parents=True, exist_ok=True)
SESSION = {"aird_path": AIRD, "session_id": SID, "resolved_uuids": []}
COMMIT = "abc1234def5678"

model = svc.open_model(Path(AIRD))

# ===== 1. Aspects are DISCOVERED, never assumed =====
# FunctionalExchange has no generated view at all, and there are 112 of them in
# Trail Power -- the most numerous type in the model. A hardcoded
# `.context_diagram` would fail on the commonest thing a user might ask about.
seen = {}
for cls in ("LogicalComponent", "PhysicalComponent", "Capability",
            "Class", "FunctionalExchange", "ComponentExchange"):
    objs = model.search(cls)
    if objs:
        seen[cls] = svc.available_aspects(objs[0])

assert "context_diagram" in seen["LogicalComponent"]
assert "realization_view" in seen["PhysicalComponent"]
assert seen["FunctionalExchange"] == [], seen["FunctionalExchange"]
assert "tree_view" in seen["Class"], seen["Class"]
assert "data_flow_view" in seen["Capability"], seen["Capability"]
print("discovered:        aspects differ per type")
print("  FunctionalExchange -> " + repr(seen["FunctionalExchange"]) + "  (112 in this model)")
print("  Class              -> " + repr(seen["Class"]))

# Probing must not render. On a capellambse diagram, attribute access IS a
# render, so a hasattr() probe on an INSTANCE draws the diagram just to ask
# whether it could -- which is how the first attempt at this blew up.
# Checked against the CODE ONLY, with the docstring stripped. The first
# version of this assertion failed on the word 'hasattr' inside the
# docstring explaining why hasattr must not be used -- the third time in
# this project that an audit has matched its own prose. An audit that reads
# comments is testing the explanation, not the behaviour.
import ast

tree = ast.parse(Path(FABRIC, 'capella_service.py').read_text(encoding='utf-8'))
fn = next(n for n in ast.walk(tree)
          if isinstance(n, ast.FunctionDef) and n.name == 'available_aspects')
calls = {n.func.id for n in ast.walk(fn)
         if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
assert 'dir' in calls, 'available_aspects should inspect the class with dir()'
assert 'hasattr' not in calls, \
    'available_aspects must not probe the instance -- attribute access renders'
print("cheap probe:       checks the class; never triggers a render")


# ===== 2. Escaping: markup in a model name must not become markup =====
class FakeObj:
    name = '<script>alert("xss")</script>'
    uuid = "u-1"


page = svc.render_view_html(
    FakeObj(), "element", "context_diagram", "<svg/>",
    [{"name": '<img src=x onerror=alert(1)>', "type": "A & B",
      "layer": "LA", "uuid": "u-2"}],
    COMMIT,
)
assert "<script>alert" not in page, "an element name was injected into the page"
# The danger is an unescaped TAG, not the words inside it. 'onerror=alert'
# survives as visible text and is inert; what must never appear is a live
# img element. The page also carries a legitimate <img> for the diagram, so
# the check names the injected one rather than banning the element.
assert "<img src=x" not in page, "a live img tag was injected"
assert "&lt;img src=x onerror=alert(1)&gt;" in page, "injected markup should be visible escaped text"
assert "&lt;script&gt;" in page, "the name should be visible as escaped text"
assert "A &amp; B" in page, "an ampersand should be escaped"
print("escaped:           script tags and & in model text render as text")


class Bracket:
    name = "[M] Engine-Out NOx Sample"
    uuid = "u-3"


p2 = svc.render_view_html(Bracket(), "element", "context_diagram", "<svg/>", [], COMMIT)
assert "[M] Engine-Out NOx Sample" in p2, "a bracket name should pass through intact"
print("not over-escaped:  a PMEI bracket name survives unchanged")

# ===== 3. The SVG is sandboxed, not inlined =====
evil = '<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
p3 = svc.render_view_html(Bracket(), "element", "context_diagram", evil, [], COMMIT)
assert "<svg" not in p3, "the SVG must not be inlined into the page"
assert "data:image/svg+xml;base64," in p3, "expected a data-URI <img>"
b64 = re.search(r"base64,([A-Za-z0-9+/=]+)", p3).group(1)
assert base64.b64decode(b64).decode() == evil, "the SVG itself should be unaltered"
print("sandboxed:         SVG embedded as <img> data URI, script cannot run")

# ===== 4. Real renders, both kinds =====
comp = model.search("LogicalComponent")[1]
r_el = svc.render_diagram_view(SESSION, str(comp.uuid), commit=COMMIT)
assert r_el["kind"] == "element" and r_el["aspect"] == "context_diagram"
assert Path(r_el["path"]).exists()

# The element list must describe the PICTURE, not the request. This shipped
# wrong and the deployed server reported exactly one element for every
# component in Residential_AC while its SVGs ranged 11KB-61KB -- the count was
# hardcoded to the target itself. Checked against the diagram's own nodes, and
# asserted to be more than one, because a count of 1 is precisely what the
# defect produced and a test that tolerates it would not have caught it.
# Render BEFORE reading nodes. The node list is populated by the layout, so a
# cold read returns nothing -- which is why render_view's own extraction runs
# after its render, and why reading it first here reported 0 and looked like a
# second bug.
_diag = comp.context_diagram
_diag.render("svg")
# A generated diagram's nodes ARE the model elements -- no `.target` on them,
# unlike an authored diagram's. Filtering on `.target` here returned 0 and read
# as a second bug; it was the same one, in the test.
_nodes = [getattr(n, "target", None) or n for n in _diag.nodes]
_nodes = [n for n in _nodes if getattr(n, "uuid", None) is not None]
assert r_el["elements"] == len(_nodes),     f"reported {r_el['elements']} elements, the diagram shows {len(_nodes)}"
assert r_el["elements"] > 1,     "this component's context diagram has neighbours -- a count of 1 is the old bug"
_page = Path(r_el["path"]).read_text(encoding="utf-8")
for _n in _nodes[:4]:
    _name = getattr(_n, "name", "") or ""
    if _name:
        assert _name in _page, f"{_name!r} is in the diagram but not listed on the page"
print("elements listed:   " + str(r_el["elements"]) + " of " + str(len(_nodes))
      + " diagram nodes, each named on the page")
print("element view:      " + r_el["aspect"] + ", " + str(r_el["bytes"]) + "B, aspects="
      + repr(r_el["aspects_available"]))

diag = model.diagrams[0]
r_dg = svc.render_diagram_view(SESSION, diag.name, commit=COMMIT)
assert r_dg["kind"] == "diagram"
print("diagram view:      " + repr(r_dg["name"][:34]) + ", " + str(r_dg["bytes"]) + "B")

# ===== 5. The artifact name is the cache key =====
by_name = svc.render_diagram_view(SESSION, comp.name, commit=COMMIT)
assert by_name["artifact_name"] == r_el["artifact_name"], \
    (by_name["artifact_name"], r_el["artifact_name"])
assert COMMIT[:7] in r_el["artifact_name"]
other = svc.render_diagram_view(SESSION, str(comp.uuid), commit="9999999aaa")
assert other["artifact_name"] != r_el["artifact_name"], "a new commit must miss"
print("cache key:         uuid and name agree; a new commit changes it")

nocommit = svc.render_diagram_view(SESSION, str(comp.uuid))
assert nocommit["artifact_name"] is None, nocommit["artifact_name"]
assert Path(nocommit["path"]).exists(), "the page must still be produced"
print("no commit:         page still rendered, artifact_name None (uncacheable)")

# ===== 6. An element with no view fails clearly =====
fe = model.search("FunctionalExchange")[0]
try:
    svc.render_diagram_view(SESSION, str(fe.uuid), commit=COMMIT)
    raise AssertionError("expected a ValueError for an element with no aspects")
except ValueError as exc:
    assert "no generated view" in str(exc)
    assert "FunctionalExchange" in str(exc), "the error should name the type"
print("clear failure:     an element with no aspect says so, and why")

try:
    svc.render_diagram_view(SESSION, str(comp.uuid), aspect="tree_view", commit=COMMIT)
    raise AssertionError("expected a ValueError for an unsupported aspect")
except ValueError as exc:
    assert "supports" in str(exc)
print("wrong aspect:      rejected, listing what the element does support")

# ===== 7. Self-contained =====
page = Path(r_el["path"]).read_text(encoding="utf-8")
for bad in ("<link ", "<script src", 'src="http', "@import"):
    assert bad not in page, "page references something external: " + bad
assert page.lstrip().startswith("<!doctype html>")
print("self-contained:    no external refs; opens from disk alone")

# ===== 8. The standalone handoff: a link that opens in a browser =====
# Someone running this server without Cartenza has no workspace repo, so this
# route IS their handoff. /download sends an attachment, which is the wrong
# thing for a page meant to be looked at.
import app as flask_app

flask_app.app.config["TESTING"] = True
client = flask_app.app.test_client()
view_name = Path(r_el["path"]).name

resp = client.get("/view/" + SID + "/" + view_name)
assert resp.status_code == 200, resp.status_code
assert resp.mimetype == "text/html", resp.mimetype
assert b"<!doctype html>" in resp.data[:200].lower()
print("standalone route:  serves the page inline as text/html")

# A crafted name must not walk out of the session directory. Both guards are
# checked: the name pattern, and the resolved path staying inside the base.
for bad in ("../../../../windows/win.ini", "view_..%2f..%2fsecret.html",
            "session.json", "view_nope.html"):
    r = client.get("/view/" + SID + "/" + bad)
    assert r.status_code in (302, 404), (bad, r.status_code)
    assert b"win.ini" not in r.data and b"aird_path" not in r.data, bad
print("no traversal:      crafted names redirect; nothing outside the session leaks")

# ===== 9. Rendering needs no Cartenza auth at all =====
# kp-auth is an optional dependency and must stay that way: the generation half
# is what an open-source user actually runs.
view_src = Path(FABRIC, "capella_service.py").read_text(encoding="utf-8")
seg = view_src.split("def render_diagram_view(")[1].split(chr(10) + "def ")[0]
for forbidden in ("auth.", "_AUTH_AVAILABLE", "api_key", "kp_auth"):
    assert forbidden not in seg, "render_diagram_view reaches for auth: " + forbidden
print("standalone-safe:   the renderer touches no auth path")

# ===== 10. The Cartenza handoff: a signed URL, not 44KB of base64 =====
# The feature shipped telling an agent to relay the page body. A 44KB page came
# back, could not be passed into another tool call, and the diagram was
# re-described in prose instead of shown -- the exact behaviour this feature
# exists to replace (Fabric_MCP_Issues/OBS-0023). So the handoff is a link now,
# and this section tests the route that actually serves it rather than a copy of
# it: the real Starlette app from mcp.streamable_http_app().
import importlib
import time

os.environ["CAPELLA_VIEW_SECRET"] = "test-only-not-a-real-secret"
import capella_service

importlib.reload(capella_service)   # the secret is read once, at import
svc = capella_service
import mcp_server as M

from starlette.testclient import TestClient

r_url = svc.render_diagram_view(SESSION, str(comp.uuid), commit=COMMIT)
view_file = Path(r_url["path"])
view_name = view_file.name
EXP = int(time.time()) + 600
TOK = svc.view_token(SID, view_name, EXP)

HOST = "dev.capella.cartenza.ai"
with TestClient(M.mcp.streamable_http_app(), base_url="https://" + HOST) as client2:
    url = "/view/" + SID + "/" + view_name

    resp = client2.get(url, params={"exp": EXP, "t": TOK})
    assert resp.status_code == 200, resp.status_code
    assert resp.headers["content-type"].startswith("text/html"), resp.headers["content-type"]
    # Byte-identical, because the point is that the browser gets the page the
    # renderer wrote -- not a re-encoded approximation of it.
    assert resp.content == view_file.read_bytes(), "served bytes differ from the written page"
    assert resp.headers["x-content-type-options"] == "nosniff"
    csp = resp.headers["content-security-policy"]
    # The page embeds its SVG as a data-URI <img> and styles inline, and needs
    # nothing else at all -- so model text that ever escaped our escaping still
    # could not reach the network from inside this.
    assert "default-src 'none'" in csp and "img-src data:" in csp, csp
    print("signed url:        200 text/html, byte-identical, CSP + nosniff")

    # Every rejection is the same bare 404. A distinct "bad token" and "no such
    # file" would tell a prober which half they had got right.
    bad = {
        "no token":        {"exp": EXP},
        "tampered token":  {"exp": EXP, "t": "0" * 32},
        "tampered expiry": {"exp": EXP + 86400, "t": TOK},
        "expired":         {"exp": int(time.time()) - 1,
                            "t": svc.view_token(SID, view_name, int(time.time()) - 1)},
    }
    for label, params in bad.items():
        r = client2.get(url, params=params)
        assert r.status_code == 404, (label, r.status_code)
        assert b"<!doctype" not in r.content.lower(), label
    print("rejected:          " + ", ".join(bad) + " -- all 404, all alike")

    # A token is bound to session AND filename together, so it cannot be
    # replayed sideways onto another page or another session's page.
    other_sid = SID + "-2"
    Path(svc._session_dir(other_sid)).mkdir(parents=True, exist_ok=True)
    (Path(svc._session_dir(other_sid)) / view_name).write_text("secret", encoding="utf-8")
    r = client2.get("/view/" + other_sid + "/" + view_name, params={"exp": EXP, "t": TOK})
    assert r.status_code == 404, r.status_code
    assert b"secret" not in r.content
    print("not transferable:  another session's page with this token -- 404")

    # The filename guard still applies after the token check passes, so a
    # correctly-signed request for something that is not a rendered view fails.
    for name in ("session.json", "upload.zip", "view_nope.html"):
        r = client2.get("/view/" + SID + "/" + name,
                        params={"exp": EXP, "t": svc.view_token(SID, name, EXP)})
        assert r.status_code == 404, (name, r.status_code)
        assert b"aird_path" not in r.content, name
    r = client2.get("/view/" + SID + "/..%2f..%2fview_x.html",
                    params={"exp": EXP, "t": TOK})
    assert r.status_code == 404, r.status_code
    print("guard holds:       a signed request for a non-view is still 404")


# The absolute URL is built from the request, with no new configuration: nginx
# forwards Host and X-Forwarded-Proto. Trusting Host is safe HERE only because
# allowed_hosts 421s a forged one before any of this code runs.
class _FakeReq:
    def __init__(self, headers):
        self.headers = headers
        self.url = type("U", (), {"scheme": "http"})()


class _FakeCtx:
    def __init__(self, req):
        self.request_context = type("R", (), {"request": req})()


_real_get_context = M.mcp.get_context
os.environ.pop("CAPELLA_PUBLIC_BASE_URL", None)
M.mcp.get_context = lambda: _FakeCtx(_FakeReq(
    {"host": HOST, "x-forwarded-proto": "https"}))
built, exp2 = M._view_url(SID, str(view_file))
assert built.startswith("https://" + HOST + "/view/" + SID + "/" + view_name), built
assert "exp=" in built and "t=" in built
assert exp2 - time.time() > 3 * 3600, "a link should outlast a working session"
assert svc.verify_view_token(SID, view_name, exp2, built.split("t=")[1])
print("url from request:  https://" + HOST + "/view/... (X-Forwarded-Proto honoured)")

# Behind something that rewrites differently, the override wins.
os.environ["CAPELLA_PUBLIC_BASE_URL"] = "https://capella.example.test/"
built2, _ = M._view_url(SID, str(view_file))
assert built2.startswith("https://capella.example.test/view/"), built2
os.environ.pop("CAPELLA_PUBLIC_BASE_URL")
print("override:          CAPELLA_PUBLIC_BASE_URL takes precedence")

# No base URL discoverable (stdio, say) -- no url, nothing raised, and `path`
# is still the standalone handoff it always was.
M.mcp.get_context = lambda: (_ for _ in ()).throw(RuntimeError("no request"))
assert M._view_url(SID, str(view_file)) == (None, None)
M.mcp.get_context = _real_get_context
print("no request:        no url, and nothing raised")

# The TOOL must actually put the url in its result. The route working and the
# tool returning the link are two different things, and shipping the first
# without the second is how OBS-0023 happened in the first place.
M.mcp.get_context = lambda: _FakeCtx(_FakeReq(
    {"host": HOST, "x-forwarded-proto": "https"}))
M._head_summary = lambda _sid: {"commit": COMMIT}
svc.save_session(SID, SESSION)
tool_result = M.render_diagram_view(SID, str(comp.uuid))
assert "error" not in tool_result, tool_result
assert tool_result["url"].startswith("https://" + HOST + "/view/"), tool_result.get("url")
assert tool_result["url_expires_at"].endswith("+00:00"), tool_result["url_expires_at"]
assert "html" not in tool_result, "the body must stay out unless asked for"
with_body = M.render_diagram_view(SID, str(comp.uuid), include_html=True)
assert with_body["html"].lstrip().startswith("<!doctype html>")
M.mcp.get_context = _real_get_context
print("tool returns url:  " + tool_result["url_expires_at"] + ", body withheld by default")

# ===== 11. A standalone deployment configures nothing =====
# The easiest case to leave untested: with no CAPELLA_VIEW_SECRET set, the
# server makes its own per-process one. Links then die on restart, which is
# honest -- the session they point into is gone by then anyway. Checked in a
# subprocess because the secret is read once, at import.
probe = (
    "import os,sys,time;"
    "os.environ.pop('CAPELLA_VIEW_SECRET',None);"
    "sys.path.insert(0,r'" + TOOLS + "');"
    "sys.path.insert(0,r'" + FABRIC + "');"
    "import capella_service as s;"
    "e=int(time.time())+60;"
    "t=s.view_token('sid','view_a.html',e);"
    "assert s.verify_view_token('sid','view_a.html',e,t);"
    "assert not s.verify_view_token('sid','view_b.html',e,t);"
    "assert len(s._VIEW_SECRET)>20;"
    "print('ok')"
)
out = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True)
assert out.returncode == 0 and out.stdout.strip() == "ok", (out.stdout, out.stderr[-600:])
print("standalone:        unconfigured, it signs with a per-process secret")

print("")
print("ALL DIAGRAM VIEW CHECKS PASSED")
