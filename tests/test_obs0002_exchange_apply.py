"""OBS-0002: the `exchange:` shorthand has to create a real FunctionalExchange
in a real model, not just survive pre-processing. This is the first test in the
repo that calls svc.apply_patch, so most of the code below is about not writing
to anything that matters.

Trail Power is a SHARED, version-controlled working copy in the sibling
Capella_Tools repo, not a disposable fixture. Everything here runs against a
copy in the system temp directory, and the original's size and mtime are
recorded before and asserted unchanged at the end -- because the failure mode
of a copy step that silently no-ops is that the test quietly starts mutating
Tony's model.

Trail Power is flat (all 18 activities directly under the root), so the owner
computed here is always the root activity. The non-trivial lowest-common-
ancestor cases are unit tested on a stub tree in
test_obs0002_exchange_sugar.py; this file proves the write actually lands.

The three sabotage sections at the end each prove a different guard is live
rather than dead code, and each asserts the model file is untouched -- which is
what shows the refusal happened before anything was saved.
"""
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

_CAPELLA_VENV_PY = r"c:\apps\Git_VSCode\Capella_Fabric_Generator\.venv\Scripts\python.exe"
if os.path.exists(_CAPELLA_VENV_PY) and os.path.normcase(sys.executable) != os.path.normcase(_CAPELLA_VENV_PY):
    sys.exit(subprocess.run([_CAPELLA_VENV_PY, __file__]).returncode)

import warnings

warnings.filterwarnings("ignore")
os.environ["CAPELLA_TOOLS_PATH"] = r"c:\apps\Git_VSCode\Capella_Tools"
sys.path.insert(0, r"c:\apps\Git_VSCode\Capella_Tools")
sys.path.insert(0, r"c:\apps\Git_VSCode\Capella_Fabric_Generator")
import capella_service as svc

SOURCE_DIR = Path(r"c:\apps\Git_VSCode\Capella_Tools\Trail_Power")
AIRD_NAME = "Trail Power.aird"
ORIGINAL = SOURCE_DIR / AIRD_NAME
assert ORIGINAL.exists(), f"fixture model not found: {ORIGINAL}"

# The original must be byte-identical when this test finishes.
_orig_before = (ORIGINAL.stat().st_size, ORIGINAL.stat().st_mtime_ns)

