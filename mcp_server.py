# Copyright 2024-2026 Open Sun Power, LLC
# SPDX-License-Identifier: Apache-2.0
# mcp_server.py — Capella Fabric Generator MCP Server
#
# Exposes the same browse / resolve / generate workflow as the web app,
# but sources Capella models from GitHub repositories instead of ZIP uploads.
#
# Transport: streamable-http  (POST /mcp)
# Port:      8001  (web app runs on 8000)
#
# Usage:
#   python mcp_server.py
#
# Claude Desktop config (~/.claude/claude_desktop_config.json):
#   {
#     "mcpServers": {
#       "capella-fabric": {
#         "url": "https://mcp.innovatingwithcapella.com/mcp"
#       }
#     }
#   }

import os
import re
import sys
from pathlib import Path

# Make sure Capella_Tools is importable (mirrors capella_service.py bootstrap)
_CAPELLA_TOOLS = Path(os.environ.get('CAPELLA_TOOLS_PATH', r'C:\apps\.metadata\Capella_Tools'))
if str(_CAPELLA_TOOLS) not in sys.path:
    sys.path.insert(0, str(_CAPELLA_TOOLS))

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
import capella_service as svc
import git_service as git_svc


def _find_aird_in(directory: Path) -> Path | None:
    """Return the first .aird file found recursively under directory, or None."""
    hits = list(directory.rglob('*.aird'))
    return hits[0] if hits else None


def _resolve_object_type(phase: str, object_type: str) -> str | None:
    """Return the canonical object_type key for the given phase, or None if unrecognised.

    Matching is case-insensitive and tolerates underscores/hyphens in place of spaces,
    so "functional_chain", "Functional Chain", and "functional chain" all resolve to
    "Functional Chain".
    """
    valid = svc.get_phase_types().get(phase, [])
    needle = object_type.strip().lower().replace('_', ' ').replace('-', ' ')
    for t in valid:
        if t.lower() == needle:
            return t
    return None


mcp = FastMCP(
    "Capella Fabric Generator",
    host='127.0.0.1',
    port=8001,
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[
            "mcp.innovatingwithcapella.com",
            "127.0.0.1:*",
            "localhost:*",
            "[::1]:*",
        ],
    ),
    instructions=(
        "Use clone_capella_repo first to establish a session. "
        "If the model depends on library repos, call add_dependency_repo for each before browsing. "
        "Call list_object_types() to discover valid phase/object_type combinations before browsing. "
        "Then browse or resolve UUIDs, then generate_fabric to get the YAML content. "
        "apply_model_patch uses py-capellambse's declarative format: target existing elements "
        "with !uuid <uuid>, use set: to update attributes and extend: to add children, and "
        "promise_id:/!promise for forward-references within the same patch. The server "
        "pre-processes patch YAML and auto-injects _type in three cases, so you may omit it "
        "entirely: function/activity children of extend: functions:/owned_functions:/"
        "activities:/owned_activities: get OperationalActivity (OA) / SystemFunction (SA) / "
        "LogicalFunction (LA) / PhysicalFunction (PA) based on the parent's phase; component "
        "children of extend: components: (SA -> SystemComponent, LA -> LogicalComponent) or "
        "extend: owned_components: (PA -> PhysicalComponent) get the matching type -- without "
        "_type, capellambse creates malformed Part objects instead. At the PA phase "
        "specifically, always use owned_components:, not components: -- PhysicalComponent."
        "components is a deprecated, non-model-coupled property on current capellambse and "
        "extending it raises 'not model-coupled'; owned_components is the real containment. "
        "(Older capellambse installs that predate this split don't have this quirk -- there "
        "components: is already the real containment and works as-is.) Property value children "
        "of extend: property_value_groups: get StringPropertyValue/FloatPropertyValue/"
        "IntegerPropertyValue/BooleanPropertyValue from the Python value's type, and "
        "PropertyValueGroup on the group itself; creating a group this way also automatically "
        "back-references it onto the parent's applied_property_value_groups in the same patch "
        "-- no separate follow-up patch is needed for Capella to treat the group as applied. "
        "Explicit _type values are always respected; auto-injection only fills in when absent. "
        "apply_model_patch cannot create FunctionalExchange/ComponentExchange/PhysicalLink "
        "elements (extend: exchanges:/component_exchanges:/physical_links:) -- these connect "
        "ports that this tool doesn't create or validate, so such patches are rejected with a "
        "clear error rather than silently producing invalid XML. Create these directly in the "
        "Capella desktop editor; renaming/retagging *existing* ones via set: name: still works "
        "fine. Call verify_model after patching to scan for quality issues, then "
        "push_model_changes to sync to GitHub. "
        "Call cleanup_session when done to release disk space. "
        "© Open Sun Power, LLC — Apache 2.0."
    ),
)


