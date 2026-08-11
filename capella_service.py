# Copyright 2024-2026 Open Sun Power, LLC
# SPDX-License-Identifier: Apache-2.0
# Capella Fabric Generator — service layer: model loading, UUID resolution, YAML fabric generation.

import io
import os
import sys
import re
import uuid
import json
import zipfile
import shutil
import tempfile
from pathlib import Path

# ---------------------------------------------------------------------------
# Ensure Capella_Tools is importable
# ---------------------------------------------------------------------------
_CAPELLA_TOOLS = Path(os.environ.get('CAPELLA_TOOLS_PATH', r'C:\apps\.metadata\Capella_Tools'))
if str(_CAPELLA_TOOLS) not in sys.path:
    sys.path.insert(0, str(_CAPELLA_TOOLS))

import yaml
import capellambse
from capellambse import decl
from capellambse import model as capellambse_model
from capella_tools.capellambse_yaml_manager import CapellaYAMLHandler

# ---------------------------------------------------------------------------
# Temp-directory layout:  <TEMP_BASE>/<session_id>/
#   upload.zip
#   unpacked/           ← extracted archive contents
#   <stem>_fabric.yaml  ← generated output
#   session.json        ← persisted session record
# ---------------------------------------------------------------------------
_TEMP_BASE = Path(tempfile.gettempdir()) / 'capella_fabric'
_TEMP_BASE.mkdir(exist_ok=True)


# ---------------------------------------------------------------------------
# Session helpers
# ---------------------------------------------------------------------------

def create_session() -> str:
    session_id = uuid.uuid4().hex
    _session_dir(session_id).mkdir(parents=True, exist_ok=True)
    return session_id


def _session_dir(session_id: str) -> Path:
    return _TEMP_BASE / session_id


def save_session(session_id: str, data: dict) -> None:
    with open(_session_dir(session_id) / 'session.json', 'w') as f:
        json.dump(data, f, indent=2)


def load_session(session_id: str) -> dict:
    with open(_session_dir(session_id) / 'session.json') as f:
        return json.load(f)


def cleanup_session(session_id: str) -> None:
    d = _session_dir(session_id)
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------------------
# Archive handling
# ---------------------------------------------------------------------------

def save_upload(file_storage, session_id: str) -> Path:
    """Save a Werkzeug FileStorage object as upload.zip."""
    zip_path = _session_dir(session_id) / 'upload.zip'
    file_storage.save(str(zip_path))
    return zip_path


def unpack_archive(session_id: str) -> Path:
    """Extract the uploaded zip into <session>/unpacked/."""
    zip_path = _session_dir(session_id) / 'upload.zip'
    unpack_dir = _session_dir(session_id) / 'unpacked'
    unpack_dir.mkdir(exist_ok=True)
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(unpack_dir)
    return unpack_dir


def find_aird_file(session_id: str) -> Path | None:
    """Return the first .aird file found under the unpacked directory."""
    unpack_dir = _session_dir(session_id) / 'unpacked'
    hits = list(unpack_dir.rglob('*.aird'))
    return hits[0] if hits else None


# ---------------------------------------------------------------------------
# Model loading
# ---------------------------------------------------------------------------

def open_model(aird_path: Path, resources: dict | None = None) -> capellambse.MelodyModel:
    """Open a Capella model, optionally with dependency library resources."""
    kwargs = {'resources': resources} if resources else {}
    return capellambse.MelodyModel(str(aird_path), **kwargs)


# ---------------------------------------------------------------------------
# Phase → object-type collections
# ---------------------------------------------------------------------------

def _all_requirements(m: capellambse.MelodyModel):
    """Model-wide Requirement search (note-0037).

    BlockArchitecture.all_requirements (m.oa.all_requirements etc.) passes
    below=<that layer> to model.search(), which restricts results to
    elements nested inside that layer's own XML subtree. Teamcenter/SMW
    import tooling drops each imported requirement's CapellaModule as an
    ownedExtensions child of the SystemEngineering root itself -- above
    all four architecture layers, not nested under any single one of them
    (confirmed against a real exported model). So no per-layer
    all_requirements property, for any layer, will ever see it. Searching
    from the model root (no below=) removes that ancestor constraint.
    """
    return m.search("Requirement", subclasses=True)


