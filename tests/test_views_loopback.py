"""Views and fabrics on loopback: a link that is a link, and cannot be walked.

Step 7 of the stdio plan, driven over a real uvicorn on a real port.

The thing being protected is specific. ``git_service.clone_repo``'s own
docstring records that the credential lands in ``<dest>/.git/config`` "for the
life of the session directory" -- mitigated on the hosted side by
``cleanup_session`` removing that directory, and here never removed, because it
is the engineer's model. So this suite plants a **real token-shaped string in a
real clone's ``.git/config``**, exactly where an https clone puts it, and then
tries to get it out through the page server every way a URL can be written.

The cases:

1. **A link is a link.** The URL is fetched and the bytes come back byte for
   byte, which is the whole complaint this answers: an agent handing over a
   Windows path that "has to be copy and pasted into the browser".
2. **The headers a self-contained page should carry** -- the same
   Content-Security-Policy the hosted ``/view`` route sets, plus nosniff and
   no-referrer.
3. **The token and the Host guards**, each against a real request.
4. **Containment, in four spellings**, with the planted credential as the
   control: percent-encoded ``..``, literal ``..`` sent down a raw socket so
   no client normalises it away, Windows backslashes, and an absolute path.
   A client cannot be trusted to send the attack, so one case does not use one.
5. **The index page**, because the site's own front page links to
   ``/v/<model>/`` and without an index that link is a 404 the product handed
   the engineer itself.
6. **The nested layout is refused at the point of use**, not only at the point
   of configuration.
7. **``generate_viewer_link`` returns a link now**, and the degradation path
   still works when the page module is gone.
"""

import http.cookiejar
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

REPO = Path(r"c:\apps\Git_VSCode\Capella_Fabric_Generator")
sys.path.insert(0, str(REPO))

from capella_bundle import config, session, settings

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if ok else 'FAIL'} {label}" + (f": {detail}" if detail and not ok else ""))
    if not ok:
        failures.append(label)


def raises(label: str, fn, *, saying: str = "") -> str:
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


ROOT = Path(tempfile.mkdtemp(prefix="views_loopback_"))
settings.default_state_root = lambda: ROOT
config.default_state_root = lambda: ROOT


class Files(settings.FileStore):
    def __init__(self) -> None:
        super().__init__(ROOT / "credentials")


STORE = Files()
settings.credential_store = lambda prefer_file=False: STORE

from capella_bundle import page_server  # noqa: E402
import stdio_server as server  # noqa: E402

#: Planted into the clone's .git/config, exactly where an https clone puts it,
#: and then hunted for. Never used to authenticate anything: the fixture
#: remote is file://, so this string's only job is to be reachable or not.
PLANTED = "ghp_PlantedInGitConfigMustNeverBeServed987"


def git(repo: Path, *args: str) -> str:
    done = subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True)
    assert done.returncode == 0, f"git {' '.join(args)}: {done.stderr}"
    return done.stdout.strip()


def origin(name: str) -> str:
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

settings.save_profile(settings.Profile(
    name="work",
    models=(settings.ModelEntry(nickname="ac", remote_url=AC, branch="main",
                                access="write"),)))
server.SELECTED_PROFILE = "work"
server._PROFILE = None

PROFILE = settings.load_profile("work")
CFG = settings.to_config(PROFILE, "ac", store=STORE)
session.prepare(CFG)

OPENED = server.open_model("ac")
SID = OPENED["session_id"]

# Planted AFTER the open, deliberately: open_model re-applies the credential
# the profile holds, which rewrites origin's URL -- so planting first would
# have the control pass and then the subject be wiped before a single
# traversal was tried. Found by reading the order rather than by a failure,
# which is the kind of thing that makes a security test pass for free.
CONFIG_FILE = CFG.clone_dir / ".git" / "config"
git(CFG.clone_dir, "remote", "set-url", "origin",
    f"https://oauth2:{PLANTED}@example.invalid/o/r.git")
check("**the control: the credential really is in the clone's .git/config**",
      PLANTED in CONFIG_FILE.read_text(encoding="utf-8", errors="replace"),
      str(CONFIG_FILE))
check("...and the views directory is outside the model directory",
      not config.state.inside(CFG.model_dir, CFG.views_dir),
      f"{CFG.views_dir} vs {CFG.model_dir}")

# Two artifacts of the kinds this product actually produces, planted where
# capella_service writes them and published the way the tools publish them.
VIEW_BODY = "<html><body><img src='data:image/svg+xml;base64,AAA'></body></html>"
planted_view = CFG.model_dir / "view_abc__context_diagram.html"
planted_view.write_text(VIEW_BODY, encoding="utf-8", newline="")
VIEW = server._publish(CFG, planted_view, move=True)