# ---------------------------------------------------------------------------
# Tool 1 — Clone repo and create session
# ---------------------------------------------------------------------------

@mcp.tool()
def clone_capella_repo(
    repo_url: str,
    github_pat: str,
    branch: str = "",
    include_realized: bool = False,
    include_realizing: bool = False,
) -> dict:
    """Clone a GitHub repository containing a Capella model.

    Returns a session_id used by all subsequent tools.
    If the model depends on library repos, call add_dependency_repo next.

    Args:
        repo_url: HTTPS URL of the GitHub repository
                  (e.g. https://github.com/owner/repo or https://github.com/owner/repo.git)
        github_pat: GitHub personal access token with repo read access
        branch: Git branch to clone (default: repo's default branch)
        include_realized: Include realized references in the generated fabric
        include_realizing: Include realizing references in the generated fabric
    """
    session_id = svc.create_session()
    try:
        git_svc.clone_repo(repo_url, github_pat, session_id, branch=branch)
    except Exception as exc:
        svc.cleanup_session(session_id)
        return {"error": str(exc)}

    aird_path = svc.find_aird_file(session_id)
    if aird_path is None:
        svc.cleanup_session(session_id)
        return {"error": "No .aird file found in the repository."}

    archive_name = repo_url.rstrip('/').split('/')[-1].removesuffix('.git')
    svc.save_session(session_id, {
        'session_id':        session_id,
        'archive_name':      archive_name,
        'aird_path':         str(aird_path),
        'resolved_uuids':    [],
        'include_realized':  include_realized,
        'include_realizing': include_realizing,
        'yaml_path':         None,
        'resources':         {},
    })
    return {
        "session_id": session_id,
        "aird_file":  aird_path.name,
        "message":    f"Cloned '{archive_name}'. Use session_id for subsequent calls.",
    }


# ---------------------------------------------------------------------------
# Tool 2 — Browse model objects by phase + type
# ---------------------------------------------------------------------------

@mcp.tool()
def browse_model(session_id: str, phase: str, object_type: str) -> list[dict]:
    """List all objects of a given type within a Capella model phase.

    Call list_object_types() first to see valid phase/object_type combinations.
    object_type matching is case-insensitive (e.g. "functional chain" works).

    Args:
        session_id:  Session ID returned by clone_capella_repo
        phase:       One of OA, SA, LA, PA
        object_type: Object type within that phase — call list_object_types() for valid values
    """
    canonical = _resolve_object_type(phase, object_type)
    if canonical is None:
        valid = svc.get_phase_types().get(phase, [])
        return [{"error": f"Unknown object_type '{object_type}' for phase {phase}. Valid types: {valid}"}]
    try:
        session = svc.load_session(session_id)
        model   = svc.open_model(Path(session['aird_path']), resources=session.get('resources') or None)
        return svc.search_by_name(model, phase, canonical, '')
    except Exception as exc:
        return [{"error": str(exc)}]


# ---------------------------------------------------------------------------
# Tool 3 — Search model objects by name
# ---------------------------------------------------------------------------

