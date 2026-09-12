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

# ---------------------------------------------------------------------------
# Optional Cartenza integration (cousin_back_log/note-0086)
#
# A well-behaved agent's safety layer refuses to pass a raw PAT as a tool-call
# argument -- correctly, and by design. That made clone_capella_repo
# uncallable by an agent even when its own registered scope granted access to
# the repo. knowledge-repo solved this once already with begin_connect: the
# agent hands the human a plain, non-secret URL, the human authorizes in a
# browser, and the agent redeems a code that never carries the secret itself.
#
# kp-auth is an OPTIONAL dependency and stays that way. Installed (Cartenza
# deployment): the credential-free path lights up. Absent (anyone running this
# server standalone): every tool below behaves exactly as it always has, with
# github_pat passed directly. Same binary, no fork.
# ---------------------------------------------------------------------------
try:
    from auth.connect import (  # type: ignore
        mint_connect_code,
        redeem_connect_code,
        consume_connect_code,
        resolve_oauth_credential,
    )
    _AUTH_AVAILABLE = True
except ImportError:
    _AUTH_AVAILABLE = False

_CONNECT_BASE_URL = os.environ.get('KP_CONNECT_BASE_URL', '').rstrip('/')
# Tags codes minted here so they can't be redeemed by kp-knowledge-repo, which
# shares the same connect_codes table once co-deployed.
_CONNECT_AUDIENCE = 'capella'