planted_fabric = CFG.model_dir / "ac_fabric.txt"
planted_fabric.write_text("objects: []\n", encoding="utf-8")
FABRIC = server._publish(CFG, planted_fabric, move=False)

# ---------------------------------------------------------------------------
print("1. A link is a link, and the bytes come back")
# ---------------------------------------------------------------------------

LINKS = server._links(CFG, VIEW)
check("the tool now returns a url, not only a path",
      LINKS.get("url", "").startswith("http://127.0.0.1:"), str(LINKS)[:200])
check("...and markdown to print verbatim",
      LINKS.get("viewer_markdown", "").startswith(f"[{VIEW.name}](http://"),
      LINKS.get("viewer_markdown", ""))
check("...and still the path, for when nothing is running",
      LINKS.get("path") == str(VIEW))
check("...and says the link lasts as long as the server",
      "as long as this server runs" in LINKS.get("viewer_note", ""))

URL = LINKS["url"]
PORT = int(re.search(r":(\d+)/", URL).group(1))
BASE = f"http://127.0.0.1:{PORT}"
TOKEN = urllib.parse.parse_qs(urllib.parse.urlparse(URL).query)["t"][0]


def opener():
    return urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))


SESSION = opener()


def fetch(path: str, *, host: str = "", use=None, raw: bool = False):
    """(status, body, headers). A refusal is a status, never an exception."""
    url = path if path.startswith("http") else BASE + path
    request = urllib.request.Request(url)
    if host:
        request.add_header("Host", host)
    client = use if use is not None else SESSION
    # The headers object, not dict(...): uvicorn sends header names in
    # lower case, and an HTTPMessage looks them up case-insensitively while a
    # plain dict does not. Converting it made five header assertions fail
    # against a server that was setting every one of them.
    try:
        with client.open(request, timeout=30) as answer:
            return answer.status, answer.read(), answer.headers
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(), exc.headers
    except urllib.error.URLError as exc:
        return 0, str(exc).encode(), {}


def raw_get(target: str) -> tuple[int, bytes]:
    """One HTTP GET down a bare socket, so the path is sent exactly as given.

    urllib and every browser collapse ``..`` in a path before it leaves, so a
    traversal written the obvious way never reaches the server and a test
    using a client proves only that the client normalised it. This sends the
    literal bytes.

    **The token goes in the cookie, not the query string.** A request carrying
    ``?t=`` is a request to *exchange* the one-time token, which the guard
    answers with a 303 to the same path before any resolving happens -- so
    asking that way measures the exchange and never reaches the containment
    check. Arriving with the cookie is how a browser arrives after the first
    click, and it is the state in which the resolver is what answers.
    """
    with socket.create_connection(("127.0.0.1", PORT), timeout=30) as sock:
        sock.sendall(
            f"GET {target} HTTP/1.1\r\nHost: 127.0.0.1:{PORT}\r\n"
            f"Cookie: {page_server.COOKIE}={TOKEN}\r\n"
            f"Connection: close\r\n\r\n".encode())
        chunks = []
        while True:
            piece = sock.recv(65536)
            if not piece:
                break
            chunks.append(piece)
    answer = b"".join(chunks)
    first = answer.split(b"\r\n", 1)[0]
    code = int(first.split(b" ")[1]) if len(first.split(b" ")) > 1 else 0
    return code, answer


status, body, headers = fetch(URL, use=opener())
check("**the rendered view is served over http**", status == 200,
      f"{status}: {body[:120]!r}")
check("...byte for byte what was published",
      body.decode("utf-8") == VIEW_BODY, body[:120].decode("utf-8", "replace"))
check("...as html", "text/html" in headers.get("Content-Type", ""),
      headers.get("Content-Type", ""))

fab_links = server._links(CFG, FABRIC)
status, body, headers = fetch(fab_links["url"], use=opener())
check("the generated fabric is served too", status == 200, str(status))
check("...as plain text", "text/plain" in headers.get("Content-Type", ""),
      headers.get("Content-Type", ""))

# ---------------------------------------------------------------------------
print("\n2. The headers a self-contained page should carry")
# ---------------------------------------------------------------------------

status, body, headers = fetch(URL, use=opener())
check("a Content-Security-Policy is set",
      headers.get("Content-Security-Policy", "") == page_server.VIEW_CSP,
      headers.get("Content-Security-Policy", "(none)"))
