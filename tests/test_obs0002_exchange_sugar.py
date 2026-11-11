"""OBS-0002: wiring an OA interaction by hand in Capella, then renaming it by
patch, was the slowest step in a modelling session -- and apply_model_patch
refused to create one at all (note-0017). It now creates interactions between
operational activities, computing the owning activity itself.

Part 1 re-confirms the upstream fact the whole carve-out rests on: an
OperationalActivity has no pin or port containment, while a SystemFunction
does. If a future capellambse gives OA activities ports, the carve-out is no
longer justified and `_reject_unsupported_exchange_creation` should go back to
refusing `exchanges` outright -- DELETE the carve-out rather than updating
these assertions.

Part 2 tests the expansion and the guard against a stub tree rather than a real
model, because the interesting case -- a non-trivial lowest common ancestor --
does not exist in any model we have. Trail Power and the Cartenza OA model are
both flat, every activity directly under the root, so a real-model test can
only ever exercise the degenerate "owner is root" path. The nesting is unit
tested here; the real write is in test_obs0002_exchange_apply.py.
"""
import os
import subprocess
import sys

# Run against the capellambse capella-fabric actually deploys with, not
# whatever the global interpreter carries (0.6.20 at the time of writing, where
# the pin classes have different names). A canary run against the wrong
# environment reports on that environment, not the product.
_CAPELLA_VENV_PY = r"c:\apps\Git_VSCode\Capella_Fabric_Generator\.venv\Scripts\python.exe"
if os.path.exists(_CAPELLA_VENV_PY) and os.path.normcase(sys.executable) != os.path.normcase(_CAPELLA_VENV_PY):
    sys.exit(subprocess.run([_CAPELLA_VENV_PY, __file__]).returncode)

sys.path.insert(0, r"c:\apps\Git_VSCode\Capella_Fabric_Generator")
import capella_service as svc

# --- 1. the upstream facts the carve-out exists because of ------------------
from capellambse.metamodel import fa, oa, sa
from capellambse.model._descriptors import Containment


def containment(cls, attr):
    d = getattr(cls, attr, None)
    if not isinstance(d, Containment):
        return None
    target = d.class_[1] if isinstance(d.class_, tuple) else d.class_
    return d.role_tag, getattr(target, "__name__", target)


assert containment(oa.OperationalActivity, "exchanges") == ("ownedFunctionalExchanges", "FunctionalExchange")
assert containment(oa.OperationalActivity, "inputs") is None, (
    "OperationalActivity gained an inputs containment -- OA activities may now use "
    "ports, which removes the justification for the carve-out. DELETE the carve-out in "
    "capella_service._reject_unsupported_exchange_creation rather than updating this."
)
assert containment(oa.OperationalActivity, "outputs") is None, (
    "OperationalActivity gained an outputs containment -- see the note above."
)
assert containment(sa.SystemFunction, "inputs") is not None, (
    "SystemFunction lost its inputs containment -- if SA functions no longer use ports "
    "the carve-out should be WIDENED, not left as is."
)
print("upstream: OperationalActivity has exchanges, no inputs/outputs (port-free)")
print("upstream: SystemFunction still carries inputs/outputs (ports)")

# The entity-level exchange this must keep refusing.
assert getattr(oa.CommunicationMean, "_xmltag", None) == "ownedComponentExchanges"
assert getattr(oa.Entity, "ports", None) is not None, (
    "oa.Entity lost its ports -- re-check whether entity exchanges still need them"
)
# Note Entity.ports is a Filter (a computed view) on capellambse 0.8.1, not a
# Containment like SystemFunction.inputs. That difference does not matter here:
# what makes entity-level exchanges unsafe for this tool is that they are a
# ComponentExchange role between ports, and that is what is asserted above.
print("upstream: Entity exposes ports, CommunicationMean is an ownedComponentExchanges role")


# --- 2. a stub activity tree ------------------------------------------------
class _Stub:
    def __init__(self, uuid, name, parent=None):
        self.uuid = uuid
        self.name = name
        self.parent = parent


