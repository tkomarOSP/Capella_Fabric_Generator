"""OBS-0013: generate_fabric parses its own output before reporting success.

The defect this guards is not a MISSING capella_tools -- that raises at import
-- but a WRONG one. A library 15 commits behind returned 607,758 characters of
plausible text that PyYAML refuses to parse, carried no is_actor field at all,
and the call reported success; an agent then acts on fields that are not there.
A parse of our own output is the only check that holds however the library is
configured, and the only one that does not need to know which configuration is
right.

Run against the real Trail Power model from Capella_Tools' examples rather than
a stub, because whether a fabric parses is a fact about the installed library
and a hand-built stand-in would only confirm my own assumptions about it.
"""
import os
import subprocess
import sys
import warnings

# Run under capella-fabric's own venv -- the capellambse it actually deploys.
_VENV_PY = r"c:\apps\Git_VSCode\Capella_Fabric_Generator\.venv\Scripts\python.exe"
if os.path.exists(_VENV_PY) and os.path.normcase(sys.executable) != os.path.normcase(_VENV_PY):
    sys.exit(subprocess.run([_VENV_PY, __file__]).returncode)

warnings.filterwarnings("ignore")
os.environ["CAPELLA_TOOLS_PATH"] = r"c:\apps\Git_VSCode\Capella_Tools"
sys.path.insert(0, r"c:\apps\Git_VSCode\Capella_Tools")
sys.path.insert(0, r"c:\apps\Git_VSCode\Capella_Fabric_Generator")

import tempfile
from pathlib import Path

import yaml
import capella_service as svc
from capella_tools import capellambse_yaml_manager as ymgr

assert os.path.normcase(r"c:\apps\Git_VSCode\Capella_Tools") in os.path.normcase(ymgr.__file__), ymgr.__file__

MODEL = Path(r"c:\apps\Git_VSCode\Capella_Tools\Trail_Power\Trail Power.aird")
assert MODEL.exists(), MODEL

NL = chr(10)
# The exact shape the stale library emitted: an unquoted name containing a colon.
BAD = NL.join(["objects:",
               "  - name: E: Audible Operation",
               "    primary_uuid: deadbeef",
               ""])

checks = 0


def check(label, ok, detail=""):
    global checks
    checks += 1
    assert ok, f"{label}: {detail}"
    print(f"  {label}")


tmp = Path(tempfile.mkdtemp(prefix="obs0013_"))
svc._session_dir = lambda sid: tmp

model = svc.open_model(MODEL)
uuids = [c.uuid for c in model.sa.all_components][:40]
assert len(uuids) >= 3, f"only {len(uuids)} SA components -- too few to conclude anything"


def session(sid):
    return {"aird_path": str(MODEL), "resolved_uuids": uuids, "session_id": sid,
            "include_realized": False, "include_realizing": False}


# ===== 1. Controls first: the inputs are what the rest of the test assumes =====
print("1. controls on the fixtures")
for name in ("SafeLoader", "CSafeLoader"):
    loader = getattr(yaml, name, None)
    if loader is None:
        print(f"  (no {name} on this install)")
        continue
    try:
        yaml.load(BAD, Loader=loader)
        raise AssertionError(f"{name} ACCEPTED the bad fixture -- it is not a negative case")
    except yaml.YAMLError:
        check(f"the bad fixture is genuinely unparseable by {name}", True)

# Behaviour, not ancestry: CSafeLoader is built on the C parser and is NOT a
# yaml.BaseLoader subclass, so an isinstance-shaped check here is wrong and
# would have to be loosened to pass -- which is how a test stops saying
# anything. What matters is that it parses good YAML and rejects bad.
check("_FABRIC_LOADER parses valid YAML",
      yaml.load("a: 1", Loader=svc._FABRIC_LOADER) == {"a": 1},
      repr(svc._FABRIC_LOADER))
try:
    yaml.load(BAD, Loader=svc._FABRIC_LOADER)
    raise AssertionError("_FABRIC_LOADER accepted the bad fixture")
except yaml.YAMLError:
    check("_FABRIC_LOADER rejects the bad fixture", True)
