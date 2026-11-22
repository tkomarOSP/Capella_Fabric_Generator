"""The setup page, driven over a real loopback port.

Step 6 of the stdio plan. Everything here runs against a real uvicorn on a
real port, with real ``file://`` git repositories behind it, because the page's
whole job is the things a unit test cannot reach: a ``Host`` header, a cookie,
a token exchange, a clone that takes time, and HTML that must never contain a
credential.

The cases, and why each is here:

1. **The three guards fire, each against a real subject.** A forged ``Host``
   is produced by setting the header -- which is the attack the check exists
   for, and which is awkward to stage any other way, probably why nothing
   asserted it before the guard became a pure function. A request with no
   token is refused. The valid one-time token is exchanged for a cookie and
   **leaves the URL**, so it is not in the address bar, in history, or in a
   referrer.
2. **Nothing is editable until a profile is open.** ``None`` is a real state:
   showing an identity form with no profile behind it invites someone to fill
   it in and wonder where it went.
3. **A model is configured and cloned end to end**, through the page, with the
   session record written where ``capella_service`` reads it.
4. **The credential is never rendered.** The token is planted, then every byte
   the page serves is searched for it -- which is verification 6, and the half
   of it that a page can get wrong without anybody noticing.
5. **A views directory inside the model directory is refused**, because the
   clone's ``.git/config`` holds the token for as long as the directory exists
   and views are served over HTTP.
6. **Verify answers four questions in order**, with a "no" high up leaving
   what is below it *unchecked* rather than failing. Three real situations are
   staged: a healthy model, a branch that does not exist, and a remote that
   does not.
7. **Libraries are declared here and nowhere else**, which is why
   ``add_dependency_repo`` is absent from the tool surface. Adding one clones
   it; removing one deletes its credential and keeps its clone.
8. **Removing a model never deletes the model.**
9. **The foreground ``--setup`` path really serves**, started as a process and
   fetched from, with nothing on stdout.
"""

import http.cookiejar
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
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


ROOT = Path(tempfile.mkdtemp(prefix="setup_page_"))
settings.default_state_root = lambda: ROOT
config.default_state_root = lambda: ROOT


class Files(settings.FileStore):
    """The file backend, forced: a test must not write the engineer's keychain."""

    def __init__(self) -> None:
        super().__init__(ROOT / "credentials")


STORE = Files()
settings.credential_store = lambda prefer_file=False: STORE

from capella_bundle import setup_app  # noqa: E402  (after the redirection)

#: Planted as a real stored credential, then hunted for in every byte the page
#: serves. A `file://` remote needs none, so this is never actually used to
#: authenticate -- which is the point: it is here to be leaked, and it must
#: not be.
PLANTED = "ghp_PlantedTokenMustNeverBeRendered0123456789"


def git(repo: Path, *args: str) -> str:
    done = subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True)
    assert done.returncode == 0, f"git {' '.join(args)}: {done.stderr}"
    return done.stdout.strip()


def origin(name: str, *, aird: bool = True, branch: str = "main") -> str:
    work = ROOT / "fixtures" / name
    (work / "model").mkdir(parents=True)
    if aird:
        (work / "model" / f"{name}.aird").write_text("<aird/>", encoding="utf-8")
        (work / "model" / f"{name}.capella").write_text("<capella/>", encoding="utf-8")
    else:
        (work / "model" / "README.md").write_text("no model\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", "-b", branch, str(work)],
                   check=True, capture_output=True)
    git(work, "add", "-A")
    git(work, "-c", "user.email=f@x", "-c", "user.name=f", "commit", "-qm", "init")
    bare = ROOT / "fixtures" / f"{name}.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(work), str(bare)],
                   check=True, capture_output=True)
    return bare.as_uri()


AC = origin("ac")
LIB = origin("brakes")
NOMODEL = origin("empty", aird=False)
ODDBRANCH = origin("odd", branch="trunk")

# ---------------------------------------------------------------------------
# A real server on a real port
# ---------------------------------------------------------------------------

URL = setup_app.serve_in_background("")
PORT = int(re.search(r":(\d+)/", URL).group(1))
TOKEN = urllib.parse.parse_qs(urllib.parse.urlparse(URL).query)["token"][0]
BASE = f"http://127.0.0.1:{PORT}"

