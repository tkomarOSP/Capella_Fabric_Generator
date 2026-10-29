"""OBS-0009: capella-fabric can tell an actor from a component.

capellambse has no separate actor class -- an actor is a component (an entity at
OA) with is_actor set -- so `type: PhysicalComponent` is correct for both, and
nothing the tool showed could tell them apart. A real review misread a
deliberate Environment actor/behavior pair as duplicate junk because of it.

Run against a real Capella model (Trail Power, from Capella_Tools' examples),
not a stub: whether is_actor exists and what it holds is a fact about the
installed metamodel, and a hand-built stand-in would only confirm my own
assumptions about it (lesson-0003).
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

from pathlib import Path

import yaml
import capella_service as svc
from capella_tools import capellambse_yaml_manager as ymgr

# Prove we're exercising the edited copy, not some other install on the path.
assert os.path.normcase(r"c:\apps\Git_VSCode\Capella_Tools") in os.path.normcase(ymgr.__file__), ymgr.__file__

MODEL = Path(r"c:\apps\Git_VSCode\Capella_Tools\Trail_Power\Trail Power.aird")
model = svc.open_model(MODEL)


# ===== 1. The Actor filter exists on every layer, and returns only actors =====
types = svc.get_phase_types()
for phase in ("OA", "SA", "LA", "PA"):
    assert "Actor" in types[phase], f"{phase} has no Actor object_type"

truth = {
    "OA": [e for e in model.oa.all_entities if e.is_actor],
    "SA": [c for c in model.sa.all_components if c.is_actor],
    "LA": [c for c in model.la.all_components if c.is_actor],
    "PA": [c for c in model.pa.all_components if c.is_actor],
}
for phase, expected in truth.items():
    rows = svc.search_by_name(model, phase, "Actor", "")
    assert len(rows) == len(expected), f"{phase}: {len(rows)} actor rows, model has {len(expected)} actors"
    assert rows and all(r["is_actor"] is True for r in rows), f"{phase}: non-actor in Actor results"
print("actor filter:      on all four layers, returns exactly the model's actors "
      f"({', '.join(f'{p} {len(v)}' for p, v in truth.items())})")


# ===== 2. "Component" is unchanged -- still actors AND non-actors =====
for phase, layer in (("SA", "sa"), ("LA", "la"), ("PA", "pa")):
    rows = svc.search_by_name(model, phase, "Component", "")
    all_comps = list(getattr(model, layer).all_components)
    assert len(rows) == len(all_comps), f"{phase}: Component count changed"
    flags = {r["is_actor"] for r in rows}
    assert flags == {True, False}, f"{phase}: expected a mix of actors and non-actors, got {flags}"
print("component browse:  unchanged counts, and each row now says which are actors")


# ===== 3. The flag only appears where it means something =====
func = next(iter(model.pa.all_functions))
info = svc._object_info(func)
assert "is_actor" not in info, "functions have no is_actor; they shouldn't gain a meaningless false"
print("browse rows:       is_actor absent on functions, present on components")


# ===== 4. generate_fabric renders is_actor, for every component shape =====
def render(obj):
    handler = ymgr.CapellaYAMLHandler()
    handler.primary_objects.append(obj)
    handler.generate_yaml(obj)
    return handler.get_yaml_content()


def fabric_entry(obj):
    """The parsed fabric object for this uuid -- or fail loudly if it vanished.

    capella_service.generate_fabric wraps generate_yaml in `except Exception:
    pass`, so an error in a data builder would silently DROP the object rather
    than fail. Finding it by uuid is what catches that.
    """
    parsed = yaml.safe_load(render(obj))
    # A fabric is `model: {schema: ..., objects: [...]}`.
    for item in (parsed or {}).get("model", {}).get("objects") or []:
        if str(item.get("primary_uuid")) == str(obj.uuid):
            return item
    raise AssertionError(f"{obj.name} ({obj.__class__.__name__}) missing from its own fabric")


cases = []
la_actor = truth["LA"][0]
la_plain = next(c for c in model.la.all_components if not c.is_actor)
cases += [("LA actor", la_actor, True), ("LA component", la_plain, False)]

pa_by_nature = {}
for c in model.pa.all_components:
    pa_by_nature.setdefault((c.nature, bool(c.is_actor)), c)
for (nature, actor), comp in sorted(pa_by_nature.items(), key=lambda kv: (str(kv[0][0]), kv[0][1])):
    if nature in ("NODE", "BEHAVIOR"):
        cases.append((f"PA {nature} {'actor' if actor else 'component'}", comp, actor))

sa_actor = truth["SA"][0]
cases.append(("SA actor", sa_actor, True))

covered = set()
for label, obj, expected in cases:
    entry = fabric_entry(obj)
    assert "is_actor" in entry, f"{label}: fabric has no is_actor field"
    assert entry["is_actor"] is expected, f"{label}: is_actor={entry['is_actor']!r}, expected {expected}"
    covered.add(label)
    print(f"fabric:            {label:26} is_actor: {str(expected).lower()}")

assert any(l.startswith("PA NODE") for l in covered), "no PA NODE component in the model to test"
assert any(l.startswith("PA BEHAVIOR") for l in covered), "no PA BEHAVIOR component in the model to test"

# The OA Entity already carried is_actor before this change -- it must still.
oa_actor = truth["OA"][0]
assert fabric_entry(oa_actor).get("is_actor") is True
print("fabric:            OA entity actor            is_actor: true (pre-existing, unchanged)")

print("\nALL OBS-0009 ACTOR CHECKS PASSED")