tmp_root = Path(tempfile.mkdtemp(prefix="obs0002_"))
try:
    work = tmp_root / "Trail_Power"
    # The whole directory: capellambse needs the .capella and .afm siblings.
    shutil.copytree(SOURCE_DIR, work)
    aird = work / AIRD_NAME
    assert aird.exists(), "copy did not produce an .aird"
    assert str(aird).startswith(str(Path(tempfile.gettempdir()))), (
        f"refusing to run: {aird} is not under the temp directory. A no-op copy "
        "would point this test at the shared working copy."
    )
    print(f"setup: working on a copy under {tempfile.gettempdir()}")

    def aird_fingerprint():
        return (aird.stat().st_size, aird.stat().st_mtime_ns)

    # apply_patch reads only these two keys off the session.
    session = {"aird_path": str(aird), "resources": None}

    # --- pick two real activities, rather than hardcoding uuids -------------
    # Everything needed later is read out as plain uuid strings while the model
    # is open, so no stale model object is held across an apply_patch call.
    model = svc.open_model(str(aird))
    activities = [a for a in model.oa.all_activities if type(a).__name__ == "OperationalActivity"]
    leaves = [a for a in activities
              if type(getattr(a, "parent", None)).__name__ == "OperationalActivity"]
    assert len(leaves) >= 2, f"fixture thinner than expected: {len(leaves)} nested activities"
    source_uuid = leaves[0].uuid
    target_uuid = leaves[1].uuid
    expected_owner = leaves[0].parent.uuid
    other_owner = next(a.uuid for a in activities if a.uuid != expected_owner)
    entity_uuid = next(e.uuid for e in model.oa.all_entities
                       if type(e).__name__ == "Entity")
    sysfn_uuid = next(f.uuid for f in model.sa.all_functions
                      if type(f).__name__ == "SystemFunction")
    exchanges_before = sum(len(a.exchanges) for a in activities)
    assert exchanges_before > 5, f"fixture thinner than expected: {exchanges_before} OA exchanges"
    print(f"setup: {len(activities)} activities, {exchanges_before} OA exchanges")
    del model, activities, leaves

    # --- 1. the happy path --------------------------------------------------
    patch = f"""
- exchange:
    name: OBS-0002 Test Interaction
    description: Written by the test, with an <angle bracketed> word and an & sign
    source: !uuid {source_uuid}
    target: !uuid {target_uuid}
"""
    result = svc.apply_patch(session, patch)
    assert result == {"status": "ok"}, result

    check = svc.open_model(str(aird))
    acts = [a for a in check.oa.all_activities]
    found = [e for a in acts for e in a.exchanges if e.name == "OBS-0002 Test Interaction"]
    assert len(found) == 1, f"expected exactly one new exchange, got {len(found)}"
    created = found[0]
    assert type(created).__name__ == "FunctionalExchange", type(created).__name__
    assert created.source.uuid == source_uuid, created.source
    assert created.target.uuid == target_uuid, created.target
    assert created.parent.uuid == expected_owner, created.parent
    assert type(created.parent).__name__ == "OperationalActivity"
    assert sum(len(a.exchanges) for a in acts) == exchanges_before + 1
    assert "&lt;angle bracketed&gt;" in str(created.description), (
        f"description was not escaped: {created.description!r}"
    )
    print("created: the interaction exists, owned by the computed activity, endpoints intact")
    print("created: the description survived capellambse's HTML repair")
    del check

    after_write = aird_fingerprint()

    # --- 2. sabotage: validation fires, before anything is written ----------
    bad = f"""
- exchange:
    name: Should Not Exist
    source: !uuid {source_uuid}
    target: !uuid {entity_uuid}
"""
    result = svc.apply_patch(session, bad)
    assert result["status"] == "error", result
    assert "OperationalActivity" in result["message"], result["message"]
    assert aird_fingerprint() == after_write, (
        "the model file changed on a refused patch -- the refusal must happen "
        "before decl.apply and before model.save"
    )
    print("sabotage 1: an Entity endpoint is refused and nothing is written")

    # --- 3. sabotage: post-apply verification fires -------------------------
    # The descriptor has to disagree with what was actually written, or the
    # comparison is not exercised. Patching the LCA helper does NOT do this --
    # the generated patch and the descriptor both come from its return value,
    # so they agree on the lie and the check passes. Instead let expansion run
    # normally and then tamper with the recorded expectation, so the model gets
    # the real owner while the descriptor expects a different one.
    real_expand = svc._expand_oa_exchanges

    def _tampered_expand(model_, patch_data):
        checks = real_expand(model_, patch_data)
        for c in checks:
            c["owner"] = other_owner
        return checks

    svc._expand_oa_exchanges = _tampered_expand
    try:
        result = svc.apply_patch(session, patch.replace("Test Interaction", "Mismatched"))
    finally:
        svc._expand_oa_exchanges = real_expand
    assert result["status"] == "error", result
    assert "did not verify" in result["message"], result["message"]
    assert "owner is" in result["message"], result["message"]
    assert aird_fingerprint() == after_write, (
        "the model file changed although verification failed -- model.save must not "
        "run when the post-apply check raises"
    )
    print("sabotage 2: a wrong owner is caught after apply and nothing is saved")

    # --- 4. sabotage: the old refusals survived the narrowing ---------------
    still_refused = f"""
- parent: !uuid {expected_owner}
  extend:
    component_exchanges:
      - name: Should Not Exist
"""
    result = svc.apply_patch(session, still_refused)
    assert result["status"] == "error", result
    assert "component_exchanges" in result["message"], result["message"]
    assert aird_fingerprint() == after_write
    print("sabotage 3: component_exchanges is still refused outright")

    sa_patch = f"""
- parent: !uuid {sysfn_uuid}
  extend:
    exchanges:
      - name: Should Not Exist
        source: !uuid {source_uuid}
        target: !uuid {target_uuid}
"""
    result = svc.apply_patch(session, sa_patch)
    assert result["status"] == "error", result
    assert "SystemFunction" in result["message"], result["message"]
    assert aird_fingerprint() == after_write
    print("sabotage 4: exchanges under a SA function is still refused")

finally:
    shutil.rmtree(tmp_root, ignore_errors=True)

assert (ORIGINAL.stat().st_size, ORIGINAL.stat().st_mtime_ns) == _orig_before, (
    f"THE SHARED FIXTURE WAS MODIFIED: {ORIGINAL}. Restore it from git."
)
print("teardown: the shared Trail Power model is untouched")

print("\nALL OBS-0002 EXCHANGE-APPLY CHECKS PASSED")