def _all_diagrams(m: capellambse.MelodyModel):
    """Model-wide Diagram search, unscoped by viewpoint (note-0044 item 4).

    m.oa.diagrams/m.sa.diagrams/m.la.diagrams/m.pa.diagrams each construct
    their own capellambse.model.DiagramAccessor with a *fixed viewpoint*
    ("Operational Analysis"/"System Analysis"/"Logical Architecture"/
    "Physical Architecture" respectively) baked into the accessor
    definition -- despite superficially looking like layer-scoped
    containment traversal, the accessor's __get__ actually just calls
    aird.enumerate_descriptors(loader, viewpoint=<that fixed string>)
    every time, filtering by viewpoint alone. CDB (Class Diagram Blank /
    data) diagrams have viewpoint "Common" -- confirmed directly against a
    real model containing two -- which matches none of the four layer
    viewpoints, so they're invisible from every single phase, not just one.
    Constructing a fresh DiagramAccessor with viewpoint=None removes that
    filter entirely and returns every diagram in the model regardless of
    viewpoint (same fix shape as _all_requirements/_model_wide above:
    don't let a structural scoping assumption hide real content).
    """
    return capellambse_model.DiagramAccessor(viewpoint=None).__get__(m)


def _model_wide(class_name: str):
    """Build a model-wide (unscoped) search lambda for a given class name.

    Data-modeling elements (ExchangeItem, ExchangeItemElement, Class,
    Association, DataPkg) live under whichever DataPkg they were authored
    in -- not scoped to one architecture layer's XML subtree the way
    per-layer accessors like m.oa.all_requirements assume. Registering
    these under every phase via a per-layer-scoped search would repeat the
    exact note-0037 bug (search rooted at the wrong ancestor misses content
    rooted elsewhere) on day one. Search from the model root instead, same
    fix pattern already proven for Requirement (_all_requirements above).
    """
    return lambda m: m.search(class_name, subclasses=True)


PHASE_COLLECTIONS: dict[str, dict[str, object]] = {
    "OA": {
        "Requirement":      _all_requirements,
        "Entity":           lambda m: m.oa.all_entities,
        "Activity":         lambda m: m.oa.all_activities,
        "Capability":       lambda m: m.oa.all_capabilities,
        "Entity Exchange":  lambda m: m.oa.all_entity_exchanges,
        "Process":          lambda m: m.oa.all_processes,
        "Diagram":          _all_diagrams,
        "Data Package":            _model_wide("DataPkg"),
        "Class":                    _model_wide("Class"),
        "Association":              _model_wide("Association"),
        "Exchange Item":            _model_wide("ExchangeItem"),
        "Exchange Item Element":    _model_wide("ExchangeItemElement"),
        "Data Type":                _model_wide("DataType"),
    },
    "SA": {
        "Requirement":       _all_requirements,
        "Component":         lambda m: m.sa.all_components,
        "Capability":        lambda m: m.sa.all_capabilities,
        "Function Exchange": lambda m: m.sa.all_function_exchanges,
        "Function":          lambda m: m.sa.all_functions,
        "Mission":           lambda m: m.sa.all_missions,
        "Functional Chain":  lambda m: m.sa.all_functional_chains,
        "Diagram":           _all_diagrams,
        "Data Package":            _model_wide("DataPkg"),
        "Class":                    _model_wide("Class"),
        "Association":              _model_wide("Association"),
        "Exchange Item":            _model_wide("ExchangeItem"),
        "Exchange Item Element":    _model_wide("ExchangeItemElement"),
        "Data Type":                _model_wide("DataType"),
    },
    "LA": {
        "Requirement":        _all_requirements,
        "Capability":         lambda m: m.la.all_capabilities,
        "Component":          lambda m: m.la.all_components,
        "Function":           lambda m: m.la.all_functions,
        "Function Exchange":  lambda m: m.la.all_function_exchanges,
        "Functional Chain":   lambda m: m.la.all_functional_chains,
        "Interface":          lambda m: m.la.all_interfaces,
        "Component Exchange": lambda m: list(m.la.component_exchanges) + list(m.la.actor_exchanges),
        "Diagram":            _all_diagrams,
        "Data Package":            _model_wide("DataPkg"),
        "Class":                    _model_wide("Class"),
        "Association":              _model_wide("Association"),
        "Exchange Item":            _model_wide("ExchangeItem"),
        "Exchange Item Element":    _model_wide("ExchangeItemElement"),
        "Data Type":                _model_wide("DataType"),
    },
    "PA": {
        "Requirement":        _all_requirements,
        "Component":          lambda m: m.pa.all_components,
        "Function":           lambda m: m.pa.all_functions,
        "Functional Chain":   lambda m: m.pa.all_functional_chains,
        "Function Exchange":  lambda m: m.pa.all_function_exchanges,
        "Capability":         lambda m: m.pa.all_capabilities,
        "Component Exchange": lambda m: m.pa.all_component_exchanges,
        "Physical Exchange":  lambda m: m.pa.all_physical_exchanges,
        "Physical Link":      lambda m: m.pa.all_physical_links,
        "Physical Path":      lambda m: m.pa.all_physical_paths,
        "Diagram":            _all_diagrams,
        "Data Package":            _model_wide("DataPkg"),
        "Class":                    _model_wide("Class"),
        "Association":              _model_wide("Association"),
        "Exchange Item":            _model_wide("ExchangeItem"),
        "Exchange Item Element":    _model_wide("ExchangeItemElement"),
        "Data Type":                _model_wide("DataType"),
    },
}