def _typed(cls_name, uuid, name, parent=None):
    """_resolve_exchange_endpoint reads type(obj).__name__, so the class name is
    the whole point of building these dynamically."""
    return type(cls_name, (_Stub,), {})(uuid, name, parent)


ACT = "OperationalActivity"
root = _typed(ACT, "u-root", "Root Operational Activity")
branch_a = _typed(ACT, "u-bA", "Operate Vehicle", root)
branch_b = _typed(ACT, "u-bB", "Maintain Vehicle", root)
leaf_a1 = _typed(ACT, "u-a1", "Provide State", branch_a)
leaf_a2 = _typed(ACT, "u-a2", "Comprehend State", branch_a)
leaf_b1 = _typed(ACT, "u-b1", "Log Service Event", branch_b)

pkg = _typed("OperationalActivityPkg", "u-pkg", "Operational Activities")
pkg_act1 = _typed(ACT, "u-p1", "Loose One", pkg)
pkg_act2 = _typed(ACT, "u-p2", "Loose Two", pkg)

entity = _typed("Entity", "u-ent", "Driver", root)
sysfn = _typed("SystemFunction", "u-sf", "Compute Range", None)

_ALL = {o.uuid: o for o in (root, branch_a, branch_b, leaf_a1, leaf_a2, leaf_b1,
                            pkg, pkg_act1, pkg_act2, entity, sysfn)}


class _Model:
    def by_uuid(self, uuid):
        if uuid not in _ALL:
            raise KeyError(uuid)
        return _ALL[uuid]


model = _Model()


def sugar(**spec):
    return [{"exchange": spec}]


def expand(patch):
    return svc._expand_oa_exchanges(model, patch)


def refuses(patch, *required, forbidden=("Traceback", "KeyError", "Invalid type hint")):
    """Assert the patch is refused and the message is actionable."""
    try:
        expand(patch)
    except ValueError as exc:
        msg = str(exc)
        for needle in required:
            assert needle in msg, f"missing {needle!r} in: {msg}"
        for leak in forbidden:
            assert leak not in msg, f"raw {leak!r} leaked into: {msg}"
        return msg
    raise AssertionError(f"expected a refusal for {patch!r}")


# --- the owner is computed --------------------------------------------------
patch = sugar(name="State Report", source=svc._UUIDRef("u-a1"), target=svc._UUIDRef("u-a2"))
checks = expand(patch)
assert patch[0]["parent"].value == "u-bA", patch[0]
child = patch[0]["extend"]["exchanges"][0]
assert child["_type"] == "FunctionalExchange"
assert child["name"] == "State Report"
assert child["source"].value == "u-a1" and child["target"].value == "u-a2"
assert "exchange" not in patch[0], "the shorthand key should be gone after expansion"
assert checks == [{
    "promise_id": child["promise_id"], "name": "State Report",
    "source": "u-a1", "target": "u-a2", "owner": "u-bA",
}]
print("owner: two leaves of one branch are owned by that branch")

patch = sugar(name="Cross", source=svc._UUIDRef("u-a1"), target=svc._UUIDRef("u-b1"))
expand(patch)
assert patch[0]["parent"].value == "u-root"
print("owner: leaves of different branches are owned by the root activity")

patch = sugar(name="Down", source=svc._UUIDRef("u-bA"), target=svc._UUIDRef("u-a1"))
expand(patch)
assert patch[0]["parent"].value == "u-bA", "a parent/child pair should be owned by the parent"
print("owner: an ancestor/descendant pair is owned by the ancestor")

patch = sugar(name="Lifted", source=svc._UUIDRef("u-a1"), target=svc._UUIDRef("u-a2"),
              owner=svc._UUIDRef("u-root"))
expand(patch)
assert patch[0]["parent"].value == "u-root"
print("owner: an explicit owner above the common ancestor is honoured")

# --- promise ids ------------------------------------------------------------
patch = sugar(name="Mine", source=svc._UUIDRef("u-a1"), target=svc._UUIDRef("u-a2"),
              promise_id="my_xch")
