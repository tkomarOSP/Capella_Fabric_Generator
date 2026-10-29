"""OBS-0001: creating child PhysicalFunctions under a PhysicalFunction is
impossible in capellambse 0.8.1. The server must say why, in terms the caller
can act on, instead of passing through 'Invalid type hint: PhysicalFunction'.

Part 1 re-confirms the upstream facts against the installed metamodel, so this
test starts failing if a future capellambse fixes the binding -- at which point
the guard should be removed rather than kept.
"""
import os
import subprocess
import sys

# Run against the capellambse capella-fabric actually deploys with, not whatever
# the global interpreter happens to carry. For a week this test was invoked
# with the global Python (capellambse 0.6.20), failed, and was reported as
# "capellambse changed underneath the server" -- when the server's own venv was
# on 0.8.1 the whole time and the guard was correct. A canary run against the
# wrong environment reports on that environment, not the product.
_CAPELLA_VENV_PY = r"c:\apps\Git_VSCode\Capella_Fabric_Generator\.venv\Scripts\python.exe"
if os.path.exists(_CAPELLA_VENV_PY) and os.path.normcase(sys.executable) != os.path.normcase(_CAPELLA_VENV_PY):
    sys.exit(subprocess.run([_CAPELLA_VENV_PY, __file__]).returncode)

sys.path.insert(0, r"c:\apps\Git_VSCode\Capella_Fabric_Generator")
import capella_service as svc

# --- 1. the upstream facts this guard exists because of -----------------
from capellambse.metamodel import la, pa
from capellambse.model._descriptors import Containment


def binding(cls, attr):
    d = getattr(cls, attr, None)
    assert isinstance(d, Containment), f"{cls.__name__}.{attr} is not a Containment"
    target = d.class_[1] if isinstance(d.class_, tuple) else d.class_
    return d.role_tag, target


assert binding(la.LogicalFunction, "functions") == ("ownedFunctions", "LogicalFunction")
assert binding(pa.PhysicalFunction, "functions") == ("ownedPhysicalComponents", "PhysicalComponent"), (
    "capellambse may have FIXED PhysicalFunction.functions -- if so, remove the guard "
    "in capella_service._reject_pa_child_functions rather than updating this assertion"
)
assert not any(
    isinstance(getattr(pa.PhysicalFunction, n, None), Containment)
    and getattr(pa.PhysicalFunction, n).role_tag == "ownedFunctions"
    for n in dir(pa.PhysicalFunction)
), "PhysicalFunction gained an ownedFunctions attribute -- the guard is now wrong"
print("upstream: PhysicalFunction.functions -> PhysicalComponent/ownedPhysicalComponents (still broken)")
print("upstream: LogicalFunction.functions  -> LogicalFunction/ownedFunctions (correct)")


# --- 2. the guard itself -------------------------------------------------
class _Stub:
    """Stands in for a model object; _layer_from_type reads the class name."""
    def __init__(self, name):
        self.name = name
        self.parent = None


def _typed(cls_name, display):
    return type(cls_name, (_Stub,), {})(display)


class _Model:
    def __init__(self, obj):
        self._obj = obj

    def by_uuid(self, _uuid):
        return self._obj


def patch_for(parent_uuid):
    return [{
        "parent": svc._UUIDRef(parent_uuid),
        "extend": {"functions": [{"name": "Cool EGR gas"}]},
    }]


# PA, parent is a Function -> refused with a real explanation
model = _Model(_typed("PhysicalFunction", "Root Physical Function"))
try:
    svc._enforce_function_types(model, patch_for("pf-uuid"))
    raise AssertionError("expected a refusal for PA child functions")
except ValueError as exc:
    msg = str(exc)
    for required in ("upstream limitation", "Root Logical Function",
                     "Capella desktop", "SIBLINGS", "Root Physical Function"):
        assert required in msg, f"missing {required!r} in: {msg}"
    assert "Invalid type hint" not in msg
print("guard: PA function parent refused, message names the cause and both options")

# LA, parent is a Function -> unaffected, and still injects the right _type
patch = patch_for("lf-uuid")
svc._enforce_function_types(_Model(_typed("LogicalFunction", "Root Logical Function")), patch)
assert patch[0]["extend"]["functions"][0]["_type"] == "LogicalFunction"
print("guard: LA function parent untouched, _type still injected")

# PA, parent is a *Component* -> not this bug; must still work
patch = patch_for("pc-uuid")
svc._enforce_function_types(_Model(_typed("PhysicalComponent", "Engine")), patch)
assert patch[0]["extend"]["functions"][0]["_type"] == "PhysicalFunction"
print("guard: PA component parent untouched (allocation case, not the bug)")

# PA, parent is a Function but no function children -> nothing to refuse
svc._enforce_function_types(
    _Model(_typed("PhysicalFunction", "Root Physical Function")),
    [{"parent": svc._UUIDRef("pf-uuid"), "set": {"name": "renamed"}}],
)
print("guard: PA function parent with no function children is left alone")

print("\nALL PA-FUNCTION-GUARD CHECKS PASSED")
