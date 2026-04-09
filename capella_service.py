# Copyright — Capella Fabric Generator
# Service layer: model loading, UUID resolution, YAML fabric generation.

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
_CAPELLA_TOOLS = Path(r'C:\apps\.metadata\Capella_Tools')
if str(_CAPELLA_TOOLS) not in sys.path:
    sys.path.insert(0, str(_CAPELLA_TOOLS))

import capellambse
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

def open_model(aird_path: Path) -> capellambse.MelodyModel:
    """Open a Capella 7.0.1 model from an .aird file path."""
    return capellambse.MelodyModel(str(aird_path))


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

    model = open_model(aird_path)

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
