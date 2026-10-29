"""note-0118: a phase browse returned every diagram in the model.

Tony, using the Fabric Generator website: pick a phase, leave the diagram search
blank, and you get all 88 diagrams rather than that phase's -- with each row
reporting layer "-", so nothing tells you which phase any of them belongs to.

The unscoping was deliberate (note-0044 item 4). capellambse's per-layer diagram
accessors filter on a fixed viewpoint string, and functional-chain / sequence /
class diagrams carry viewpoint "Common", matching none of the four -- so they
were invisible from every phase. Unscoping fixed that by hiding nothing.

THE FIRST ATTEMPT AT SCOPING GOT THIS WRONG, and Tony caught it: it grouped by
`viewpoint`, treating "Common" as "belongs to no layer, so show it everywhere."
But `viewpoint` is the SIRIUS viewpoint -- it groups diagram KINDS, not Arcadia
layers. All 26 "Common" diagrams on this model are functional chains, sequence
diagrams, a class diagram and a state machine drawn from SA, LA and PA alike:
"[LFCD] Generate Power" and "[PFCD] Provide Status" both report "Common". So
every phase was showing the other phases' chain diagrams. His words: "common
diagrams are including some Logical diagrams. The types that begin with L are a
giveaway."

The authoritative layer is the diagram's TARGET element's `.layer` -- containment
in the model, not a Sirius grouping and not the "[LFCD]" name prefix a user can
edit. Asserted below: it resolves a layer for all 88 and agrees with `viewpoint`
on all 62 whose viewpoint does name a layer, so it is strictly better
information rather than a different guess.

Measured against the real Trail Power model under capella-fabric's own venv,
because which layer each diagram truly belongs to is a fact about real Capella
content.
"""
import os
import subprocess
import sys
import warnings

_VENV_PY = r"c:\apps\Git_VSCode\Capella_Fabric_Generator\.venv\Scripts\python.exe"
if os.path.exists(_VENV_PY) and os.path.normcase(sys.executable) != os.path.normcase(_VENV_PY):
    sys.exit(subprocess.run([_VENV_PY, __file__]).returncode)

warnings.filterwarnings("ignore")
os.environ["CAPELLA_TOOLS_PATH"] = r"c:\apps\Git_VSCode\Capella_Tools"
sys.path.insert(0, r"c:\apps\Git_VSCode\Capella_Tools")
sys.path.insert(0, r"c:\apps\Git_VSCode\Capella_Fabric_Generator")

from collections import Counter
from pathlib import Path

import capella_service as svc

MODEL = Path(r"c:\apps\Git_VSCode\Capella_Tools\Trail_Power\Trail Power.aird")
model = svc.open_model(MODEL)

every = list(svc._all_diagrams(model))
total = len(every)
by_phase = Counter(svc.diagram_phase(d) for d in every)
assert total > 40, f"fixture thinner than expected: {total}"
print(f"model:             {total} diagrams, by target layer: {dict(by_phase)}")

# ===== 1. viewpoint is NOT the layer -- the defect Tony caught =====
# Stated as an assertion so nobody "simplifies" diagram_phase back to viewpoint.
common = [d for d in every if svc._viewpoint_of(d) == "Common"]
phases_in_common = {svc.diagram_phase(d) for d in common}
assert len(common) > 10, len(common)
assert len(phases_in_common) > 1, (
    "the whole defect: 'Common' spans several layers, so it cannot be treated "
    f"as one cross-cutting bucket. Got {phases_in_common}"
)
mixed = Counter(svc.diagram_phase(d) for d in common)
print(f"viewpoint 'Common': {len(common)} diagrams spanning {dict(mixed)} "
      "-- not one cross-cutting group")
# And the giveaway he read off the screen, confirmed at the data level.
lfcd = [d for d in common if d.name.startswith("[LFCD]")]
assert lfcd and all(svc.diagram_phase(d) == "LA" for d in lfcd), \
    "[LFCD] diagrams must resolve to LA, not to a 'Common' bucket"
print(f"                   e.g. {len(lfcd)} [LFCD] diagrams, all -> LA")

# ===== 2. target.layer is better information, not a different guess =====
# Where viewpoint DOES name a layer, the two must agree; where it doesn't,
# target.layer must still answer. Both halves matter.
VP_PHASE = {"Operational Analysis": "OA", "System Analysis": "SA",
            "Logical Architecture": "LA", "Physical Architecture": "PA",
            "EPBS architecture": "EPBS"}
agree = disagree = 0
for d in every:
    expected = VP_PHASE.get(svc._viewpoint_of(d) or "")
    if not expected:
        continue
    if svc.diagram_phase(d) == expected:
        agree += 1
    else:
        disagree += 1