check("the page is serving on a loopback port", URL.startswith("http://127.0.0.1:"), URL)
check("...and the port is accepting connections before the link is handed over",
      socket.socket().connect_ex(("127.0.0.1", PORT)) == 0)
check("asking again returns the same link, not a second server",
      setup_app.serve_in_background("") == URL)


def opener(cookies: bool = True):
    handlers = [urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())] \
        if cookies else []
    return urllib.request.build_opener(*handlers)


SESSION = opener()


def flash_of(body: str) -> str:
    """Just the flash message, not the whole page.

    Written after a mutation test caught a check passing for the wrong
    reason: the model form's own help text mentions ``.git/config``, so
    asserting that phrase against the whole body was satisfied by static page
    text whether the refusal happened or not. A message the engineer is being
    shown has to be asserted where it is shown.
    """
    found = re.search(r'<div class="flash">(.*?)</div>', body, re.S)
    return found.group(1) if found else ""


def fetch(path: str = "/", *, data: dict | None = None, host: str = "",
          use=None, follow: bool = True) -> tuple[int, str, str]:
    """(status, body, final url). A refusal is a status, never an exception."""
    url = BASE + path
    body = urllib.parse.urlencode(data).encode() if data is not None else None
    request = urllib.request.Request(url, data=body)
    if host:
        # Explicit, so urllib does not derive it from the URL. This is the
        # forged-Host attack: a name the attacker controls resolving to
        # 127.0.0.1, with the browser faithfully sending it.
        request.add_header("Host", host)
    client = use if use is not None else SESSION
    try:
        with client.open(request, timeout=30) as answer:
            return answer.status, answer.read().decode("utf-8", "replace"), answer.url
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace"), url
    except urllib.error.URLError as exc:
        return 0, str(exc), url


# ---------------------------------------------------------------------------
print("1. The three guards, each against a real subject")
# ---------------------------------------------------------------------------

# 400 for a bad host and 403 for a bad token, which is the library's choice
# and a deliberate one: a forged host is a malformed request for this server,
# while a missing token is an unauthorized one. Pinned here because the two
# statuses are how an operator reading a log tells the two apart.
status, body, _ = fetch("/", host="evil.example", use=opener())
check("**a forged Host header is refused**", status == 400, f"{status}: {body[:120]}")
check("...and the refusal names what refused and what to do",
      "this page" in body.lower() and "127.0.0.1" in body, body[:200])

status, body, _ = fetch("/", use=opener())
check("no token and no cookie is refused", status == 403, f"{status}: {body[:120]}")
check("...with a different status from the forged host, so a log distinguishes them",
      status != 400, str(status))

# The exchange. A fresh client, the one-time token, and the token must be gone
# from where the browser ends up.
first = opener()
status, body, landed = fetch(f"/?token={TOKEN}", use=first)
check("the one-time token is accepted", status == 200, f"{status}: {body[:200]}")
check("**and exchanged, so it is no longer in the URL**",
      "token=" not in landed, landed)
status, body, _ = fetch("/", use=first)
check("...the cookie alone works afterwards", status == 200, str(status))
check("the cookie is this product's own, not the notebook's",
      setup_app.COOKIE == "capella_fabric_setup"
      and setup_app.COOKIE != "cartenza_setup", setup_app.COOKIE)

# A forged host with a VALID cookie is the attack the ordering exists for, so
# it is checked after the cookie has been obtained rather than before.
status, body, _ = fetch("/", host="evil.example", use=first)
check("**a valid cookie on a forged Host is still refused**", status == 400,
      f"{status}: {body[:120]}")

SESSION = first        # from here on, an authenticated browser

# ---------------------------------------------------------------------------
print("\n2. Nothing is editable until a profile is open")
# ---------------------------------------------------------------------------

status, body, _ = fetch("/")
check("the chooser is shown", "Start a new one" in body)
check("...and no identity form is", 'action="/identity"' not in body)
check("...and no model form is", 'action="/model"' not in body)

status, body, _ = fetch("/identity", data={"agent_name": "Sneaky"})
check("saving an identity with no profile open is refused",
      "No profile is open" in body, body[:200])
check("...and nothing was written", settings.list_profiles() == [],
      str(settings.list_profiles()))

# ---------------------------------------------------------------------------
print("\n3. A model, configured and cloned through the page")
# ---------------------------------------------------------------------------