check("...allowing no script, no font and no remote image",
      "default-src 'none'" in page_server.VIEW_CSP
      and "script" not in page_server.VIEW_CSP, page_server.VIEW_CSP)
check("...but allowing the embedded data-URI the page actually uses",
      "img-src data:" in page_server.VIEW_CSP)
check("nosniff is set", headers.get("X-Content-Type-Options") == "nosniff")
check("and no referrer leaves the page",
      headers.get("Referrer-Policy") == "no-referrer")
check("the policy matches the hosted server's own /view route",
      page_server.VIEW_CSP
      in (REPO / "mcp_server.py").read_text(encoding="utf-8"),
      page_server.VIEW_CSP)

# ---------------------------------------------------------------------------
print("\n3. The token and the Host guards")
# ---------------------------------------------------------------------------

bare_path = urllib.parse.urlparse(URL).path
status, body, _ = fetch(bare_path, use=opener())
check("**the same page with no token is refused**", status == 403,
      f"{status}: {body[:120]!r}")
check("...and the refusal says to ask the agent for a fresh link",
      b"ask the agent" in body.lower(), body[:200].decode("utf-8", "replace"))
check("...and does not say to restart anything, which is the setup page's advice",
      b"restart" not in body.lower(), body[:200].decode("utf-8", "replace"))

first = opener()
status, _body, _h = fetch(URL, use=first)
check("the token admits the first request", status == 200, str(status))
status, _body, _h = fetch(bare_path, use=first)
check("...and the cookie it set admits the next", status == 200, str(status))
check("the cookie is this product's own, not the notebook's",
      page_server.COOKIE == "capella_fabric_pages"
      and page_server.COOKIE != "cartenza_pages", page_server.COOKIE)

status, body, _ = fetch(URL, host="evil.example", use=opener())
check("**a forged Host header is refused**", status == 400,
      f"{status}: {body[:120]!r}")
status, body, _ = fetch(bare_path, host="evil.example", use=first)
check("**...even carrying a valid cookie**", status == 400,
      f"{status}: {body[:120]!r}")

# ---------------------------------------------------------------------------
print("\n4. Containment: the credential cannot be walked to")
# ---------------------------------------------------------------------------

# How many levels up it actually is, computed rather than guessed -- a fixed
# number of ../ that happens to fall short would pass for the wrong reason.
UP = len(CFG.views_dir.resolve().parts)
up_to_config = "/".join([".."] * UP) + str(CONFIG_FILE.resolve()).replace("\\", "/")[2:]

attempts = [
    ("percent-encoded ..", f"/v/ac/{urllib.parse.quote(up_to_config, safe='')}"),
    ("the plan's shape", "/v/ac/" + urllib.parse.quote(
        "../../../../Users/komar/.gitconfig", safe="")),
    ("Windows backslashes", "/v/ac/" + urllib.parse.quote(
        r"..\..\..\..\models\fixtures-ac\unpacked\.git\config", safe="")),
    ("an absolute path", "/v/ac/" + urllib.parse.quote(
        str(CONFIG_FILE.resolve()), safe="")),
    ("a bare .git/config under the views name", "/v/ac/.git/config"),
]
for label, attempt in attempts:
    status, body, _ = fetch(attempt, use=first)
    check(f"{label} is refused", status == 404, f"{status}: {body[:100]!r}")
    check(f"...and {label} returned no credential", PLANTED.encode() not in body)

# The same thing without a client in the way: urllib collapses `..` before
# sending, so every case above proves the client normalised it as much as it
# proves the server refused it. A raw socket sends the bytes as written.
raw_attempts = [
    up_to_config,
    "../../../../Users/komar/.gitconfig",
    "..%2f..%2f..%2f..%2fmodels%2ffixtures-ac%2funpacked%2f.git%2fconfig",
    str(CONFIG_FILE.resolve()).replace("\\", "/"),
]
for attempt in raw_attempts:
    code, answer = raw_get(f"/v/ac/{attempt}")
    short = attempt[:58]
    check(f"**raw socket, literal path: {short} is refused**",
          code in (400, 404), f"{code}")
    check(f"...and returned no credential: {short}",
          PLANTED.encode() not in answer)
# The control for the raw path: the same socket, the same cookie, a legitimate
# file. Without it every refusal above could be a request the server never
# understood rather than one it declined.
code, answer = raw_get(f"/v/ac/{VIEW.name}")
check("**the control: a raw request for a real file is served**",
      code == 200 and VIEW_BODY.encode() in answer, f"{code}")