assert disagree == 0, f"{disagree} diagrams where target.layer contradicts the viewpoint"
assert agree > 50, agree
assert by_phase.get(None, 0) == 0, \
    f"{by_phase[None]} diagrams could not be placed at all -- investigate before trusting this"
print(f"cross-check:       agrees with viewpoint on all {agree} that name a layer, "
      f"and places the other {total - agree} too")

# ===== 3. A phase browse no longer returns the whole model =====
# Unplaceable-by-design diagrams (EPBS, or an unresolvable target) appear under
# every phase, so they are added to each expected count.
unclaimed = sum(n for ph, n in by_phase.items() if ph not in svc.BROWSE_PHASES)
counts = {}
for phase in svc.BROWSE_PHASES:
    rows = svc.search_by_name(model, phase, "Diagram", "")
    counts[phase] = len(rows)
    assert len(rows) < total, \
        f"{phase} still returns everything ({len(rows)} of {total}) -- the phase is ignored"
    expected = by_phase.get(phase, 0) + unclaimed
    assert len(rows) == expected, f"{phase}: {len(rows)} rows, expected {expected}"
print("phase browse:      " + ", ".join(f"{p} {n}" for p, n in counts.items())
      + f" (was {total} for every phase)")

# ===== 4. Nothing is hidden: every diagram is reachable from some phase =====
# The property that protects note-0044's fix. A count assertion would not.
reachable = set()
for phase in svc.BROWSE_PHASES:
    reachable |= {r["uuid"] for r in svc.search_by_name(model, phase, "Diagram", "")}
assert reachable == {str(d.uuid) for d in every}, \
    "scoping made some diagrams unreachable from every phase -- exactly the note-0044 bug"
print(f"nothing hidden:    all {total} diagrams still reachable across the four phases")

# ===== 5. No leakage: a phase shows only its own, plus the unplaceable =====
# This is the assertion the viewpoint-based version failed.
for phase in svc.BROWSE_PHASES:
    rows = svc.search_by_name(model, phase, "Diagram", "")
    for r in rows:
        assert r["layer"] in (phase, "EPBS", "—"), \
            f"{phase} browse returned a {r['layer']} diagram: {r['name']}"
print("no leakage:        every row in a phase browse is that phase's own, or unplaceable")

# Named explicitly, because these are the rows Tony was looking at.
oa_rows = svc.search_by_name(model, "OA", "Diagram", "")
for prefix in ("[LFCD]", "[PFCD]", "[SFCD]"):
    assert not any(r["name"].startswith(prefix) for r in oa_rows), \
        f"OA is still showing {prefix} diagrams"
print("                   OA shows no [LFCD]/[PFCD]/[SFCD] chain diagrams")

# ===== 6. The unplaceable ones appear under EVERY phase, not none =====
epbs = {str(d.uuid) for d in every if svc.diagram_phase(d) not in svc.BROWSE_PHASES}
if epbs:
    for phase in svc.BROWSE_PHASES:
        got = {r["uuid"] for r in svc.search_by_name(model, phase, "Diagram", "")}
        assert epbs <= got, f"{phase} lost the diagrams belonging to no browse phase"
    print(f"unplaceable:       {len(epbs)} (EPBS) visible from all four phases, not hidden")

# ===== 7. Every row says which layer it is =====
# The original complaint was as much about this: 88 rows all reporting "—".
pa_rows = svc.search_by_name(model, "PA", "Diagram", "")
assert all(r["layer"] != "—" for r in pa_rows if svc.diagram_phase), "rows still unlabelled"
assert {r["layer"] for r in pa_rows} <= {"PA", "EPBS"}, {r["layer"] for r in pa_rows}
sample = next(r for r in pa_rows if r["name"].startswith("[PFCD]"))
assert sample["layer"] == "PA" and sample["viewpoint"] == "Common", sample
print(f"row labelling:     {sample['name'][:32]!r} -> layer PA "
      f"(viewpoint still reported as {sample['viewpoint']!r})")

# ===== 8. Name search still works, and stays scoped =====
named = svc.search_by_name(model, "PA", "Diagram", "[PAB]")
assert named and len(named) < len(pa_rows), (len(named), len(pa_rows))
assert all("[PAB]" in r["name"] for r in named)
print(f"name search:       '[PAB]' in PA -> {len(named)} of {len(pa_rows)} rows")

# ===== 9. Non-diagram types are untouched =====
comps = svc.search_by_name(model, "PA", "Component", "")
assert comps and all("viewpoint" not in c for c in comps), \
    "viewpoint leaked onto non-diagram results"
assert all(c["layer"] == "PA" for c in comps), "component layers changed"
print(f"unrelated types:   PA Component still {len(comps)} rows, no viewpoint field")

print("\nALL NOTE-0118 DIAGRAM-PHASE CHECKS PASSED")