def get_phase_types() -> dict[str, list[str]]:
    """Return {phase: [type_label, ...]} for populating the browse UI."""
    return {phase: list(types.keys()) for phase, types in PHASE_COLLECTIONS.items()}


def search_by_name(model, phase: str, obj_type: str, name_query: str) -> list[dict]:
    """Return _object_info dicts from a phase+type collection matching name_query (case-insensitive substring)."""
    getter = PHASE_COLLECTIONS.get(phase, {}).get(obj_type)
    if getter is None:
        return []
    q = name_query.strip().lower()
    return [
        _object_info(obj)
        for obj in getter(model)
        if not q or q in (getattr(obj, 'name', '') or '').lower()
    ]


# ---------------------------------------------------------------------------
# UUID parsing
# ---------------------------------------------------------------------------

def parse_uuid_text(text: str) -> list[str]:
    """Split on commas, newlines, or semicolons; strip; deduplicate."""
    parts = re.split(r'[,\n;]+', text)
    seen: set[str] = set()
    result: list[str] = []
    for raw in parts:
        u = raw.strip()
        if u and u not in seen:
            seen.add(u)
            result.append(u)
    return result


# ---------------------------------------------------------------------------
# Object inspection
# ---------------------------------------------------------------------------

def _layer_from_type(type_name: str) -> str:
    """Derive the Capella layer from the class name."""
    if any(x in type_name for x in ('Operational', 'Entity', 'Activity', 'Process')):
        return 'OA'
    if 'System' in type_name:
        return 'SA'
    if 'Logical' in type_name:
        return 'LA'
    if 'Physical' in type_name:
        return 'PA'
    return '—'


def _parent_name(obj) -> str:
    try:
        parent = obj.parent if hasattr(obj, 'parent') else obj.owner
        return getattr(parent, 'name', str(parent)) or '—'
    except Exception:
        return '—'


def _object_info(obj) -> dict:
    type_name = obj.__class__.__name__
    return {
        'uuid': str(obj.uuid),
        'name': getattr(obj, 'name', '—') or '—',
        'type': type_name,
        'layer': _layer_from_type(type_name),
        'parent': _parent_name(obj),
    }


def resolve_uuids(model, uuid_list: list[str]) -> tuple[list[dict], list[str]]:
    """
    Attempt to resolve each UUID against the model.

    Returns:
        resolved  — list of info-dicts for found objects
        not_found — list of UUIDs that could not be resolved
    """
    resolved: list[dict] = []
    not_found: list[str] = []
    for u in uuid_list:
        try:
            obj = model.by_uuid(u)
            resolved.append(_object_info(obj))
        except Exception:
            not_found.append(u)
    return resolved, not_found


# ---------------------------------------------------------------------------
# Fabric generation
# ---------------------------------------------------------------------------