status, body, _ = fetch("/profile/new", data={"name": "work"})
check("a profile is created", "Created work" in body, body[:200])
check("...and saved to disk", "work" in settings.list_profiles())

status, body, _ = fetch("/profile/new", data={"name": "no good!"})
check("a profile name with punctuation is refused",
      "cannot be used as a profile name" in body)

status, body, _ = fetch("/identity", data={
    "agent_name": "Thermal Architecture Agent", "display_name": "Thermal",
    "user_name": "Tony", "role": "reviews the thermal architecture"})
check("the identity is saved", "Saved this agent" in body)
check("...and read back", settings.load_profile("work").identity.visible_name
      == "Thermal", settings.load_profile("work").identity.visible_name)

status, body, _ = fetch("/model", data={
    "nickname": "ac", "remote_url": AC, "branch": "main", "access": "write",
    "pat": PLANTED})
check("**the model clones through the page**", "ac is ready" in body, body[-600:])
check("...and reports what it did", "cloned" in body and "model ac.aird" in body,
      body[-400:])

profile = settings.load_profile("work")
cfg = settings.to_config(profile, "ac")
check("the clone is where capella_service expects it",
      (cfg.clone_dir / ".git").is_dir(), str(cfg.clone_dir))
check("...and session.json is beside it",
      (cfg.model_dir / "session.json").is_file())
record = json.loads((cfg.model_dir / "session.json").read_text(encoding="utf-8"))
check("...naming the model file", record["aird_path"].endswith("ac.aird"),
      record["aird_path"])
check("the stored credential was kept", settings.secret_for(profile, "ac", STORE)
      == PLANTED)

# A blank token on a later save means "keep what is stored", so that changing
# a branch does not quietly delete a working credential.
status, body, _ = fetch("/model", data={
    "nickname": "ac", "remote_url": AC, "branch": "main", "access": "write",
    "include_realized": "1", "pat": ""})
check("saving again with a blank token keeps the credential",
      settings.secret_for(settings.load_profile("work"), "ac", STORE) == PLANTED)
check("...and the realization flag was taken",
      settings.load_profile("work").find("ac").include_realized)
check("...and the clone was not made again",
      "already on disk" in body, body[-300:])

# ---------------------------------------------------------------------------
print("\n4. The credential is never rendered")
# ---------------------------------------------------------------------------

pages = []
for path in ("/", "/?edit=ac"):
    _s, page_body, _u = fetch(path)
    pages.append(page_body)
check("**the planted token appears in no page the server serves**",
      not any(PLANTED in page for page in pages))
check("...and a configured model is shown as stored instead",
      "&bull;" in pages[0] or "•" in pages[0])
check("...and the page says a token is already stored when editing",
      "already stored" in pages[1], pages[1][:200])

profile_text = (ROOT / "profiles" / "work.json").read_text(encoding="utf-8")
check("the profile file on disk holds no credential either",
      PLANTED not in profile_text)
check("...and the credential store is a different file",
      not str(STORE._path("x")).startswith(str(ROOT / "profiles")),
      str(STORE._path("x")))

# ---------------------------------------------------------------------------
print("\n5. A views directory inside the model directory is refused")
# ---------------------------------------------------------------------------


def _resolves(nickname: str) -> bool:
    """Whether a saved model can be turned into a usable config at all."""
    try:
        settings.to_config(settings.load_profile("work"), nickname, store=STORE)
        return True
    except Exception:
        return False


inside = cfg.model_dir / "views"
status, body, _ = fetch("/model", data={
    "nickname": "nested", "remote_url": AC, "branch": "main", "access": "read",
    "model_dir": str(cfg.model_dir), "views_dir": str(inside)})
said = flash_of(body)
check("**the nested layout is refused**", "cannot be cloned yet" in said
      or "Not saved" in said, said[:400])
check("...and the reason named in the flash is the credential in .git/config",
      ".git/config" in said, said[:500])
check("...and the refusal is the configuration's, so nothing can resolve it",
      not _resolves("nested"), "to_config succeeded")
check("...and no views directory was created inside the model directory",
      not inside.exists())
# Via `..`, which resolves before the containment check rather than after --
# so a path that merely *looks* outside is caught too.
sneaky = cfg.model_dir / "sub" / ".." / "views"
status, body, _ = fetch("/model", data={
    "nickname": "sneaky", "remote_url": AC, "branch": "main", "access": "read",
    "model_dir": str(cfg.model_dir), "views_dir": str(sneaky)})