_NO_AUTH_MSG = (
    "Credential-free connect isn't configured on this server — pass github_pat "
    "directly instead."
)


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
            # Cartenza co-deploy (note-0086): the same binary served from the
            # Cartenza droplet, where kp-auth is installed and the
            # credential-free connect path is live. A hostname missing from
            # this list 421s every request -- it is not a config-file setting.
            "capella.cartenza.ai",
            "dev.capella.cartenza.ai",
            "127.0.0.1:*",
            "localhost:*",
            "[::1]:*",
        ],
    ),
    instructions=(
        "This server is for the Capella model itself (OA/SA/LA/PA layers, requirements, "
        "diagrams) — it is NOT for logging notes/observations/decisions about the work. "
        "For that, use knowledge_repo (add_log_entry/append_log_entry), a separate MCP server "
        "(cousin_back_log/note-0050 — a real ChatGPT session once reached for the wrong MCP "
        "server for a logging task and had to be manually redirected; check which server a task "
        "actually belongs to before guessing when several are connected). "
        "Use clone_capella_repo first to establish a session. "
        "If your own safety layer blocks credential-shaped tool-call arguments (a raw "
        "github_pat value), don't try to disguise or reformat them — use "
        "begin_connect(agent_id, repo_url, branch) instead: it returns a plain, non-secret URL "
        "for the human to open in a browser and authorize there, then call "
        "clone_capella_repo(connect_code=...) with no github_pat/repo_url/branch needed "
        "(cousin_back_log/NOTE-0062, note-0086). Requires a registered agent first, at "
        "/onboarding/agents on the Cartenza site. On that connect page, 'Connect with GitHub' "
        "is better than pasting a PAT: one GitHub authorization also covers the model's library "
        "repos, so add_dependency_repo then needs no credential at all. "
        "Two things about connect codes that save the human unnecessary browser trips: a code is "
        "consumed only when a clone actually SUCCEEDS, so if clone_capella_repo fails (bad branch, "
        "empty repo, no .aird) retry with the SAME code rather than asking for a new link — the "
        "error message says so explicitly. And a code expires 15 minutes after begin_connect, so "
        "only then does the user genuinely need to reissue one. "
        "Sessions are disposable and are swept about 4 hours after last use; a session_id that has "
        "gone quiet that long, or one that predates a server restart, is gone and needs a fresh "
        "clone. That is a new authorization event, so batch model work rather than re-cloning "
        "between every step. "
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
        "One documented gap at PA: child PhysicalFunctions cannot be created under a "
        "PhysicalFunction by any patch shape or _type value — capellambse 0.8.1 binds "
        "PhysicalFunction.functions to PhysicalComponent (ownedPhysicalComponents) and defines no "
        "owned-child-function attribute on PhysicalFunction, so the type hint can never resolve. "
        "This is an upstream metamodel defect, not a patch error, and the server now says so "
        "rather than letting capellambse's bare 'Invalid type hint: PhysicalFunction' through. "
        "Create such functions under the LA Root Logical Function (the identical patch shape works "
        "there) and realize them onto PA, or model them in the Capella desktop editor. Targeting "
        "the containing PhysicalFunctionPkg does succeed but makes siblings of the root physical "
        "function, not children — a different structure, so choose it deliberately if at all. "
        "apply_model_patch cannot create FunctionalExchange/ComponentExchange/PhysicalLink "
        "elements (extend: exchanges:/component_exchanges:/physical_links:) -- these connect "
        "ports that this tool doesn't create or validate, so such patches are rejected with a "
        "clear error rather than silently producing invalid XML. Create these directly in the "
        "Capella desktop editor; renaming/retagging *existing* ones via set: name: still works "
        "fine. "
        "Data-modeling elements -- Data Package, Class, Association, Exchange Item, Exchange "
        "Item Element, Data Type -- are browsable via browse_model/search_model_objects under "
        "any phase (searched model-wide, since they live under a DataPkg, not one architecture "
        "layer). Data Type covers primitive types (Integer, Boolean, Float, String, etc, "
        "usually in a Predefined Types DataPkg) -- resolve one this way and reference its uuid "
        "in a Class property's type:, which already works fine once you have the uuid. "
        "Diagram browsing is model-wide regardless of phase passed -- this now includes CDB "
        "(Class Diagram Blank / data) diagrams, which have viewpoint 'Common' and were "
        "previously invisible from every phase (each layer's diagrams accessor filters by a "
        "fixed viewpoint string, not true layer-scoping). "
        "Naming/describing any of these, or a FunctionalExchange, works via plain set: name:/"
        "set: description:, no special handling needed. Creating a Class (extend: classes: "
        "under a DataPkg), a Property on it (extend: owned_properties: -- despite being a "
        "Filter, this works correctly, no owned_features: workaround needed), an Association "
        "connecting two classes (extend: associations: with nested extend: members: Property "
        "ends, each type: !uuid/!promise a class), and allocating an ExchangeItem onto a "
        "FunctionalExchange (extend: exchanged_items: [!promise ...] -- a reference, not "
        "nested creation) all work with no _type injection needed. New Property/"
        "ExchangeItemElement children (properties:/owned_properties:/owned_features:/"
        "elements:) get min card: 1 / max card: 1 stamped automatically, matching what "
        "Capella Studio itself defaults to -- decl.py has no way to create a fresh "
        "min_card/max_card into an empty Single container from patch YAML, so this happens "
        "via a direct Python NewObject('LiteralNumericValue', value='1') assignment after "
        "decl.apply() creates the real object, not through the YAML engine. An explicit "
        "*custom* cardinality (e.g. min_card: 0) still isn't supported via patch YAML -- "
        "set a non-default Min/Max Card in the Capella desktop editor. "
        "Call verify_model after patching to scan for quality issues, then "
        "push_model_changes to sync to GitHub. "
        "Call cleanup_session when done to release disk space. "
        "© Open Sun Power, LLC — Apache 2.0."
    ),
)


# ---------------------------------------------------------------------------
# Tool 1 — Clone repo and create session
# ---------------------------------------------------------------------------