def generate_fabric(session: dict) -> tuple[Path, int]:
    """
    Re-open the model and generate a YAML fabric for the resolved UUIDs.

    Returns:
        yaml_path    — Path to the written .txt file
        object_count — approximate count of primary objects in the output
    """
    aird_path = Path(session['aird_path'])
    uuid_list: list[str] = session['resolved_uuids']
    include_realized: bool = session.get('include_realized', False)
    include_realizing: bool = session.get('include_realizing', False)
    session_id: str = session['session_id']

    model = open_model(aird_path, resources=session.get('resources') or None)

    handler = CapellaYAMLHandler()
    handler.set_realized_refs(include_realized)
    handler.set_realizing_refs(include_realizing)

    for u in uuid_list:
        try:
            obj = model.by_uuid(u)
            handler.primary_objects.append(obj)  
            handler.generate_yaml(obj)
        except Exception:
            pass

    handler.generate_yaml_referenced_objects()
    yaml_content = handler.get_yaml_content()

    # Count primary objects by occurrences of the primary_uuid key
    object_count = yaml_content.count('primary_uuid:')

    archive_stem = Path(session.get('archive_name', 'model')).stem
    yaml_name = f'{archive_stem}_fabric.txt'
    yaml_path = _session_dir(session_id) / yaml_name

    with open(yaml_path, 'w', encoding='utf-8') as f:
        f.write('# Capella Fabric YAML\n')
        f.write(yaml_content)
        f.write('\n')

    return yaml_path, object_count


# ---------------------------------------------------------------------------
# Declarative patch (write)
# ---------------------------------------------------------------------------

# capellambse decl uses !uuid and !promise custom YAML tags; round-trip them
# through our pre-processing by subclassing the loader/dumper so we never
# touch the global yaml.SafeLoader / yaml.SafeDumper singletons.

class _UUIDRef:
    def __init__(self, value: str): self.value = value

class _PromiseRef:
    def __init__(self, value: str): self.value = value

class _PatchLoader(yaml.SafeLoader): pass
class _PatchDumper(yaml.SafeDumper): pass

_PatchLoader.add_constructor('!uuid',    lambda l, n: _UUIDRef(l.construct_scalar(n)))
_PatchLoader.add_constructor('!promise', lambda l, n: _PromiseRef(l.construct_scalar(n)))
_PatchDumper.add_representer(_UUIDRef,    lambda d, v: d.represent_scalar('!uuid', v.value))
_PatchDumper.add_representer(_PromiseRef, lambda d, v: d.represent_scalar('!promise', v.value))

# ARCADIA phase → required Capella class for function/activity children
_PHASE_FUNCTION_TYPE: dict[str, str] = {
    'OA': 'OperationalActivity',
    'SA': 'SystemFunction',
    'LA': 'LogicalFunction',
    'PA': 'PhysicalFunction',
}

# extend: attribute names that hold function/activity children
_FUNCTION_ATTRS = frozenset({
    'functions', 'owned_functions',
    'activities', 'owned_activities',
})

# ARCADIA phase → required Capella class for component children
# SA/LA use 'components'; PA uses 'owned_components' (→ ownedPhysicalComponents in XML).
# Without _type the declarative engine creates malformed Part objects instead of
# real PhysicalComponent/SystemComponent/LogicalComponent elements.
_PHASE_COMPONENT_TYPE: dict[str, str] = {
    'SA': 'SystemComponent',
    'LA': 'LogicalComponent',
    'PA': 'PhysicalComponent',
}

_COMPONENT_ATTRS = frozenset({
    'components',        # SA, LA  (→ ownedSystemComponents / ownedLogicalComponents)
    'owned_components',  # PA      (→ ownedPhysicalComponents)
})


def _resolve_phase_for_uuid(model, uuid_str: str) -> str:
    """Walk the parent chain from a UUID'd object to determine its Capella layer."""
    try:
        current = model.by_uuid(uuid_str)
        for _ in range(20):
            phase = _layer_from_type(type(current).__name__)
            if phase != '—':
                return phase
            parent = getattr(current, 'parent', None) or getattr(current, 'owner', None)
            if parent is None or parent is current:
                break
            current = parent
    except Exception:
        pass
    return '—'