check("**...including one dressed up with `..`**",
      ".git/config" in flash_of(body), flash_of(body)[:400])
check("...and that one cannot resolve either", not _resolves("sneaky"))

# ---------------------------------------------------------------------------
print("\n6. Verify answers four questions, in the order they bite")
# ---------------------------------------------------------------------------


def verify_steps(nickname: str) -> dict[str, dict]:
    profile_now = settings.load_profile("work")
    cfg_now = settings.to_config(profile_now, nickname, store=STORE)
    return {s["step"]: s for s in session.verify(cfg_now)}


healthy = verify_steps("ac")
check("a healthy model is reachable", healthy["repository reachable"]["ok"] is True,
      str(healthy["repository reachable"]))
check("...its branch is present", healthy["branch present"]["ok"] is True)
check("**...push access is proved by a dry-run push, not assumed**",
      healthy["push access"]["ok"] is True, str(healthy["push access"]))
check("...and nothing was actually pushed",
      session.PUSH_PROBE_REF.split("/")[-1] not in
      git(cfg.clone_dir, "ls-remote", "--heads", "origin"),
      git(cfg.clone_dir, "ls-remote", "--heads", "origin"))
check("...and the model is present", healthy["Capella model present"]["ok"] is True,
      str(healthy["Capella model present"]))

status, body, _ = fetch("/model/verify", data={"nickname": "ac"})
check("the page renders the verdicts",
      "passed every check" in body and "push access" in body, body[-400:])

# A branch that is not there. Caught from the advertised ref list rather than
# by cloning and failing, which is what makes it cheap enough to offer.
fetch("/model", data={"nickname": "odd", "remote_url": ODDBRANCH,
                      "branch": "main", "access": "read"})
odd = verify_steps("odd")
check("a wrong branch is reported as such", odd["branch present"]["ok"] is False,
      str(odd["branch present"]))
check("...and the real branches are listed", "trunk" in odd["branch present"]["detail"],
      odd["branch present"]["detail"])
check("...while the repository itself is still reachable",
      odd["repository reachable"]["ok"] is True)
check("push access is NOT probed for a read-only model",
      odd["push access"]["ok"] is None
      and "reading only" in odd["push access"]["detail"],
      str(odd["push access"]))

# A remote that does not exist: everything below it is unanswerable, and says
# so rather than failing. A wrong token reported as "no .aird found" is the
# message that sends an engineer looking for a file that is there.
fetch("/model", data={"nickname": "gone",
                      "remote_url": (ROOT / "fixtures" / "nope.git").as_uri(),
                      "branch": "main", "access": "write"})
gone = verify_steps("gone")
check("an unreachable repository is reported first",
      gone["repository reachable"]["ok"] is False, str(gone["repository reachable"]))
for later in ("branch present", "push access", "Capella model present"):
    check(f"**...and {later} is 'not checked', not 'failed'**",
          gone[later]["ok"] is None and "could not be read" in gone[later]["detail"],
          str(gone[later]))

# A clone with no .aird. The likeliest cause is the branch, and the message
# says so rather than implying the repository is wrong.
fetch("/model", data={"nickname": "empty", "remote_url": NOMODEL,
                      "branch": "main", "access": "read"})
empty_cfg = settings.to_config(settings.load_profile("work"), "empty", store=STORE)
check("a repository with no model is reported at the clone",
      not session.is_prepared(empty_cfg)
      or session.verify(empty_cfg)[-1]["ok"] is False,
      str(session.verify(empty_cfg)[-1]))

# ---------------------------------------------------------------------------
print("\n7. Libraries are declared here, and nowhere else")
# ---------------------------------------------------------------------------

status, body, _ = fetch("/library", data={
    "nickname": "ac", "resource_name": "Brake Library", "remote_url": LIB,
    "branch": "main", "pat": ""})
check("a library is added and cloned", "Added Brake Library" in body, body[-500:])
grown = settings.load_profile("work").find("ac")
check("...and recorded on the model",
      [d.resource_name for d in grown.dependencies] == ["Brake Library"],
      str(grown.dependencies))
dep_dir = grown.dependencies[0].resolved_clone_dir(grown.resolved_model_dir())
check("...cloned under the model's own directory",
      (dep_dir / ".git").is_dir(), str(dep_dir))