@mcp.tool()
def search_model_objects(
    session_id:  str,
    phase:       str,
    object_type: str,
    name_query:  str,
) -> list[dict]:
    """Search model objects by name (case-insensitive substring match).

    Call list_object_types() first to see valid phase/object_type combinations.
    object_type matching is case-insensitive (e.g. "functional chain" works).

    Args:
        session_id:  Session ID returned by clone_capella_repo
        phase:       One of OA, SA, LA, PA
        object_type: Object type within that phase — call list_object_types() for valid values
        name_query:  Substring to match against object names
    """
    canonical = _resolve_object_type(phase, object_type)
    if canonical is None:
        valid = svc.get_phase_types().get(phase, [])
        return [{"error": f"Unknown object_type '{object_type}' for phase {phase}. Valid types: {valid}"}]
    try:
        session = svc.load_session(session_id)
        model   = svc.open_model(Path(session['aird_path']), resources=session.get('resources') or None)
        return svc.search_by_name(model, phase, canonical, name_query)
    except Exception as exc:
        return [{"error": str(exc)}]


# ---------------------------------------------------------------------------
# Tool 4 — Resolve UUIDs and save to session
# ---------------------------------------------------------------------------

@mcp.tool()
def resolve_model_uuids(session_id: str, uuids: list[str]) -> dict:
    """Resolve UUIDs to model objects and save them to the session for fabric generation.

    Args:
        session_id: Session ID returned by clone_capella_repo
        uuids:      List of Capella object UUIDs to resolve
    """
    try:
        session              = svc.load_session(session_id)
        model                = svc.open_model(Path(session['aird_path']), resources=session.get('resources') or None)
        resolved, not_found  = svc.resolve_uuids(model, uuids)
        session['resolved_uuids'] = [r['uuid'] for r in resolved]
        svc.save_session(session_id, session)
        return {"resolved": resolved, "not_found": not_found}
    except Exception as exc:
        return {"error": str(exc)}


# ---------------------------------------------------------------------------
# Tool 5 — Generate YAML fabric
# ---------------------------------------------------------------------------

@mcp.tool()
def generate_fabric(session_id: str) -> dict:
    """Generate a YAML fabric for the resolved UUIDs in the session.

    Call resolve_model_uuids (or browse_model) first to populate the UUID list.

    Args:
        session_id: Session ID returned by clone_capella_repo
    """
    try:
        session              = svc.load_session(session_id)
        yaml_path, obj_count = svc.generate_fabric(session)
        content              = yaml_path.read_text(encoding='utf-8')
        return {
            "yaml_content":  content,
            "object_count":  obj_count,
            "filename":      yaml_path.name,
        }
    except Exception as exc:
        return {"error": str(exc)}


# ---------------------------------------------------------------------------
# Tool 6 — Add a dependency library repository to the session
# ---------------------------------------------------------------------------

@mcp.tool()
def add_dependency_repo(
    session_id:    str,
    repo_url:      str,
    github_pat:    str,
    resource_name: str,
    branch:        str = "",
) -> dict:
    """Clone a dependency library repository and register it with the session.

    Call after clone_capella_repo, before browse_model or generate_fabric.
    resource_name must match the name used in the main model's cross-references
    (e.g. "Bike BrakeSystem Library"). Can be called multiple times for multiple libraries.

    Args:
        session_id:    Session ID returned by clone_capella_repo
        repo_url:      HTTPS URL of the dependency repository
        github_pat:    GitHub PAT with repo read access
        resource_name: Name this library is referenced by in the main model
        branch:        Git branch to clone (default: repo's default branch)
    """
    try:
        session  = svc.load_session(session_id)
        safe_dir = re.sub(r'[^\w\-]', '_', resource_name)
        dep_dir  = svc._session_dir(session_id) / 'deps' / safe_dir
        git_svc.clone_to_dir(repo_url, github_pat, dep_dir, branch=branch)
    except Exception as exc:
        return {"error": str(exc)}

    aird = _find_aird_in(dep_dir)
    if aird is None:
        return {"error": f"No .aird file found in dependency repo '{resource_name}'."}

    model_folder = str(aird.parent)
    session.setdefault('resources', {})[resource_name] = {"path": model_folder}
    svc.save_session(session_id, session)
    return {
        "resource_name": resource_name,
        "model_folder":  model_folder,
        "aird_file":     aird.name,
        "message":       f"Dependency '{resource_name}' registered. Proceed with browse_model or generate_fabric.",
    }