def _enforce_function_types(model, patch_data: list) -> None:
    """Inject/correct _type on function/activity children based on each parent's phase."""
    for item in patch_data:
        if not isinstance(item, dict):
            continue
        parent_ref = item.get('parent')
        if not isinstance(parent_ref, _UUIDRef):
            continue
        phase = _resolve_phase_for_uuid(model, parent_ref.value)
        correct_type = _PHASE_FUNCTION_TYPE.get(phase)
        if not correct_type:
            continue
        extend = item.get('extend', {})
        if not isinstance(extend, dict):
            continue
        for attr in _FUNCTION_ATTRS:
            for child in extend.get(attr, []):
                if isinstance(child, dict):
                    child['_type'] = correct_type  # inject if absent, correct if wrong


_UNSUPPORTED_EXTEND_ATTRS = frozenset({
    'exchanges', 'component_exchanges', 'physical_links',
})


def _reject_unsupported_exchange_creation(patch_data: list) -> None:
    """Refuse to create FunctionalExchange/ComponentExchange/PhysicalLink
    elements via extend: (note-0017).

    These all connect ports (FunctionInputPort/FunctionOutputPort,
    PhysicalPort), not the functions/components/UUIDs a patch typically
    references directly. apply_model_patch's generic decl-based engine sets
    source/target as raw IDREF associations with no validation that the
    referenced UUID is actually a port -- it will happily wire an exchange's
    target directly at a Function/Component's own UUID, producing a
    structurally invalid, dangling reference that verify_model won't catch
    (patch reports "ok", nothing flags the corruption). The Capella desktop
    editor creates the backing ports as part of drawing the connection;
    replicating that here isn't worth the complexity for something usually
    faster to draw by hand than to describe, and a wrong auto-created
    exchange is more work to find and remove than to just draw correctly.
    Renaming/retagging *existing* exchanges/links via set: name: is
    unaffected -- that pattern doesn't touch these keys under extend:.
    """
    for item in patch_data:
        if not isinstance(item, dict):
            continue
        extend = item.get('extend', {})
        if not isinstance(extend, dict):
            continue
        hit = _UNSUPPORTED_EXTEND_ATTRS & extend.keys()
        if hit:
            raise ValueError(
                "apply_model_patch cannot create new "
                f"{'/'.join(sorted(hit))} via extend: -- FunctionalExchange/"
                "ComponentExchange/PhysicalLink creation requires backing "
                "ports that this tool doesn't create or validate (note-0017). "
                "Create these directly in the Capella desktop editor; "
                "renaming/retagging *existing* exchanges/links via set: "
                "name: remains reliable."
            )


def _normalize_pa_component_key(model, patch_data: list) -> None:
    """Rewrite extend: components: -> extend: owned_components: for PA-phase
    parents, but only when this model's PhysicalComponent class actually has
    the owned_components/related_components split (note-0021).

    Current capellambse deprecates PhysicalComponent.components as a
    non-model-coupled computed property (related_components), moving the
    real containment to owned_components -- extending 'components' directly
    raises "not model-coupled" from capellambse's generic decl engine. But
    older capellambse installs (still common among Siemens customers on
    Capella 6.1-era tooling) predate that split: there, 'components' IS the
    real, model-coupled containment, and rewriting it would break instead of
    fix. Capella_Tools' own capellambse_yaml_manager.py already defends this
    exact same way (`hasattr(type(obj), "related_components")`) -- mirrored
    here so this tool supports both.
    """
    for item in patch_data:
        if not isinstance(item, dict):
            continue
        parent_ref = item.get('parent')
        if not isinstance(parent_ref, _UUIDRef):
            continue
        if _resolve_phase_for_uuid(model, parent_ref.value) != 'PA':
            continue
        extend = item.get('extend', {})
        if not isinstance(extend, dict) or 'components' not in extend:
            continue
        try:
            parent_obj = model.by_uuid(parent_ref.value)
        except Exception:
            continue
        if not hasattr(type(parent_obj), 'related_components'):
            continue  # older capellambse: components IS the real containment -- leave it
        children = extend.pop('components')
        extend.setdefault('owned_components', []).extend(children)


def _enforce_component_types(model, patch_data: list) -> None:
    """Inject _type on component children where absent to prevent Part object creation."""
    for item in patch_data:
        if not isinstance(item, dict):
            continue
        parent_ref = item.get('parent')
        if not isinstance(parent_ref, _UUIDRef):
            continue
        phase = _resolve_phase_for_uuid(model, parent_ref.value)
        correct_type = _PHASE_COMPONENT_TYPE.get(phase)
        if not correct_type:
            continue
        extend = item.get('extend', {})
        if not isinstance(extend, dict):
            continue
        for attr in _COMPONENT_ATTRS:
            for child in extend.get(attr, []):
                if isinstance(child, dict) and '_type' not in child:
                    child['_type'] = correct_type