check("...with the hosted directory spelling",
      dep_dir.name == "Brake_Library", dep_dir.name)

cfg_with_lib = settings.to_config(settings.load_profile("work"), "ac", store=STORE)
check("the model now resolves the library as a resource",
      "Brake Library" in cfg_with_lib.resources(), str(cfg_with_lib.resources()))
check("...and nothing is missing", cfg_with_lib.missing_dependencies() == [],
      str(cfg_with_lib.missing_dependencies()))

status, body, _ = fetch("/library", data={
    "nickname": "ac", "resource_name": "Brake Library", "remote_url": LIB,
    "branch": "main"})
check("a duplicate resource name is refused",
      "more than once" in body, body[-400:])

# Its own credential, so that removing it can be shown to clean up.
STORE.set(settings.credential_account(
    "work", settings.dependency_account("ac", "Brake Library")), PLANTED)
status, body, _ = fetch("/library/delete", data={
    "nickname": "ac", "resource_name": "Brake Library"})
check("a library is removed", "Removed the library" in body, body[-300:])
check("...from the model", settings.load_profile("work").find("ac").dependencies == ())
check("**...and its credential is deleted with it**",
      not STORE.get(settings.credential_account(
          "work", settings.dependency_account("ac", "Brake Library"))))
check("...but its clone is left on disk", (dep_dir / ".git").is_dir())

# ---------------------------------------------------------------------------
print("\n8. Removing a model never deletes the model")
# ---------------------------------------------------------------------------

head_before = git(cfg.clone_dir, "rev-parse", "HEAD")
status, body, _ = fetch("/model/delete", data={"nickname": "ac"})
check("the model is removed from the profile", "Removed ac" in body, body[-300:])
check("...and the page says the clone was kept", "left on disk" in body)
check("**...and the clone really is still there**",
      (cfg.clone_dir / ".git").is_dir() and
      git(cfg.clone_dir, "rev-parse", "HEAD") == head_before)
check("...and its credential is gone",
      not STORE.get(settings.credential_account("work", "ac")))

# ---------------------------------------------------------------------------
print("\n9. The launch command, and the foreground --setup path")
# ---------------------------------------------------------------------------

command = setup_app.launch_command()
check("the launch command names the real interpreter",
      sys.executable.lower() in command.lower(), command)
check("...and the launcher, not the server module",
      "run_capella_fabric.py" in command and "stdio_server.py" not in command,
      command)
check("...and carries no profile name, so it is the same line for every agent",
      "--profile" not in command, command)
check("with a profile it is still offered", "--profile" in
      setup_app.launch_command("work"), setup_app.launch_command("work"))
check("the launcher it names exists", setup_app.launcher_path().is_file(),
      str(setup_app.launcher_path()))

child_env = {k: v for k, v in os.environ.items() if not k.startswith("PYTHON")}
foreground = subprocess.Popen(
    [sys.executable, str(setup_app.launcher_path()), "--setup", "--no-browser"],
    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    cwd=str(ROOT), env=child_env)
try:
    link = ""
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline and foreground.poll() is None:
        # The link is printed to stderr, which is the contract: stdout is the
        # JSON-RPC channel for the same launcher under the server path.
        line = foreground.stderr.readline()
        found = re.search(r"http://127\.0\.0\.1:\d+/\?token=\S+", line or "")
        if found:
            link = found.group(0)
            break
    check("**--setup prints a link to stderr and serves it**", bool(link), link)
    if link:
        with urllib.request.build_opener(
                urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
        ).open(link, timeout=30) as answer:
            served = answer.read().decode("utf-8", "replace")
        check("...and the page it serves is the setup page",
              "Capella Fabric setup" in served, served[:200])
finally:
    foreground.terminate()
    try:
        out, _err = foreground.communicate(timeout=30)
    except subprocess.TimeoutExpired:
        foreground.kill()
        out, _err = foreground.communicate()
check("...with nothing on stdout", not (out or "").strip(), (out or "")[:200])

shutil.rmtree(ROOT, ignore_errors=True)

print()
if failures:
    print(f"{len(failures)} CHECK(S) FAILED")
    for name in failures:
        print(f"  - {name}")
    sys.exit(1)
print("setup page: all checks passed")