print(f"     (loader in use: {svc._FABRIC_LOADER.__module__}.{svc._FABRIC_LOADER.__name__})")
check("FabricNotParseable is a ValueError, so `except Exception` reports a sentence",
      issubclass(svc.FabricNotParseable, ValueError), svc.FabricNotParseable.__mro__)


# ===== 2. The happy path: a real model, and the FILE's bytes parse =====
print("2. the real model")
path, count, artifacts = svc.generate_fabric(session("good"))
text = Path(path).read_text(encoding="utf-8")
check("a fabric was produced", len(text) > 2000, f"only {len(text)} chars")
check("it reports objects", count >= 5, f"object_count={count}")
parsed = yaml.safe_load(text)          # the file on disk, not the in-memory copy
check("the file on disk parses", parsed is not None, "safe_load returned None")
check("is_actor survived into the fabric", "is_actor" in text, "no is_actor field")
check("no ref__uuid double-underscore key", "ref__uuid" not in text, "ref__uuid present")
print(f"     ({len(text):,} chars, {count} objects, {artifacts} artifacts)")

# Both loaders agree -- the claim the code comment makes for preferring the C one.
if hasattr(yaml, "CSafeLoader"):
    check("SafeLoader and CSafeLoader agree on this fabric",
          yaml.load(text, Loader=yaml.SafeLoader) == yaml.load(text, Loader=yaml.CSafeLoader),
          "the two loaders disagree, so the fast path is not equivalent")


# ===== 3. The guard fires: break the subject, not the test =====
print("3. the guard, with the library's own output replaced by unparseable text")


class BadHandler(ymgr.CapellaYAMLHandler):
    """Everything real except the one method whose output is the defect."""

    def get_yaml_content(self):
        return BAD


real = svc.CapellaYAMLHandler
svc.CapellaYAMLHandler = BadHandler
try:
    raised = None
    try:
        svc.generate_fabric(session("bad"))
    except svc.FabricNotParseable as exc:
        raised = exc
    check("generate_fabric refuses rather than reporting success", raised is not None,
          "no FabricNotParseable raised -- the guard did not fire")

    msg = str(raised)
    check("the message says it is not parseable YAML", "not parseable YAML" in msg, msg[:200])
    check("it names the capella_tools file actually in use",
          "capellambse_yaml_manager" in msg, msg[:300])
    check("it points at the maintained checkout, not a stale one",
          os.path.normcase(r"git_vscode\capella_tools") in os.path.normcase(msg), msg[:300])
    import re
    m = re.search(r"at line (\d+), column (\d+)", msg)
    check("it gives a line and column", m is not None, msg[:300])
    check("it names the file it wrote", "_fabric.txt" in msg, msg[:300])
    check("the parser's own complaint survives", "mapping values" in msg.lower(), msg[:300])

    # The file must still be there: the message tells someone to inspect it.
    written = sorted(tmp.glob("*_fabric.txt"))
    check("the output was still written for inspection", len(written) >= 1,
          f"found {written}")
    body = written[-1].read_text(encoding="utf-8")
    check("the written file holds the offending text",
          "Audible Operation" in body, body[:200])
    # The reported line must index the file a human will open -- which carries a
    # "# Capella Fabric YAML" header line, so a number taken from the raw YAML
    # would be off by one and send them to the wrong line.
    lines = body.splitlines()
    reported = int(m.group(1))
    check("the reported line number indexes the written file",
          1 <= reported <= len(lines) and "Audible Operation" in lines[reported - 1],
          f"line {reported} of the file is {lines[reported - 1]!r}" if reported <= len(lines)
          else f"line {reported} is past the end ({len(lines)} lines)")
finally:
    svc.CapellaYAMLHandler = real

check("the real handler is restored", svc.CapellaYAMLHandler is real)

# And the happy path still works after the restore -- proves the patch was the
# only variable, not that the module was left broken.
path2, count2, _ = svc.generate_fabric(session("good2"))
check("a real fabric still generates after the mutation is undone",
      yaml.safe_load(Path(path2).read_text(encoding="utf-8")) is not None,
      "post-restore fabric does not parse")

print(f"\nALL OBS-0013 FABRIC-PARSE CHECKS PASSED ({checks} checks)")
