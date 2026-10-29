"""note-0118 follow-up: a diagram whose layer won't resolve must not 500 a browse.

Tony hit a server error browsing a LARGE model. Trail Power (88 diagrams, clean)
exercises none of the ways a real .aird can be partly unreadable, so the counts
test cannot catch this class of failure at all.

The exposure is wider than it looks. Placing a diagram now walks
`d.target.layer` -- two lazy property resolutions through capellambse's loader,
per diagram, and `_object_info` reads `.viewpoint` for EVERY object of EVERY
type. `getattr(obj, attr, None)` swallows AttributeError and nothing else, so a
descriptor pointing at a representation that didn't load can raise from inside
capellambse and take down the entire browse -- over a column that is decoration.

Exercised with objects that raise on attribute access, since that is the shape of
the failure whichever capellambse internal produces it. Whether it is what Tony
actually hit is for the log to say; a gunicorn worker timeout on a big model
looks identical from the browser and is a different problem.
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

import capella_service as svc


class _Layer:
    """Stands in for a capellambse architecture-layer object, which is
    recognised by its CLASS NAME -- so the fakes below must be named exactly as
    capellambse names them."""


class LogicalArchitecture(_Layer):
    pass


class PhysicalArchitecture(_Layer):
    pass


class EPBSArchitecture(_Layer):
    pass


def diagram(uuid, name, *, layer=None, viewpoint="Common"):
    """A minimal stand-in for a DRepresentationDescriptor.

    `viewpoint` and `target` are set on the CLASS, not the instance, because
    that is where capellambse defines them -- and _is_diagram deliberately
    inspects the class so it never evaluates a lazy property.
    """
    class T:
        pass
    t = T()
    t.layer = layer() if layer else None
    return type("D", (), {"uuid": uuid, "name": name,
                          "viewpoint": viewpoint, "target": t})()


# --- the failure shapes, one per way capellambse can refuse to answer ---

class NoTargetAttr:
    """No .target at all -- the AttributeError case getattr already handled."""
    uuid = "bad-1"
    name = "[CDB] Descriptor with no target"
    viewpoint = "Common"


class TargetRaises:
    uuid = "bad-2"
    name = "[LAB] Descriptor whose representation did not load"
    viewpoint = "Common"

    @property
    def target(self):
        raise RuntimeError("cannot resolve representation for this descriptor")


class LayerRaises:
    """Target resolves; .layer raises -- KeyError is what a dangling
    reference in the loader looks like."""
    uuid = "bad-3"
    name = "[PAB] Target with a dangling layer reference"
    viewpoint = "Common"

    class _T:
        @property
        def layer(self):
            raise KeyError("layer")

    target = _T()


class ViewpointRaises:
    """Placeable, but the decorative viewpoint field blows up."""
    uuid = "bad-4"
    name = "[LDFB] Viewpoint that will not resolve"

    @property
    def viewpoint(self):
        raise RuntimeError("no viewpoint")

    class _T:
        layer = LogicalArchitecture()

    target = _T()


GOOD_LA = diagram("ok-la", "[LFCD] Generate Power", layer=LogicalArchitecture)
GOOD_PA = diagram("ok-pa", "[PFCD] Provide Status", layer=PhysicalArchitecture)
EPBS = diagram("ok-epbs", "[EAB] Structure", layer=EPBSArchitecture,
               viewpoint="EPBS architecture")
BROKEN = [NoTargetAttr(), TargetRaises(), LayerRaises()]

# ===== 1. diagram_phase absorbs every failure shape =====
for bad in BROKEN:
    assert svc.diagram_phase(bad) is None, bad.__class__.__name__
assert svc.diagram_phase(diagram("n", "no layer", layer=None)) is None
assert svc.diagram_phase(GOOD_LA) == "LA"
assert svc.diagram_phase(GOOD_PA) == "PA"
assert svc.diagram_phase(EPBS) == "EPBS"
print("diagram_phase:     four failure shapes absorbed, real layers still read")

# ===== 2. _viewpoint_of absorbs its own =====
assert svc._viewpoint_of(ViewpointRaises()) is None
assert svc._viewpoint_of(GOOD_LA) == "Common"
print("_viewpoint_of:     a raising viewpoint absorbed")

# ===== 3. _object_info still produces a usable row =====
row = svc._object_info(BROKEN[1])
assert row["uuid"] == "bad-2" and row["layer"] == "—", row
print("_object_info:      unplaceable diagram -> layer '—', row intact")

row = svc._object_info(GOOD_LA)
assert row["layer"] == "LA" and row["viewpoint"] == "Common", row
print("                   a 'Common' logical chain still labelled LA, not Common")

# A diagram that can be placed but whose viewpoint raises must still be placed.
# Resolving the two independently is the point: a decorative field that fails
# must not cost the row its real layer.
row = svc._object_info(ViewpointRaises())
assert row["layer"] == "LA" and "viewpoint" not in row, row
print("                   placeable but viewpoint-less row keeps its LA label")

# ===== 3b. Non-diagram elements are not touched by any of this =====
# Model elements have targets too, so an ungated layer lookup would quietly
# start relabelling them -- and would pay for two lazy resolutions per element
# on every browse.
class FakeExchange:
    """No viewpoint, but it does have a target with a resolvable layer."""
    uuid = "exch-1"
    name = "Energy"

    class _T:
        layer = PhysicalArchitecture()

    target = _T()


assert not svc._is_diagram(FakeExchange()), "an exchange must not read as a diagram"
assert svc._is_diagram(GOOD_LA) and svc._is_diagram(NoTargetAttr())
row = svc._object_info(FakeExchange())
assert row["layer"] == "—" and "viewpoint" not in row, row
print("non-diagrams:      an exchange with a resolvable target is left alone")

# ===== 4. A phase browse survives a model containing them =====
population = BROKEN + [ViewpointRaises(), GOOD_LA, GOOD_PA, EPBS]
_orig = svc._all_diagrams
svc._all_diagrams = lambda m: population
try:
    seen = {ph: svc.search_by_name(None, ph, "Diagram", "")
            for ph in svc.BROWSE_PHASES}
finally:
    svc._all_diagrams = _orig

# LA: its two own (GOOD_LA, ViewpointRaises) + EPBS + the 3 unplaceable = 6.
assert len(seen["LA"]) == 6, [r["name"] for r in seen["LA"]]
# PA: its one own + EPBS + 3 unplaceable = 5. And NOT the logical ones.
assert len(seen["PA"]) == 5, [r["name"] for r in seen["PA"]]
assert not any(r["layer"] == "LA" for r in seen["PA"]), seen["PA"]
# OA has none of its own: EPBS + 3 unplaceable = 4.
assert len(seen["OA"]) == 4, [r["name"] for r in seen["OA"]]
print(f"phase browse:      OA {len(seen['OA'])}, LA {len(seen['LA'])}, "
      f"PA {len(seen['PA'])} rows -- no error raised, no leakage")

# ===== 5. Unplaceable diagrams are hidden from NO phase =====
# The safe default for "I can't tell what this is" must be visible, not lost.
for ph, rows in seen.items():
    uuids = {r["uuid"] for r in rows}
    assert {"bad-1", "bad-2", "bad-3"} <= uuids, (ph, uuids)
    assert "ok-epbs" in uuids, ph
print("never hidden:      all three unplaceable diagrams, and EPBS, visible from all four")

# ===== 6. The real model is unaffected by the hardening =====
from pathlib import Path
model = svc.open_model(Path(r"c:\apps\Git_VSCode\Capella_Tools\Trail_Power\Trail Power.aird"))
pa_real = svc.search_by_name(model, "PA", "Diagram", "")
assert len(pa_real) == 34, len(pa_real)
assert {r["layer"] for r in pa_real} <= {"PA", "EPBS"}, {r["layer"] for r in pa_real}
print(f"real model:        PA still {len(pa_real)} rows, every one labelled PA or EPBS")

print("\nALL NOTE-0118 ROBUSTNESS CHECKS PASSED")