def _pv_type_from_value(value) -> str | None:
    """Return the correct capellambse PropertyValue class name for a Python value."""
    if isinstance(value, bool):  return 'BooleanPropertyValue'   # bool before int!
    if isinstance(value, int):   return 'IntegerPropertyValue'
    if isinstance(value, float): return 'FloatPropertyValue'
    if isinstance(value, str):   return 'StringPropertyValue'
    return None


def _enforce_pv_types(patch_data: list) -> None:
    """Inject _type on PropertyValueGroup and property value children where absent.

    Also back-references each newly-extended group onto its owning component's
    applied_property_value_groups (note-0028): owned_property_value_groups
    (containment) and applied_property_value_groups (association) are
    independent Capella attributes, so a group created via `extend:
    property_value_groups:` alone is structurally present in the XML but never
    counted as "applied" -- Capella silently ignores it. Assigns a promise_id
    to the new group (if it doesn't already have one) and appends a matching
    !promise reference into the same parent's applied_property_value_groups.
    """
    for item in patch_data:
        if not isinstance(item, dict):
            continue
        for block_key in ('extend', 'set'):
            block = item.get(block_key, {})
            if not isinstance(block, dict):
                continue
            for pvg in block.get('property_value_groups', []):
                if not isinstance(pvg, dict):
                    continue
                if '_type' not in pvg:
                    pvg['_type'] = 'PropertyValueGroup'
                for pv in pvg.get('property_values', []):
                    if isinstance(pv, dict) and '_type' not in pv:
                        t = _pv_type_from_value(pv.get('value'))
                        if t:
                            pv['_type'] = t
                if block_key == 'extend':
                    promise_id = pvg.get('promise_id')
                    if not promise_id:
                        promise_id = f"_pvg_{uuid.uuid4().hex}"
                        pvg['promise_id'] = promise_id
                    block.setdefault('applied_property_value_groups', []).append(
                        _PromiseRef(promise_id)
                    )
            for pv in block.get('property_values', []):
                if isinstance(pv, dict) and '_type' not in pv:
                    t = _pv_type_from_value(pv.get('value'))
                    if t:
                        pv['_type'] = t


_PROPERTY_CARDINALITY_ATTRS = frozenset({'properties', 'owned_properties', 'owned_features'})


def _default_cardinality_promises(patch_data: list) -> list[str]:
    """Ensure new Property/ExchangeItemElement children get a promise_id so
    their default min_card/max_card (note-0044 item 1, cousin_back_log) can
    be stamped after decl.apply() creates them.

    Capella Studio defaults a new property's/exchange-item-element's Min/Max
    Card to 1/1; apply_model_patch previously left it unset entirely.
    Confirmed empirically: neither decl.py's YAML engine nor a plain scalar/
    dict assignment can create a fresh min_card/max_card -- both are
    Single-wrapped Containment references to a whole LiteralNumericValue
    object, and every generic-engine path only knows how to append into
    lists or mutate an *existing* sub-object, never create a new one into
    an empty Single slot. The only way found: after the real object exists,
    assign capellambse.model.NewObject('LiteralNumericValue', value='1')
    directly via Python (bypassing decl.py entirely for this one step).
    That requires a real object reference, which doesn't exist until
    decl.apply() runs -- so this pass only ensures every such child has a
    promise_id (generating one if absent), and apply_patch resolves those
    promises against decl.apply()'s own return value afterward to do the
    actual stamping. Skips any child that already specifies its own
    min_card/max_card -- explicit intent in the patch is never overridden.
    """
    promise_ids: list[str] = []

    def _ensure_promise(child: dict) -> None:
        if 'min_card' in child or 'max_card' in child:
            return
        promise_id = child.get('promise_id')
        if not promise_id:
            promise_id = f"_card_{uuid.uuid4().hex}"
            child['promise_id'] = promise_id
        promise_ids.append(promise_id)

    for item in patch_data:
        if not isinstance(item, dict):
            continue
        extend = item.get('extend', {})
        if not isinstance(extend, dict):
            continue
        for attr in _PROPERTY_CARDINALITY_ATTRS | {'elements'}:
            for child in extend.get(attr, []):
                if isinstance(child, dict):
                    _ensure_promise(child)
        for ei in extend.get('exchange_items', []):
            if not isinstance(ei, dict):
                continue
            for child in ei.get('elements', []):
                if isinstance(child, dict):
                    _ensure_promise(child)

    return promise_ids