# And the control for all of it: the legitimate request still works, so the
# refusals above are the containment check and not a broken server.
status, body, _ = fetch(URL, use=first)
check("**the control: the legitimate page is still served**", status == 200,
      str(status))

check("nothing in the views directory contains the credential",
      not any(PLANTED in p.read_text(encoding="utf-8", errors="replace")
              for p in CFG.views_dir.rglob("*") if p.is_file()),
      str(list(CFG.views_dir.rglob("*"))))
check("...and the model directory still holds no servable html",
      not list(CFG.model_dir.glob("*.html")),
      str(list(CFG.model_dir.glob("*"))))

# ---------------------------------------------------------------------------
print("\n5. The index page, which the site's own front page links to")
# ---------------------------------------------------------------------------

index_links = server.generate_viewer_link(SID)
check("generate_viewer_link with no filename gives the index a url",
      index_links.get("url", "").startswith("http://127.0.0.1:"),
      str(index_links)[:200])
check("...and lists what was produced",
      set(index_links["produced"]) == {VIEW.name, FABRIC.name},
      str(index_links["produced"]))
check("...leaving index.html out of that listing",
      "index.html" not in index_links["produced"], str(index_links["produced"]))

status, body, _ = fetch(index_links["url"], use=first)
text = body.decode("utf-8", "replace")
check("**the index is served, not a 404**", status == 200,
      f"{status}: {text[:150]}")
check("...and names the model", "ac" in text)
check("...and links to both artifacts",
      f'href="{VIEW.name}"' in text and f'href="{FABRIC.name}"' in text,
      text[:400])
check("...and says what each one is",
      "rendered view" in text and "generated fabric" in text, text[:600])
check("...and loads nothing from off this machine",
      "http://" not in text.replace(f"http://127.0.0.1:{PORT}", "")
      and "https://" not in text, text[:400])

status, body, _ = fetch(f"/?t={TOKEN}", use=opener())
front = body.decode("utf-8", "replace")
check("the site's front page lists the model", status == 200 and "ac" in front,
      front[:200])
linked = re.search(r'href="(/v/[^"]+)"', front)
check("...and the link it offers resolves", bool(linked), front[:300])
if linked:
    status, body, _ = fetch(linked.group(1), use=first)
    check("**...to the index rather than a 404**", status == 200,
          f"{status}: {body[:150]!r}")

status, body, _ = fetch(f"/v/nosuchmodel/?t={TOKEN}", use=opener())
check("an unserved model name is a 404 naming what is served",
      status == 404 and b"ac" in body, f"{status}: {body[:150]!r}")

# ---------------------------------------------------------------------------
print("\n6. The nested layout is refused at the point of use too")
# ---------------------------------------------------------------------------

nested = config.ModelConfig(
    name="nested", remote_url=AC, branch="main", pat="", access="read",
    model_dir=CFG.model_dir, views_dir=CFG.model_dir / "views")
refusal = raises("registering a views dir inside a model dir is refused",
                 lambda: page_server.register(nested), saying=".git/config")
check("...and the refusal names the model directory",
      str(CFG.model_dir) in refusal, refusal)
check("...and nothing was registered under that name",
      "nested" not in page_server.registered(),
      str(sorted(page_server.registered())))

same = config.ModelConfig(
    name="same", remote_url=AC, branch="main", pat="", access="read",
    model_dir=CFG.model_dir, views_dir=CFG.model_dir)
raises("...and so is serving the model directory itself",
       lambda: page_server.register(same), saying=".git/config")

# ---------------------------------------------------------------------------
print("\n7. The degradation path still works")
# ---------------------------------------------------------------------------

original = server._pages
check("**the control: the page module is found normally**",
      original() is not None)
try:
    server._pages = lambda: None
    degraded = server._links(CFG, VIEW)
    check("with no page server there is no url", "url" not in degraded,
          str(degraded))
    check("...and no markdown presented as a link",
          "viewer_markdown" not in degraded, str(degraded))
    check("...but the file's own path, and where to find it",
          degraded["path"] == str(VIEW) and "browser" in degraded["viewer_note"])
    check("...and it is not offered as something to click",
          "click" in degraded["viewer_note"], degraded["viewer_note"])
finally:
    server._pages = original

check("...and the url comes back once it is available",
      server._links(CFG, VIEW).get("url", "").startswith("http://127.0.0.1:"))

page_server.stop()
shutil.rmtree(ROOT, ignore_errors=True)

print()
if failures:
    print(f"{len(failures)} CHECK(S) FAILED")
    for name in failures:
        print(f"  - {name}")
    sys.exit(1)
print("views on loopback: all checks passed")