@mcp.tool()
def begin_connect(agent_id: str, repo_url: str, branch: str = "main") -> dict:
    """Start a credential-free connect — the alternative to passing github_pat.

    For clients whose safety layer blocks credential-shaped tool-call arguments.
    Never pass a PAT or api_key to this tool, or any tool — that's the whole
    point. Checks the request against the given agent's *registered* scope
    (register one at /onboarding/agents) before issuing anything; an
    out-of-scope repo/branch is rejected here, not silently allowed.

    Returns a one-time link. Show it to the human as a plain URL — it's not a
    secret, safe to print verbatim. They open it in a browser (out of your own
    tool-call channel entirely) and authorize there. Once they've done that,
    call clone_capella_repo(connect_code=<code>) — no github_pat, repo_url or
    branch needed, they're resolved from the code.

    Prefer "Connect with GitHub" on that page over pasting a PAT: a GitHub
    authorization covers the model repo AND its library repos, so
    add_dependency_repo then needs no further credential. A pasted PAT is a
    single-use snapshot and each dependency would need its own connect code.

    Args:
        agent_id: A registered agent's id (from /onboarding/agents).
        repo_url: HTTPS URL of the Capella model repo to connect.
        branch: Branch to connect (default: main).
    """
    if not _AUTH_AVAILABLE:
        return {"error": _NO_AUTH_MSG}
    if not _CONNECT_BASE_URL:
        return {"error": "KP_CONNECT_BASE_URL env var not set"}

    minted = mint_connect_code(
        agent_id=agent_id, remote_url=repo_url, branch=branch,
        audience=_CONNECT_AUDIENCE,
    )
    if "error" in minted:
        return minted

    return {
        "connect_url": f"{_CONNECT_BASE_URL}/connect?code={minted['code']}",
        "expires_in_minutes": minted["expires_in_minutes"],
        "message": "Show this URL to the user — it's not a secret. They open it in a browser to "
                   "authorize; you never see their credential. Once they've done that, call "
                   "clone_capella_repo(connect_code=...).",
    }


