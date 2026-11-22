"""The local server must reach nothing hosted, nothing Cartenza, and advertise
exactly the sixteen tools it supports -- measured over a real stdio session.

Step 8 of the stdio plan, modelled on the notebook's own boundary test.

### Why a test and not a convention

Three separate boundaries meet in this repository, and a directory layout only
makes crossing them *awkward*:

1. **The hosted service.** ``capella_service.py``, ``git_service.py``,
   ``mcp_server.py`` and ``app.py`` are live on a droplet. The local server
   adds itself by assigning one function onto ``capella_service._session_dir``
   and nothing else; if ``mcp_server`` ever pulled ``capella_bundle`` in, the
   hosted service would start depending on a desktop library it does not have.
2. **Cartenza.** Capella Fabric is being separated from Cartenza on IP
   grounds -- ``Tony_BackLog``/note-0047 ("peeling fabric off helps eliminate
   IP issues, since fabric started as Siemens support work") and
   ``capella_fabric``/note-0001 ("shares no dependency or IP relationship").
   The foundation library is deliberately a neutral package with no knowledge
   format and no Cartenza naming; this asserts the consumer honours that too,
   in its import graph *and* in its source.
3. **The web application.** The setup page enters credentials. An MCP server
   has no business holding that code, so it must be reached only from inside a
   function call.

A test makes each one fail. That is the guarantee that survives someone adding
an import in a hurry.

### The second half: what is advertised

A tool that exists and answers "that needs the Cartenza website" is worse than
one never advertised -- a harness choosing tools cannot tell a broken
capability from a missing one and will keep reaching for it. So the hosted
tools must be *absent*, asserted by name rather than by count, since a count
passes while the wrong two are missing.

And the instructions are checked for naming a tool that does not exist, which
is the failure nobody tests by hand: it surfaces on a live agent's first turn.
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(r"c:\apps\Git_VSCode\Capella_Fabric_Generator")
sys.path.insert(0, str(REPO))

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if ok else 'FAIL'} {label}" + (f": {detail}" if detail and not ok else ""))
    if not ok:
        failures.append(label)


#: Anything belonging to the hosted service, to Cartenza, or to a dependency
#: neither of them should drag in here.
#:
#: ``starlette`` and ``uvicorn`` are deliberately NOT here: the MCP library
#: imports them itself for its HTTP transports, so banning them would be
#: banning ``mcp``, and they carry no hosted behaviour of their own.
FORBIDDEN_TOPS = (
    # The hosted Capella service's own front ends.
    "mcp_server",
    "app",
    "flask",
    # Cartenza. The IP separation note-0047 and note-0001 record.
    "knowledge_repo",
    "engineering_notebook",
    "viewer",
    "mcp_common",
    "kp",
    # Cartenza's hosted dependencies.
    "auth",
    "sqlalchemy",
    "fastapi",
    "stripe",
    "resend",
)

FORBIDDEN_MODULES = (
    # A web application that enters credentials, reached only from inside
    # setup_model() and from the launcher's --setup.
    "capella_bundle.setup_app",
    # Lazily imported by _pages() on first use, so that a server which never
    # renders anything never starts a web framework's router.
    "capella_bundle.page_server",
)

PROBE = r"""
import json, sys
sys.path.insert(0, %(repo)r)
before = set(sys.modules)
import stdio_server
new = sorted(set(sys.modules) - before)
tools = sorted(stdio_server.mcp._tool_manager._tools)
print("@@@" + json.dumps({"modules": new, "tools": tools}))
"""

HOSTED_PROBE = r"""
import json, sys
sys.path.insert(0, %(repo)r)
import mcp_server
print("@@@" + json.dumps({"modules": sorted(sys.modules)}))
"""


def probe(source: str) -> dict:
    """Import something in a clean ``-I`` process and report what it pulled.

    A subprocess, not an import here: this file has already imported other
    things, so ``sys.modules`` in this process says what the *suite* loaded
    rather than what the subject loads. ``-I`` also means no ``PYTHONPATH``
    and no user site directory, so nothing arrives by a route the test did
    not intend -- which on this machine additionally sidesteps a stale
    editable install that has caused one long investigation.
    """
    result = subprocess.run(
        [sys.executable, "-I", "-c", source % {"repo": str(REPO)}],
        capture_output=True, text=True, cwd=str(REPO),
    )
    marker = [line for line in result.stdout.splitlines()
              if line.startswith("@@@")]
    if not marker:
        raise AssertionError(
            "the probe produced no result:\n"
            f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}")
    return json.loads(marker[0][3:])


info = probe(PROBE)
modules = info["modules"]
tops = {name.split(".")[0] for name in modules}

# ---------------------------------------------------------------------------
print("1. The import closure reaches nothing hosted and nothing Cartenza")
# ---------------------------------------------------------------------------

for banned in FORBIDDEN_TOPS:
    check(f"{banned} is not imported", banned not in tops)
for banned in FORBIDDEN_MODULES:
    check(f"{banned} is not imported", banned not in modules)

# The control. Every absence above is satisfied by a probe that imported
# nothing at all, which is how ten checks in this family once passed for the
# wrong reason.
check("**the control: it really did import the server**",
      "stdio_server" in modules or "capella_bundle" in tops, str(modules[:8]))
check("...and capella_service with it", "capella_service" in modules)
check("...and the foundation library", "mcp_desktop" in tops)
check("...and capellambse, which is the point of the whole thing",
      "capellambse" in tops)
print(f"  ({len(modules)} modules in total; {len(tops)} top-level packages)")

# ---------------------------------------------------------------------------
print("\n2. The hosted server does not import any of this")
# ---------------------------------------------------------------------------
# The sharper form of "the shared files are untouched". They could be
# byte-identical and still be broken by this work if something they import
# started importing capella_bundle -- and the droplet has no mcp_desktop
# checkout beside it, so that would be an ImportError on a live service.
#
# Asserting the hosted server does not import it at all is stronger than
# testing it in a tree without the library: what is never imported cannot
# fail to import.

hosted = set(probe(HOSTED_PROBE)["modules"])
check("**the control: the hosted server really was imported**",
      "mcp_server" in hosted)
# The second control, which proves the *detector* rather than the subject:
# the same probe pointed at this server reports capella_bundle as present. So
# the absences below are the hosted server not importing it, rather than this
# probe being unable to see it either way. This is checked instead of
# temporarily adding an import to mcp_server.py, which is a file on a live
# droplet and not one to mutate for a test.
ours_probe = probe(PROBE)["modules"]
check("**the control: the same probe does see capella_bundle when it is there**",
      "capella_bundle" in ours_probe, str(ours_probe[:6]))
for ours in ("capella_bundle", "capella_bundle.session", "capella_bundle.config",
             "capella_bundle.settings", "capella_bundle.page_server",
             "capella_bundle.setup_app", "stdio_server", "mcp_desktop",
             "run_capella_fabric"):
    check(f"...and it does not import {ours}", ours not in hosted)

# ---------------------------------------------------------------------------
print("\n3. The source carries no Cartenza dependency and no Cartenza branding")
# ---------------------------------------------------------------------------
# Parsed, not grepped. The first version of this scanned lines and skipped
# anything beginning with a quote or a hash -- which catches a one-line
# docstring and misses every *continuation* line of a multi-line one, so it
# reported four comparisons-to-the-notebook written deliberately in prose.
# A line-based check cannot tell an explanation from a dependency, and the
# explanations are the part worth keeping: foundation.py's note about
# CARTENZA_MCP_DESKTOP exists precisely to say it is not read.
#
# So two precise questions instead, each answered from the syntax tree.

import ast  # noqa: E402  -- used only by this section

SOURCES = sorted((REPO / "capella_bundle").glob("*.py")) + \
    [REPO / "stdio_server.py", REPO / "run_capella_fabric.py"]

CARTENZA_PACKAGES = {"knowledge_repo", "engineering_notebook", "viewer",
                     "mcp_common", "auth", "kp"}


def docstring_nodes(tree: ast.AST) -> set[int]:
    """The ids of every string node that is a docstring, to exempt them."""
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            body = getattr(node, "body", None) or []
            if body and isinstance(body[0], ast.Expr) \
                    and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                found.add(id(body[0].value))
    return found


# 3a. No import of a Cartenza package, anywhere, at any nesting. Section 1
# proves the runtime graph; this proves no conditional or lazy branch carries
# one that only fires on a machine where it would succeed.
bad_imports = []
for source in SOURCES:
    tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module or ""]
        else:
            continue
        for name in names:
            if name.split(".")[0] in CARTENZA_PACKAGES:
                bad_imports.append(f"{source.name}:{node.lineno}: {name}")
check("**no Cartenza package is imported, even conditionally**",
      not bad_imports, "; ".join(bad_imports[:4]))
check("**the control: the parse really found the imports**",
      any(isinstance(n, (ast.Import, ast.ImportFrom))
          for n in ast.walk(ast.parse(
              (REPO / "stdio_server.py").read_text(encoding="utf-8")))))

# 3b. No Cartenza branding in a string this product ships -- a path, a
# keychain service, a cookie, a message. Docstrings are exempt, being
# developer prose rather than anything a user or a disk ever sees.
branded = []
for source in SOURCES:
    text = source.read_text(encoding="utf-8")
    tree = ast.parse(text, filename=str(source))
    exempt = docstring_nodes(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                and id(node) not in exempt \
                and "cartenza" in node.value.lower():
            branded.append(f"{source.name}:{node.lineno}: {node.value[:50]}")
check("**no shipped string carries Cartenza branding**",
      not branded, "; ".join(branded[:4]))

# The namespace constants, named rather than inferred: these are what land on
# disk and in the operating system's credential store, and two products
# sharing any of them would let either read or reach the other's.
from capella_bundle import config as bconfig  # noqa: E402
from capella_bundle import page_server as bpages  # noqa: E402
from capella_bundle import settings as bsettings  # noqa: E402
from capella_bundle import setup_app as bsetup  # noqa: E402

for label, value, notthis in (
    ("the state directory", bconfig.APP, "Cartenza"),
    ("the keychain service", bsettings.SERVICE, "cartenza-engineering-notebook"),
    ("the setup cookie", bsetup.COOKIE, "cartenza_setup"),
    ("the pages cookie", bpages.COOKIE, "cartenza_pages"),
):
    check(f"{label} is this product's own ({value!r})",
          "cartenza" not in value.lower() and value != notthis, value)

# The one that is not negotiable in any form: an agent id, which OBS-0010
# says never belongs in a repository or a bundle configuration.
idish = []
for source in sorted((REPO / "capella_bundle").glob("*.py")) + \
        [REPO / "stdio_server.py"]:
    text = source.read_text(encoding="utf-8")
    if re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
                 text):
        idish.append(source.name)
check("no uuid-shaped identifier is hard-coded anywhere",
      not idish, str(idish))

# And no credential-shaped literal, which is the other thing that must never
# reach a repository.
secrets = []
for source in sorted((REPO / "capella_bundle").glob("*.py")) + \
        [REPO / "stdio_server.py"]:
    text = source.read_text(encoding="utf-8")
    for pattern in (r"ghp_[A-Za-z0-9]{20,}", r"glpat-[A-Za-z0-9_-]{15,}",
                    r"github_pat_[A-Za-z0-9_]{20,}"):
        if re.search(pattern, text):
            secrets.append(f"{source.name}: {pattern}")
check("no credential-shaped literal is in the shipped source",
      not secrets, str(secrets))

# ---------------------------------------------------------------------------
print("\n4. Every module imports on its own, from anywhere")
# ---------------------------------------------------------------------------
# A package whose modules only import in a particular order, or only from the
# repository root, is a package that fails inside a bundle for a reason that
# reads as a broken install. Python guarantees the package __init__ runs
# first, which is what the foundation bootstrap relies on.

for module in ("capella_bundle.config", "capella_bundle.settings",
               "capella_bundle.session", "capella_bundle.instructions",
               "capella_bundle.page_server", "capella_bundle.setup_app",
               "capella_bundle.foundation"):
    done = subprocess.run(
        [sys.executable, "-I", "-c",
         f"import sys; sys.path.insert(0, r'{REPO}'); import {module}; print('ok')"],
        capture_output=True, text=True, cwd=str(Path(sys.executable).parent))
    check(f"{module} imports alone, from an unrelated directory",
          done.returncode == 0 and "ok" in done.stdout, done.stderr[-200:])

# ---------------------------------------------------------------------------
print("\n5. The hosted split adds up, and the absences are by name")
# ---------------------------------------------------------------------------

EXPECTED = {
    "setup_model", "use_profile", "list_my_scopes", "open_model",
    "close_model", "list_object_types", "browse_model",
    "search_model_objects", "resolve_model_uuids", "verify_model",
    "generate_fabric", "render_diagram_view", "generate_viewer_link",
    "apply_model_patch", "push_model_changes", "pull_model_changes",
}

MUST_BE_ABSENT = {
    # Connect codes need the Cartenza database and website.
    "begin_connect",
    # Library repositories are declared and cloned on the setup page, because
    # that is where slow work and credentials belong.
    "add_dependency_repo",
    # Renamed for what they now do, so the old names must not answer: one
    # never clones and the other never deletes.
    "clone_capella_repo",
    "cleanup_session",
}

in_process = set(info["tools"])
check("the server registers exactly sixteen tools", len(in_process) == 16,
      f"{len(in_process)}: {sorted(in_process)}")
check("...and they are exactly the expected set", in_process == EXPECTED,
      str(in_process ^ EXPECTED))

hosted_source = (REPO / "mcp_server.py").read_text(encoding="utf-8")
hosted_tools = set(re.findall(r"@mcp\.tool\(\)\s*\ndef (\w+)\(", hosted_source))
HOSTED_ONLY = {"begin_connect"}
MOVED = {"add_dependency_repo"}
RENAMED = {"clone_capella_repo": "open_model", "cleanup_session": "close_model"}
LOCAL_ONLY = {"setup_model", "use_profile", "list_my_scopes",
              "generate_viewer_link"}

check("the hosted server still has fourteen tools", len(hosted_tools) == 14,
      f"{len(hosted_tools)}: {sorted(hosted_tools)}")
keepers = hosted_tools - HOSTED_ONLY - MOVED
check("...minus one hosted-only and one moved leaves twelve",
      len(keepers) == 12, f"{len(keepers)}: {sorted(keepers)}")
check("...and twelve plus four local-only is sixteen",
      len(keepers) + len(LOCAL_ONLY) == len(EXPECTED))
print(f"  hosted {len(hosted_tools)} - {len(HOSTED_ONLY)} hosted-only "
      f"- {len(MOVED)} moved = {len(keepers)} keepers "
      f"({len(RENAMED)} renamed) + {len(LOCAL_ONLY)} local-only "
      f"= {len(keepers) + len(LOCAL_ONLY)}")
check("every renamed tool was a hosted one and its new name is registered",
      all(old in hosted_tools and new in in_process
          for old, new in RENAMED.items()))
for name in sorted(MUST_BE_ABSENT):
    check(f"{name} is absent in process", name not in in_process)

# ---------------------------------------------------------------------------
print("\n6. The wire agrees, and a real tool call answers")
# ---------------------------------------------------------------------------
# Everything above reads mcp._tool_manager._tools, a private attribute in a
# subprocess. What a client actually receives is a tools/list response over
# stdio, and those are not the same claim. This section talks to the server the
# way a harness does, through the launcher the setup page prints.
#
# It also exercises the thing that would otherwise break silently: stdout is
# the JSON-RPC channel, so any stray print in the start-up path corrupts the
# first message and the handshake fails outright.


class Wire:
    """A live stdio MCP session against the launcher."""

    def __init__(self) -> None:
        env = {k: v for k, v in os.environ.items()
               if not k.startswith("PYTHON")}
        env["PYTHONIOENCODING"] = "utf-8"
        self.proc = subprocess.Popen(
            [sys.executable, str(REPO / "run_capella_fabric.py")],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, encoding="utf-8",
            bufsize=1, env=env, cwd=str(REPO))

    def send(self, obj) -> None:
        self.proc.stdin.write(json.dumps(obj) + "\n")
        self.proc.stdin.flush()

    def read(self):
        while True:
            line = self.proc.stdout.readline()
            if not line:
                return None
            line = line.strip()
            if line:
                return json.loads(line)

    def call(self, name: str, arguments: dict, request_id: int):
        self.send({"jsonrpc": "2.0", "id": request_id, "method": "tools/call",
                   "params": {"name": name, "arguments": arguments}})
        return self.read()

    def close(self) -> str:
        try:
            self.proc.stdin.close()
        except Exception:
            pass
        try:
            self.proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        try:
            return self.proc.stderr.read()
        except Exception:
            return ""


wire = Wire()
try:
    wire.send({"jsonrpc": "2.0", "id": 1, "method": "initialize",
               "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                          "clientInfo": {"name": "boundary-test",
                                         "version": "0"}}})
    init = wire.read()
    check("**the handshake completed over stdio**", init is not None,
          "nothing came back -- a stray write to stdout would do this")
    result = (init or {}).get("result") or {}
    instructions = result.get("instructions", "")
    check("...and the server named itself",
          (result.get("serverInfo") or {}).get("name") == "Capella Fabric",
          str(result.get("serverInfo")))

    wire.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
    wire.send({"jsonrpc": "2.0", "id": 2, "method": "tools/list",
               "params": {}})
    listed = wire.read()
    wire_tools = {t["name"] for t in ((listed or {}).get("result") or {}).get("tools", [])}
    check("the wire lists exactly the same sixteen", wire_tools == EXPECTED,
          f"only wire: {sorted(wire_tools - EXPECTED)}; "
          f"only in-process: {sorted(EXPECTED - wire_tools)}")
    for name in sorted(MUST_BE_ABSENT):
        check(f"{name} is absent on the wire", name not in wire_tools)

    # A real call, end to end. list_object_types needs no session and no
    # configuration, so it answers on any machine -- and it reaches all the
    # way into capellambse, which is what makes it a wire test rather than a
    # second tool listing.
    answered = wire.call("list_object_types", {}, 3)
    payload = (answered or {}).get("result") or {}
    text = "".join(part.get("text", "")
                   for part in payload.get("content", []))
    check("**a real tool call answers over the wire**",
          bool(text) and not payload.get("isError"), str(answered)[:300])
    try:
        phases = json.loads(text)
    except Exception:
        phases = {}
    check("...with the four Arcadia phases",
          set(phases) >= {"OA", "SA", "LA", "PA"}, str(sorted(phases))[:200])
    check("...each carrying object types",
          all(isinstance(v, list) and v for v in phases.values()),
          str(phases)[:200])

    # And a call that must fail, failing *readably*: an unconfigured machine
    # is the normal first-run state, and what an agent gets back has to be a
    # sentence it can act on rather than a transport error.
    refused = wire.call("open_model", {}, 4)
    body = (refused or {}).get("result") or {}
    said = "".join(part.get("text", "") for part in body.get("content", []))
    check("an unconfigured open_model is refused, not a crash",
          refused is not None and "error" not in refused, str(refused)[:200])
    check("...with a sentence naming what to do",
          "setup_model" in said or "use_profile" in said or "profile" in said.lower(),
          said[:300])
    check("...and no traceback", "Traceback" not in said, said[:200])

    # The instructions, checked for the failure nobody tests by hand: naming
    # a tool that does not exist. That surfaces on a live agent's first turn.
    check("the server sends its instructions", bool(instructions.strip()))
    named = set(re.findall(r"\b([a-z_][a-z0-9_]{3,})\(\)", instructions))
    unknown = named - EXPECTED
    check("**every tool the instructions name exists**", not unknown,
          f"unknown: {sorted(unknown)}")
    check("...and they do name the entry point",
          "list_my_scopes" in named and "open_model" in named,
          str(sorted(named)))
    for name in sorted(MUST_BE_ABSENT):
        check(f"the instructions do not offer {name}", name not in instructions)
    check("the instructions name no hosted host",
          "cartenza.ai" not in instructions.lower(), instructions[:200])
finally:
    stderr = wire.close()

check("the start-up banner went to stderr", "Capella Fabric" in stderr,
      stderr[-300:])
check("...and nothing crashed on the way down", "Traceback" not in stderr,
      stderr[-400:])

print()
if failures:
    print(f"{len(failures)} CHECK(S) FAILED")
    for name in failures:
        print(f"  - {name}")
    sys.exit(1)
print("capella boundary: nothing hosted or Cartenza reached, "
      "and the wire lists exactly sixteen")