# ---------------------------------------------------------------------------
# Tool 7 — List valid object types (no session required)
# ---------------------------------------------------------------------------

@mcp.tool()
def list_object_types() -> dict:
    """Return all valid phase and object_type values for browse_model and search_model_objects.

    Call this before browse_model if unsure what object types exist for a phase.
    Returns a dict mapping phase (OA/SA/LA/PA) to a list of valid object_type strings.
    No session required.
    """
    return svc.get_phase_types()


# ---------------------------------------------------------------------------
# Tools 8-10 — Write / verify / push
# ---------------------------------------------------------------------------

@mcp.tool()
def apply_model_patch(
    session_id: str,
    patch_yaml: str,
    commit_message: str,
    author_name: str = "",
    author_email: str = "",
) -> dict:
    """Apply a declarative YAML patch to the Capella model, save, and git-commit.

    Uses py-capellambse's decl.apply() format. Target existing elements with
    `!uuid <uuid>` and use `set:` to update properties or `extend:` to add
    children. Use `promise_id:` / `!promise` for forward-references within the
    same patch. See this server's own `instructions` (returned at connection
    time) for the full `_type` auto-injection rules (function/activity,
    component, property-value), the PA-phase `owned_components:` requirement,
    the automatic property-value-group back-reference, and which `extend:`
    targets are rejected (exchanges/component_exchanges/physical_links).

    Scope convention: limit creation to structure (components/entities),
    functions, and activities. Call push_model_changes afterward to sync to
    GitHub.

    patch_yaml examples (YAML string):

        # Add a child component and function — _type auto-injected from phase:
        - parent: !uuid <parent-component-uuid>
          extend:
            components:
              - name: New Subsystem
            functions:
              - name: Process Input Data

        # Add a property value group — _type auto-injected, and the group is
        # automatically applied to the parent in this same patch (no second
        # patch needed):
        - parent: !uuid <component-uuid>
          extend:
            property_value_groups:
              - name: Mass
                property_values:
                  - name: units
                    value: kg          # str → StringPropertyValue
                  - name: value
                    value: 12.5        # float → FloatPropertyValue

    Args:
        session_id:     Session ID from clone_capella_repo.
        patch_yaml:     Declarative YAML document (list of patch entries).
        commit_message: Git commit message describing this change.
        author_name:    Git author name (e.g. "Tony Komar"). Defaults to the
                        server's configured git identity if omitted.
        author_email:   Git author e-mail address.
    """
    try:
        session = svc.load_session(session_id)
        result  = svc.apply_patch(session, patch_yaml)
        if result['status'] != 'ok':
            return result
        commit_result = git_svc.commit_changes(
            session_id, commit_message, author_name, author_email
        )
        return {"patch": result, "commit": commit_result}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


@mcp.tool()
def push_model_changes(session_id: str) -> dict:
    """Push all committed model changes to the remote GitHub repository.

    Call after one or more apply_model_patch calls when ready to persist.
    The session's git remote already carries credentials from clone_capella_repo.

    Args:
        session_id: Session ID from clone_capella_repo.
    """
    try:
        return git_svc.push_changes(session_id)
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


@mcp.tool()
def verify_model(session_id: str, phase: str) -> dict:
    """Scan a model phase for common quality issues.

    Checks performed:
      - Elements with missing or empty names
      - Functions not allocated to any component (SA / LA / PA)

    Returns findings grouped by category with object info for each issue.
    An empty findings dict means no issues were found.

    Args:
        session_id: Session ID from clone_capella_repo.
        phase:      OA, SA, LA, or PA.
    """
    try:
        session = svc.load_session(session_id)
        return svc.verify_phase(session, phase)
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


# ---------------------------------------------------------------------------
# Tool 11 — Cleanup
# ---------------------------------------------------------------------------

@mcp.tool()
def cleanup_session(session_id: str) -> dict:
    """Delete the cloned repository and all session temp files.

    Args:
        session_id: Session ID returned by clone_capella_repo
    """
    svc.cleanup_session(session_id)
    return {"status": "cleaned up", "session_id": session_id}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    mcp.run(transport='streamable-http')