def _preprocess_patch(model, patch_yaml: str) -> tuple[str, list[str]]:
    """Parse patch YAML, enforce ARCADIA type conventions, re-serialize.

    Returns the processed YAML plus any promise_ids needing a default
    min_card/max_card stamped after decl.apply() runs (note-0044).
    """
    try:
        patch_data = yaml.load(patch_yaml, Loader=_PatchLoader)
    except yaml.YAMLError:
        return patch_yaml, []  # pass through; decl.apply() will surface the error
    if not isinstance(patch_data, list):
        return patch_yaml, []
    _reject_unsupported_exchange_creation(patch_data)
    _normalize_pa_component_key(model, patch_data)
    _enforce_function_types(model, patch_data)
    _enforce_component_types(model, patch_data)
    _enforce_pv_types(patch_data)
    cardinality_promise_ids = _default_cardinality_promises(patch_data)
    processed = yaml.dump(patch_data, Dumper=_PatchDumper, default_flow_style=False, allow_unicode=True)
    return processed, cardinality_promise_ids


def apply_patch(session: dict, patch_yaml: str) -> dict:
    """Apply a declarative YAML patch to the model and save it to disk."""
    aird_path = Path(session['aird_path'])
    model = open_model(aird_path, resources=session.get('resources') or None)
    try:
        processed_yaml, cardinality_promise_ids = _preprocess_patch(model, patch_yaml)
        promises = decl.apply(model, io.StringIO(processed_yaml))
        for pid in cardinality_promise_ids:
            obj = promises.get(decl.Promise(pid))
            if obj is None:
                continue
            if getattr(obj, 'min_card', 'n/a') is None:
                obj.min_card = capellambse_model.NewObject('LiteralNumericValue', value='1')
            if getattr(obj, 'max_card', 'n/a') is None:
                obj.max_card = capellambse_model.NewObject('LiteralNumericValue', value='1')
    except Exception as exc:
        return {"status": "error", "message": str(exc)}
    model.save()
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Verification / quality scan
# ---------------------------------------------------------------------------

_VERIFY_ACCESSORS: dict[str, dict[str, object]] = {
    "OA": {
        "entities":   lambda m: m.oa.all_entities,
        "activities": lambda m: m.oa.all_activities,
    },
    "SA": {
        "components": lambda m: m.sa.all_components,
        "functions":  lambda m: m.sa.all_functions,
    },
    "LA": {
        "components": lambda m: m.la.all_components,
        "functions":  lambda m: m.la.all_functions,
    },
    "PA": {
        "components": lambda m: m.pa.all_components,
        "functions":  lambda m: m.pa.all_functions,
    },
}


def verify_phase(session: dict, phase: str) -> dict:
    """Scan a model phase for common quality issues."""
    accessors = _VERIFY_ACCESSORS.get(phase.upper())
    if not accessors:
        return {"status": "error", "message": f"Unknown phase: {phase}. Use OA, SA, LA, or PA."}

    model = open_model(Path(session['aird_path']), resources=session.get('resources') or None)
    findings: dict[str, list[dict]] = {}

    for label, getter in accessors.items():
        unnamed = [_object_info(o) for o in getter(model) if not getattr(o, 'name', '').strip()]
        if unnamed:
            findings[f"unnamed_{label}"] = unnamed

    if phase.upper() in ("SA", "LA", "PA"):
        fn_getter = accessors.get("functions")
        if fn_getter:
            unallocated = []
            for fn in fn_getter(model):
                try:
                    if not fn.allocating_components:
                        unallocated.append(_object_info(fn))
                except Exception:
                    pass
            if unallocated:
                findings["unallocated_functions"] = unallocated

    return {
        "phase": phase.upper(),
        "findings": findings,
        "total_issues": sum(len(v) for v in findings.values()),
    }