checks = expand(patch)
assert checks[0]["promise_id"] == "my_xch"
assert patch[0]["extend"]["exchanges"][0]["promise_id"] == "my_xch"
patch = sugar(name="Minted", source=svc._UUIDRef("u-a1"), target=svc._UUIDRef("u-a2"))
assert expand(patch)[0]["promise_id"].startswith("_xch_")
print("promises: an explicit promise_id is kept, an absent one is minted")

# --- several in one patch, author order preserved ---------------------------
patch = [
    {"exchange": {"name": "First", "source": svc._UUIDRef("u-a1"), "target": svc._UUIDRef("u-a2")}},
    {"parent": svc._UUIDRef("u-root"), "set": {"name": "Root Operational Activity"}},
    {"exchange": {"name": "Second", "source": svc._UUIDRef("u-a1"), "target": svc._UUIDRef("u-b1")}},
]
checks = expand(patch)
assert [c["name"] for c in checks] == ["First", "Second"]
assert patch[0]["parent"].value == "u-bA" and patch[2]["parent"].value == "u-root"
assert patch[1]["set"]["name"] == "Root Operational Activity", "unrelated entries untouched"
print("order: several interactions expand in place, other entries are left alone")

# --- description is an HTML field -------------------------------------------
assert svc._description_for_html("Position and heading, each 100 ms cycle") == \
    "Position and heading, each 100 ms cycle"
assert svc._description_for_html("Sends A & B status") == "Sends A &amp; B status"
assert svc._description_for_html("moved <see note>") == "moved &lt;see note&gt;", (
    "an angle-bracketed word must be escaped -- capellambse's repair_html turns "
    "'<see note>' into '<see note=\"\"/>', which renders as nothing and loses the text"
)
assert svc._description_for_html("<p>Already HTML</p>") == "<p>Already HTML</p>"
patch = sugar(name="Described", source=svc._UUIDRef("u-a1"), target=svc._UUIDRef("u-a2"),
              description="Rejected if the remote <has moved>")
expand(patch)
assert patch[0]["extend"]["exchanges"][0]["description"] == "Rejected if the remote &lt;has moved&gt;"
print("description: plain text is escaped, real markup is passed through")

# --- refusals ---------------------------------------------------------------
refuses(sugar(source=svc._UUIDRef("u-a1"), target=svc._UUIDRef("u-a2")),
        "has no name:", "name: <interaction name>")
print("refused: an unnamed interaction")

refuses(sugar(name="NoTarget", source=svc._UUIDRef("u-a1")),
        "missing target:", "!uuid")
print("refused: a missing endpoint")

refuses(sugar(name="Bare", source="u-a1", target=svc._UUIDRef("u-a2")),
        "plain value", "!uuid u-a1", "note-0017")
print("refused: an untagged endpoint, naming the tag to add")

refuses(sugar(name="Promised", source=svc._PromiseRef("new_act"), target=svc._UUIDRef("u-a2")),
        "!promise new_act", "not supported yet", "then wire them in the next")
print("refused: a promise endpoint, pointing at the two-patch workaround")

refuses(sugar(name="Ghost", source=svc._UUIDRef("u-nope"), target=svc._UUIDRef("u-a2")),
        "not in this model", "Nothing was changed", "search_model_objects")
print("refused: an unresolvable uuid, distinguished from a wrong-class one")

refuses(sugar(name="ToEntity", source=svc._UUIDRef("u-a1"), target=svc._UUIDRef("u-ent")),
        "is a Entity", "'Driver'", "CommunicationMean", "note-0017")
print("refused: an Entity endpoint, which is the port-using OA case")

refuses(sugar(name="ToSysFn", source=svc._UUIDRef("u-a1"), target=svc._UUIDRef("u-sf")),
        "is a SystemFunction", "no ports")
print("refused: a SA function endpoint")

refuses(sugar(name="Loop", source=svc._UUIDRef("u-a1"), target=svc._UUIDRef("u-a1")),
        "same activity", "copied", "desktop editor")