@mcp.tool()
def clone_capella_repo(
    repo_url: str = "",
    github_pat: str = "",
    branch: str = "",
    include_realized: bool = False,
    include_realizing: bool = False,
    connect_code: str = "",
) -> dict:
    """Clone a GitHub repository containing a Capella model.

    Returns a session_id used by all subsequent tools.
    If the model depends on library repos, call add_dependency_repo next.

    Two ways to authenticate — use whichever fits your client:
    - **`github_pat` directly** — fine for clients that can safely pass a PAT
      as a tool-call argument (e.g. Claude Code, with the PAT sourced from an
      environment variable, never authored by the model).
    - **`connect_code`** (from `begin_connect`) — for clients whose safety
      layer blocks credential-shaped tool-call arguments. No `github_pat`/
      `repo_url`/`branch` needed in that case; they're resolved server-side
      from the code, which a human already authorized in a browser. The model
      never sees the actual credential.

    Args:
        repo_url: HTTPS URL of the GitHub repository
                  (e.g. https://github.com/owner/repo or https://github.com/owner/repo.git).
                  Omit when using connect_code.
        github_pat: GitHub personal access token with repo read access.
                    Omit when using connect_code.
        branch: Git branch to clone (default: repo's default branch).
                Omit when using connect_code — the branch is whatever
                begin_connect scoped this code to.
        include_realized: Include realized references in the generated fabric
        include_realizing: Include realizing references in the generated fabric
        connect_code: A code from begin_connect, in place of repo_url/github_pat/branch.
    """
    oauth_connection_id = None
    if connect_code:
        if not _AUTH_AVAILABLE:
            return {"error": _NO_AUTH_MSG}
        resolved = redeem_connect_code(connect_code, audience=_CONNECT_AUDIENCE)
        if "error" in resolved:
            return resolved
        repo_url = resolved["remote_url"]
        github_pat = resolved["pat"]
        branch = resolved["branch"] or ""
        # An id, not a secret -- lets add_dependency_repo re-resolve the same
        # live GitHub authorization for library repos without a second human
        # round trip (note-0086). None on the PAT path, which is single-use.
        oauth_connection_id = resolved["oauth_connection_id"]
    elif not repo_url or not github_pat:
        return {"error": "Provide either (repo_url and github_pat) or connect_code."}

    # Both failure paths below run BEFORE consume_connect_code, so the code is
    # still live. Say so explicitly: the invariant was previously documented
    # only in source comments, so a real user rediscovered it by trial and
    # (reasonably) declined to rely on it, paying for extra browser round trips
    # instead (After_Treatment_System_Notebook/Fabric_MCP_Issues OBS-0003).
    retry_hint = (" Your connect_code was NOT consumed — fix the cause and call "
                  "clone_capella_repo again with the same code. Only ask the user "
                  "for a new link if it has expired (15 minutes from issue).") if connect_code else ""

    session_id = svc.create_session()
    try:
        git_svc.clone_repo(repo_url, github_pat, session_id, branch=branch)
    except Exception as exc:
        svc.cleanup_session(session_id)
        return {"error": f"{exc}{retry_hint}"}

    aird_path = svc.find_aird_file(session_id)
    if aird_path is None:
        svc.cleanup_session(session_id)
        return {"error": "No .aird file found in the repository." + retry_hint}

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
        'oauth_connection_id': oauth_connection_id,
    })

    if connect_code:
        # Burn the code only now that the clone actually succeeded -- a
        # transient git failure must not spend the one use the human granted.
        consume_connect_code(connect_code)

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
    resource_name: str,
    github_pat:    str = "",
    branch:        str = "",
) -> dict:
    """Clone a dependency library repository and register it with the session.

    Call after clone_capella_repo, before browse_model or generate_fabric.
    resource_name must match the name used in the main model's cross-references
    (e.g. "Bike BrakeSystem Library"). Can be called multiple times for multiple libraries.

    github_pat is optional. If the session was started with a connect_code that
    the human fulfilled by choosing "Connect with GitHub", that one GitHub
    authorization already covers their library repos too — omit github_pat and
    it is reused automatically, no further browser round trip. If they fulfilled
    it by pasting a PAT instead, that snapshot is single-use by design and this
    tool will say so; run begin_connect again for this dependency (or prefer the
    GitHub option next time).

    Args:
        session_id:    Session ID returned by clone_capella_repo
        repo_url:      HTTPS URL of the dependency repository
        resource_name: Name this library is referenced by in the main model
        github_pat:    GitHub PAT with repo read access. Omit to reuse the
                       session's existing GitHub authorization.
        branch:        Git branch to clone (default: repo's default branch)
    """
    try:
        session  = svc.load_session(session_id)
    except Exception as exc:
        return {"error": str(exc)}

    if not github_pat:
        conn_id = session.get('oauth_connection_id')
        if not conn_id:
            return {"error": "This session has no reusable GitHub authorization — it was started "
                             "with a PAT, or with a connect code the user fulfilled by pasting a "
                             "PAT (single-use by design). Pass github_pat, or call begin_connect "
                             "again for this dependency repo."}
        if not _AUTH_AVAILABLE:
            return {"error": _NO_AUTH_MSG}
        # Decrypt fresh per call from the connection id held in session.json --
        # the id is not itself a secret (note-0086).
        github_pat = resolve_oauth_credential(conn_id) or ""
        if not github_pat:
            return {"error": "The GitHub connection this session was authorized with no longer exists. "
                             "Pass github_pat, or call begin_connect again."}

    try:
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
    # An OAuth-authorized session may have outlived its 8-hour access token,
    # which clone_capella_repo baked into origin. Re-resolve before pushing
    # rather than failing with the model edits already committed locally
    # (cousin_back_log/note-0093). PAT sessions carry no connection id and skip
    # this entirely, as does a standalone deployment with no kp-auth installed.
    try:
        session = svc.load_session(session_id)
    except Exception as exc:
        return {"status": "error", "message": str(exc)}
    conn_id = session.get('oauth_connection_id')
    if conn_id and _AUTH_AVAILABLE:
        token = resolve_oauth_credential(conn_id) or ""
        if not token:
            return {"status": "error",
                    "message": "The GitHub authorization for this session has expired or been "
                               "revoked. Your model changes are still committed locally. Ask the "
                               "user to reconnect, then start a new session and re-apply, or pass "
                               "a PAT via clone_capella_repo."}
        try:
            git_svc.repoint_origin(session_id, token)
        except Exception:
            pass  # fall through and let the push report the real failure

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
