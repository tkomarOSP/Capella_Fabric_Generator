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
        "Use clone_capella_repo first to establish a session, then browse or "
        "resolve UUIDs, then generate_fabric to get the YAML content. "
        "Call cleanup_session when done to release disk space."
    ),
)


# ---------------------------------------------------------------------------
# Tool 1 — Clone repo and create session
# ---------------------------------------------------------------------------

@mcp.tool()
def clone_capella_repo(
    repo_url: str,
    github_pat: str,
    include_realized: bool = False,
    include_realizing: bool = False,
) -> dict:
    """Clone a GitHub repository containing a Capella model.

    Returns a session_id used by all subsequent tools.

    Args:
        repo_url: HTTPS URL of the GitHub repository
                  (e.g. https://github.com/owner/repo or https://github.com/owner/repo.git)
        github_pat: GitHub personal access token with repo read access
        include_realized: Include realized references in the generated fabric
        include_realizing: Include realizing references in the generated fabric
    """
    session_id = svc.create_session()
    try:
        git_svc.clone_repo(repo_url, github_pat, session_id)
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

    Args:
        session_id:  Session ID returned by clone_capella_repo
        phase:       One of OA, SA, LA, PA
        object_type: Object type within that phase (e.g. Component, Requirement, Function)
    """
    try:
        session = svc.load_session(session_id)
        model   = svc.open_model(Path(session['aird_path']))
        return svc.search_by_name(model, phase, object_type, '')
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

    Args:
        session_id:  Session ID returned by clone_capella_repo
        phase:       One of OA, SA, LA, PA
        object_type: Object type within that phase
        name_query:  Substring to match against object names
    """
    try:
        session = svc.load_session(session_id)
        model   = svc.open_model(Path(session['aird_path']))
        return svc.search_by_name(model, phase, object_type, name_query)
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
        model                = svc.open_model(Path(session['aird_path']))
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
# Tool 6 — Cleanup
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