print("refused: a self-loop")

refuses(sugar(name="Orphans", source=svc._UUIDRef("u-p1"), target=svc._UUIDRef("u-p2")),
        "no common OperationalActivity", "'Operational Activities'",
        "OperationalActivityPkg", "owner: !uuid")
print("refused: a package as the nearest shared container, naming the escape hatch")

refuses(sugar(name="Stray", source=svc._UUIDRef("u-a1"), target=svc._UUIDRef("u-a2"),
              owner=svc._UUIDRef("u-bB")),
        "does not contain both endpoints", "Omit owner:")
print("refused: an explicit owner that contains neither endpoint")

refuses(sugar(name="BadOwner", source=svc._UUIDRef("u-a1"), target=svc._UUIDRef("u-a2"),
              owner=svc._UUIDRef("u-ent")),
        "is a Entity")
print("refused: a non-activity owner")

refuses([{"exchange": {"name": "Both"}, "parent": svc._UUIDRef("u-root")}],
        "both the exchange: shorthand", "computes its own owner")
print("refused: the shorthand mixed with parent:/extend:")


# --- 3. the guard, on canonical patches -------------------------------------
def guard_refuses(patch, *required):
    try:
        svc._reject_unsupported_exchange_creation(model, patch)
    except ValueError as exc:
        msg = str(exc)
        for needle in required:
            assert needle in msg, f"missing {needle!r} in: {msg}"
        return
    raise AssertionError(f"expected the guard to refuse {patch!r}")


def canonical(parent_uuid, **child):
    return [{"parent": svc._UUIDRef(parent_uuid), "extend": {"exchanges": [child]}}]


# An expanded shorthand entry must survive its own guard.
patch = sugar(name="Survives", source=svc._UUIDRef("u-a1"), target=svc._UUIDRef("u-a2"))
expand(patch)
svc._reject_unsupported_exchange_creation(model, patch)
print("guard: an expanded interaction passes the guard")

svc._reject_unsupported_exchange_creation(model, canonical(
    "u-bA", name="Hand written", source=svc._UUIDRef("u-a1"), target=svc._UUIDRef("u-a2")))
print("guard: a hand-written canonical interaction under an activity is allowed")

guard_refuses([{"parent": svc._UUIDRef("u-root"), "extend": {"component_exchanges": [{"name": "x"}]}}],
              "component_exchanges", "backing ports", "note-0017")
guard_refuses([{"parent": svc._UUIDRef("u-root"), "extend": {"physical_links": [{"name": "x"}]}}],
              "physical_links", "backing ports")
print("guard: component_exchanges and physical_links are still refused outright")

guard_refuses(canonical("u-sf", name="x", source=svc._UUIDRef("u-a1"), target=svc._UUIDRef("u-a2")),
              "under a SystemFunction", "no ports", "note-0017")
guard_refuses(canonical("u-ent", name="x", source=svc._UUIDRef("u-a1"), target=svc._UUIDRef("u-a2")),
              "under a Entity", "CommunicationMean")
print("guard: exchanges under a non-activity parent are refused, naming the class")

guard_refuses([{"parent": svc._PromiseRef("new"), "extend": {"exchanges": [{"name": "x"}]}}],
              "!promise", "has to exist already")
print("guard: a promise parent is refused")

guard_refuses(canonical("u-bA", name="Dangling", source=svc._UUIDRef("u-a1")),
              "no valid target:", "dangling reference", "shorthand")
guard_refuses(canonical("u-bA", name="Untagged", source="u-a1", target=svc._UUIDRef("u-a2")),
              "no valid source:")
print("guard: a canonical entry with a missing or untagged endpoint is refused too")

guard_refuses(canonical("u-gone", name="x", source=svc._UUIDRef("u-a1"), target=svc._UUIDRef("u-a2")),
              "not in this model", "Nothing was changed")
print("guard: an unresolvable parent is refused")

print("\nALL OBS-0002 EXCHANGE-SUGAR CHECKS PASSED")
