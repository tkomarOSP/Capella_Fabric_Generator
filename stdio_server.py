# Copyright 2024-2026 Open Sun Power, LLC
# SPDX-License-Identifier: Apache-2.0
"""Capella Fabric as a local MCP server. Transport: stdio.

The third front end in this repository, beside ``mcp_server.py`` (hosted,
streamable-http on 8001) and ``app.py`` (the Flask site). All three sit over
the same ``capella_service.py`` and ``git_service.py``, and **none of those
four files is edited** -- the only coupling this one adds is a single
assignment onto ``capella_service._session_dir``, installed from
``capella_bundle.session``. That is what lets a live hosted service and a
desktop server share a 2000-line model layer.

Sixteen tools, registered here rather than filtered out of the hosted server.
The hosted ``mcp_server`` registers 14 at module scope, so there is no point at
which a subset could be chosen, and importing it to get at them would start a
second ``FastMCP``, pull in Starlette and an HTTP transport, and run its
optional ``auth`` probe. The split:

    14 hosted
     -1 hosted-only        begin_connect: connect codes need the Cartenza
                           database and website, neither of which is here.
     -1 moved              add_dependency_repo: library repositories are
                           declared and cloned on the setup page, because
                           that is where slow work and credentials belong.
    ---
     12 keepers, 2 of them renamed for what they now do:
                           clone_capella_repo -> open_model  (never clones)
                           cleanup_session    -> close_model (never deletes)
     +4 local-only         setup_model, use_profile, list_my_scopes,
                           generate_viewer_link
    ---
     16

**Absent, not erroring.** A tool that exists and answers "that needs the
Cartenza website" is worse than one that was never advertised: a harness
choosing tools cannot tell a broken capability from a missing one, and will
keep reaching for it.

**No credential crosses this interface.** The hosted ``clone_capella_repo``
takes ``github_pat``; nothing here takes a secret or returns one. Configuration
comes from the engineer's profile and the operating system's credential store.

**Nothing may be written to stdout.** Under stdio, stdout *is* the JSON-RPC
channel, so one stray line corrupts the session's first message. The banner and
every start-up failure go to stderr. ``mcp.run()`` takes no argument: stdio is
the MCP library's own default, and the hosted server's
``transport='streamable-http'`` is the override.

No ``host``, ``port`` or ``TransportSecuritySettings`` either. Under stdio they
are inert, and leaving them in would describe a network server that does not
exist.
"""

from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path
from typing import Any, Optional

# capella_bundle's __init__ runs the foundation bootstrap, which puts this
# repository's root at sys.path[0] (for capella_service) and finds the
# mcp_desktop checkout. So it has to be imported before either of them, and
# this is not an import that may be reordered alphabetically.
import capella_bundle  # noqa: F401  -- imported for its side effect
from capella_bundle import session as bsession
from capella_bundle import settings as bsettings
from capella_bundle.config import ConfigError, ModelConfig, require_git
from capella_bundle.instructions import INSTRUCTIONS

import capella_service as svc  # noqa: E402  (after the bootstrap above)
import git_service as git_svc  # noqa: E402

from mcp.server.fastmcp import FastMCP  # noqa: E402

mcp = FastMCP("Capella Fabric", instructions=INSTRUCTIONS)


# Which profile this server was *launched* for: the optional --profile in a
# harness recipe. Empty is the normal case and not an error.
PROFILE_NAME = ""

# Which profile this *session* chose, by calling use_profile().
#
# The distinction is the whole point of it. A harness recipe configures an
# agent *definition*, while which model someone is working in belongs to an
# agent *session*, and Simcenter AI Agents draws no line between the two: a
# profile written into a recipe reaches only sessions that start afterwards, so
# it cannot answer "which model are we in today" and leaves a newly created
# agent with nothing. A module global is session-scoped by construction --
# under stdio the harness starts one process per session.
SELECTED_PROFILE = ""

#: One ``(config, sync)`` pair per open model, not a module singleton: an
#: engineer may hold a read-only reference model open beside the one being
#: patched. Keyed by the session_id ``session.session_id_for`` derives from the
#: clone path, so a restart hands back the same key for the same model.
#:
#: The ``sync`` half is the dict ``session.open_model`` returned, kept because
#: ``ModelConfig.write_refusal`` is measured against it -- a clone that was
#: behind the remote at open is a clone a patch must not be committed onto.
_OPEN: dict[str, tuple[ModelConfig, dict[str, Any]]] = {}

#: Resolved lazily rather than at import, so that importing this module --
#: which the tool-surface test does -- never depends on a configured machine.
#: A missing profile must surface as a readable error from a tool, not as an
#: ImportError nobody can act on.
_PROFILE: Optional[bsettings.Profile] = None


def _profile() -> bsettings.Profile:
    """This session's profile: what it chose, else what it was launched with.

    The session's choice wins. An engineer who says "work in the brake-system
    model" has said something more specific than a recipe written weeks ago,
    and the recipe is the thing they cannot change from where they stand.
    """
    global _PROFILE
    if _PROFILE is None:
        _PROFILE = bsettings.load_profile(SELECTED_PROFILE or PROFILE_NAME)
    return _PROFILE


def _opened(session_id: str) -> tuple[ModelConfig, dict[str, Any]]:
    """The config and sync state this session was opened with.

    An unknown id is answered the same way ``session.get_session`` answers it,
    and for the same reason: re-opening is free here, because the clone is
    already on disk and no credential, browser trip or authorization is
    involved. An agent that reads "unknown session" and remembers the hosted
    rules may otherwise tell the engineer their work is gone.
    """
    found = _OPEN.get(session_id)
    if found is None:
        raise bsession.ModelError(
            f"Unknown session_id {session_id!r}. This server was probably "
            f"restarted since you last called open_model. Call it again -- it "
            f"costs nothing and loses nothing, because the clone is already "
            f"on disk and nothing has to be authorized."
        )
    return found


def _record(session_id: str) -> dict[str, Any]:
    """The ``session.json`` record ``capella_service`` reads."""
    _opened(session_id)          # the readable error, before the service's own
    return bsession.get_session(session_id)


def _model_for(session_id: str):
    """Open the Capella model itself, with its library repositories attached.

    Loaded per call and never cached, exactly as the hosted server does it: a
    ``MelodyModel`` holds the parsed XML, the clone can move under it on any
    fetch, and a cached model is how a server starts answering from a tree
    that is no longer on disk.
    """
    record = _record(session_id)
    return svc.open_model(Path(record["aird_path"]),
                          resources=record.get("resources") or None)


def _resolve_object_type(phase: str, object_type: str) -> Optional[str]:
    """The canonical ``object_type`` for a phase, or None if unrecognised.

    Eight lines duplicated from ``mcp_server``, deliberately, because the
    alternative is importing the hosted server into this process -- which
    would register its 14 tools, start a second FastMCP, and pull in the HTTP
    transport. The function is pure and reads its vocabulary from
    ``capella_service.get_phase_types()``, so the two copies cannot disagree
    about which types exist; only about how a name is normalised, which the
    tool-surface test pins against the hosted spelling.
    """
    valid = svc.get_phase_types().get(phase, [])
    needle = object_type.strip().lower().replace("_", " ").replace("-", " ")
    for candidate in valid:
        if candidate.lower() == needle:
            return candidate
    return None


def _unknown_type(phase: str, object_type: str) -> bsession.ModelError:
    valid = svc.get_phase_types().get(phase, [])
    return bsession.ModelError(
        f"Unknown object_type {object_type!r} for phase {phase}. Valid types: "
        f"{valid}"
    )


# ---------------------------------------------------------------------------
# Publishing an artifact out of the model directory
# ---------------------------------------------------------------------------


def _publish(cfg: ModelConfig, path: Path, *, move: bool) -> Path:
    """Put a generated artifact where it can be served, and return it there.

    ``capella_service`` writes both rendered views and generated fabrics into
    the session directory, which under this server's adapter *is* the model's
    permanent directory -- and that directory holds the clone, whose
    ``.git/config`` holds this model's access token for as long as the
    directory exists. Hosted, ``cleanup_session`` deletes it; here it is never
    deleted, so nothing inside it may ever be reachable from a web server.

    So the artifact is relocated to the model's views directory, which
    ``settings.to_config`` has already refused to let sit inside the model
    directory. A view is **moved**: nothing reads it back, and a model
    directory that never holds a servable file is a stronger invariant than
    one that holds files a containment check must keep protecting. A fabric is
    **copied**, because the path ``capella_service`` just returned is also what
    the tool reports and re-reads.
    """
    cfg.views_dir.mkdir(parents=True, exist_ok=True)
    target = cfg.views_dir / path.name
    if move:
        # Not Path.replace: the views directory may be on another drive, which
        # is a configuration the setup page allows on purpose.
        shutil.move(str(path), str(target))
    else:
        shutil.copy2(str(path), str(target))
    return target


def _links(cfg: ModelConfig, target: Path) -> dict[str, Any]:
    """How to hand this artifact over.

    A loopback URL once the local page server is running, and the file's own
    path until then. The path is never offered *as* a link: a path is not a
    link, a chat window will not open one, and the engineer is left copying it
    into a browser by hand. So the two cases say different things.
    """
    site = _page_site()
    if site is None:
        return {
            "path": str(target),
            "viewer_note": (
                f"This page is self-contained and opens in any browser from "
                f"{target}. No local page server is running in this build, so "
                f"there is no link to hand over yet -- tell the engineer the "
                f"file is there rather than presenting the path as something "
                f"to click."
            ),
        }
    url = site.link_for(cfg.name, target.name)
    return {
        "path": str(target),
        "url": url,
        "viewer_markdown": f"[{target.name}]({url})",
        "viewer_note": (
            "Give the engineer the markdown above verbatim, on its own line. "
            "It is served on this machine's loopback interface only and lasts "
            "as long as this server runs."
        ),
    }


def _page_site():
    """The loopback page server, if this build serves pages. Else None.

    One seam, read by ``_links`` alone, so that the tools below are written
    once against both cases rather than twice. ``generate_viewer_link`` and
    ``render_diagram_view`` are complete and correct without it -- the pages
    they produce are self-contained files -- and gain a clickable link when it
    is there.

    Failure here degrades to None rather than raising, and that is the
    notebook's lesson rather than caution for its own sake: a render or a
    fabric that worked perfectly well must not come back as an error because
    a local web server would not start. The file is on disk either way, and
    ``_links`` still names it.
    """
    try:
        from capella_bundle import page_server  # noqa: PLC0415

        return page_server.site()
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Setting up, and choosing what to work in
# ---------------------------------------------------------------------------


@mcp.tool()
def setup_model() -> dict:
    """Start the setup page and return a link to give the engineer.

    Call this when no model is configured yet, or when the engineer wants to
    add one, change a branch, declare a library repository or replace a token.
    **Give them what it returns and stop there** — setting up is theirs to do,
    and that page is the only place a credential is ever entered.

    What they do on it: name this agent, add the model repositories it should
    reach, mark each read or write, name any library repositories the model
    cross-references, and supply a token. Saving clones the repository, which
    for a Capella model can take minutes — which is exactly why it happens
    there, where they can watch it, and not here where they cannot.

    When they say they are finished, call list_my_scopes() to see what they
    configured, then open_model(). Do not ask them for a repository URL or a
    token yourself.
    """
    # Imported here, not at module scope: an MCP server must not carry a web
    # application in its import graph, and the boundary test pins that.
    try:
        from capella_bundle.setup_app import serve_in_background  # noqa: PLC0415
    except ImportError:
        # Not a failure to report as one. The setup page is a separate
        # process's worth of dependencies, and a machine without them can
        # still be set up -- from a terminal, with the command below. Saying
        # so beats raising, which would leave an agent with nothing to offer.
        command = launch_command(setup=True)
        return {
            "run_this_instead": command,
            "tell_the_engineer": (
                f"Setting up needs a page this server cannot start from "
                f"inside an agent session. Open a terminal and run:\n\n"
                f"    {command}\n\n"
                f"It opens the setup page in your browser. Tell me when you "
                f"are finished and I will read what you configured."
            ),
            "then": "call list_my_scopes() once they say they are done",
        }

    # No profile name invented here. With none given the page opens on its
    # chooser, which is the only place a profile should be picked.
    url = serve_in_background(PROFILE_NAME)
    return {
        "url": url,
        "tell_the_engineer": (
            f"Open this link to set up your Capella model: {url}\n"
            f"Tell me when you are finished and I will read what you "
            f"configured."
        ),
        "then": "call list_my_scopes() once they say they are done",
    }


def _scopes_payload(profile: bsettings.Profile) -> dict:
    """What one profile holds: its models, and nothing resembling a secret.

    Shared by list_my_scopes and use_profile so that choosing a profile and
    asking what is reachable cannot drift into two different answers.
    """
    models = []
    for entry in profile.models:
        row = {
            "nickname": entry.nickname,
            "repo": entry.remote_url,
            "branch": entry.branch,
            "access": entry.access,
        }
        try:
            cfg = bsettings.to_config(profile, entry.nickname)
        except Exception as exc:
            # A model whose directories are misconfigured is reported as
            # itself with the reason, never left out of the list. An engineer
            # told "you have one model" when they have two has been
            # misinformed, which is worse than being shown something to fix.
            row["unusable"] = str(exc)
        else:
            row["prepared"] = bsession.is_prepared(cfg)
            if entry.dependencies:
                row["libraries"] = [d.resource_name for d in entry.dependencies]
            if entry.shared:
                row["shared"] = True
        models.append(row)
    return {
        "profile": profile.name,
        "agent": profile.identity.describe(),
        "models": models,
    }


def _profile_summary(name: str) -> dict:
    """Enough of a profile to recognise it by, without selecting it."""
    try:
        profile = bsettings.load_profile(name)
    except Exception as exc:
        return {"profile": name, "unreadable": str(exc)}
    return _scopes_payload(profile)


@mcp.tool()
def use_profile(name: str = "") -> dict:
    """Choose which of this machine's model profiles this session works in.

    Call it with **no argument** first: it lists every profile on this machine
    with the models each one holds, and selects nothing. Then **ask the
    engineer which one they want** and call it again with that name.

    **Never choose for them, not even when there is only one.** A profile can
    carry write access to a Capella model repository, and which one this
    session touches is theirs to say. Listing is free; selecting is a decision.

    Args:
        name: The profile to use, exactly as this tool reports it. Omit it to
            see the choices without selecting one.

    Returns the same shape as list_my_scopes once a profile is selected, plus
    `selected`. Switching is allowed until a model is open and refused
    afterwards: close the open models first if you really mean to switch.
    """
    global SELECTED_PROFILE, _PROFILE

    available = bsettings.list_profiles()
    if not available:
        raise bsettings.SettingsError(
            "No Capella model is set up on this machine yet, so there is no "
            "profile to choose. Call setup_model() to get the engineer a "
            "setup page; they add their model repositories there, and nothing "
            "has to be configured before that."
        )

    current = SELECTED_PROFILE or PROFILE_NAME
    wanted = (name or "").strip()

    if not wanted:
        return {
            "selected": current or None,
            "profiles": [_profile_summary(each) for each in available],
            "then": (
                "Ask the engineer which of these they want to work in, then "
                "call use_profile(name=...) with their answer. Do not choose "
                "for them, and do not pick one because it is the only one."
            ),
        }

    if wanted not in available:
        names = ", ".join(repr(each) for each in available)
        raise bsettings.SettingsError(
            f"No model profile called {wanted!r} is set up on this machine. "
            f"Profiles that exist: {names}. Call use_profile() with no "
            f"argument to see what each one holds. If the engineer expected a "
            f"profile that is not listed, it is added on the setup page -- "
            f"call setup_model() and give them the link."
        )

    if _OPEN and wanted != current:
        raise bsettings.SettingsError(
            f"This session has models open from the {current!r} profile, so "
            f"switching to {wanted!r} now would leave them pointing into a "
            f"profile this session is no longer using. Call close_model() for "
            f"each open session_id first, then choose again. Nothing is lost "
            f"by closing: the clone stays on disk."
        )

    SELECTED_PROFILE = wanted
    # Dropped rather than kept: the engineer may have been on the setup page
    # between this call and the last one, and a cached profile would serve
    # them the models they had before they finished adding one.
    _PROFILE = None

    payload = _scopes_payload(_profile())
    payload["selected"] = wanted
    return payload


@mcp.tool()
def list_my_scopes() -> dict:
    """The Capella models this agent can reach, and which it may write to.

    Call this first, before open_model, when you have not been told which
    model to use. Deliberately the same tool name hosted Cartenza uses, so
    instructions written for one product work in the other; it answers from
    the local profile, with no registry and no agent id involved.

    If it reports that this session has not chosen a profile yet, call
    use_profile() — that is the one thing to do about it, and it needs no
    restart and no change to how this server was launched.

    Each model has a `nickname` — that is what you pass to open_model — plus
    its repository, branch, an `access` of "read" or "write", and `prepared`,
    which says whether it has been cloned to this machine yet. A model that is
    not prepared is a setup step outstanding, not a fault in this agent.

    Nothing here is a credential, and there is nothing to authorize.
    """
    return _scopes_payload(_profile())


# ---------------------------------------------------------------------------
# Opening and closing a model
# ---------------------------------------------------------------------------


@mcp.tool()
def open_model(name: str = "") -> dict:
    """Open one of this agent's Capella models and return a session_id.

    Nothing has to be authorized: no connect code, no browser step, and no
    credential passed here. The engineer configured this agent's models on the
    setup page, and this server reads them from there.

    This does **not** clone. Cloning a Capella model can take minutes and
    belongs on the setup page where the engineer can watch it; if a model has
    not been set up, the error says so and says what to do.

    It fetches and fast-forwards, which takes seconds whatever the model's
    size. Read two fields in the result:

      synced=true   you are looking at the remote's current content.
      synced=false  it did not work (offline, credential, or the two histories
                    have diverged). The model still opens and reads correctly,
                    but may be behind the remote. `sync_error` or `sync_note`
                    says why — mention it if it bears on your answer.

    `write_refusal`, when present, is the reason a patch would be refused
    right now: a read-only access, a clone behind its remote, or a divergence.
    Read it before composing a patch rather than after.

    Args:
        name: The nickname of the model to open, as list_my_scopes reports it.
            Omit it only when this agent has exactly one model; with several,
            the error names them rather than picking one.
    """
    profile = _profile()
    cfg = bsettings.to_config(profile, name)
    result = bsession.open_model(cfg)
    # The config follows the session rather than being a module singleton, so
    # several models can be open at once -- a reference model beside the one
    # being patched. The sync half is what write_refusal is measured against.
    _OPEN[result["session_id"]] = (cfg, dict(result))
    return result


@mcp.tool()
def close_model(session_id: str) -> dict:
    """Release the in-memory session. Does **not** delete the clone.

    The local clone is left exactly where it is — it is the engineer's model,
    not a cache, and keeping it is what makes the next open instant. Deleting
    it would also destroy any committed-but-unpushed model edits. Call
    push_model_changes first if there are commits that should reach the remote;
    closing does not push.

    There is no cost to leaving a session open, so this is optional.

    Args:
        session_id: From open_model.
    """
    _OPEN.pop(session_id, None)
    return bsession.forget(session_id)


# ---------------------------------------------------------------------------
# Reading the model
# ---------------------------------------------------------------------------


@mcp.tool()
def list_object_types() -> dict:
    """Every valid phase and object_type for browse_model and search_model_objects.

    Returns a mapping of phase (OA/SA/LA/PA) to the object types that exist in
    it. No session required — call it before browsing if unsure what is there.
    """
    return svc.get_phase_types()


@mcp.tool()
def browse_model(session_id: str, phase: str, object_type: str) -> list[dict]:
    """List every object of one type within a Capella model phase.

    Call list_object_types() first for the valid combinations. object_type
    matching is case-insensitive and tolerates underscores or hyphens for
    spaces, so "functional_chain" and "Functional Chain" both work.

    The "* Package" types list the model's own package tree for that phase.
    Use them to navigate structure, and to get the parent uuid
    apply_model_patch needs when adding an element into a specific package.

    Args:
        session_id: From open_model.
        phase: One of OA, SA, LA, PA.
        object_type: A type within that phase — list_object_types() has them.
    """
    canonical = _resolve_object_type(phase, object_type)
    if canonical is None:
        raise _unknown_type(phase, object_type)
    return svc.search_by_name(_model_for(session_id), phase, canonical, "")


@mcp.tool()
def search_model_objects(session_id: str, phase: str, object_type: str,
                         name_query: str) -> list[dict]:
    """Search model objects by name — case-insensitive substring match.

    For a requirement imported from a document, `name_query` matches `name`,
    `long_name` and `prefix` alike, which matters because such a requirement
    usually has an empty `name` and carries its title in `long_name` and its
    ID in `prefix`. Search by the title or by the ID; do not conclude a
    requirement is unnamed.

    Args:
        session_id: From open_model.
        phase: One of OA, SA, LA, PA.
        object_type: A type within that phase — list_object_types() has them.
        name_query: Substring to match.
    """
    canonical = _resolve_object_type(phase, object_type)
    if canonical is None:
        raise _unknown_type(phase, object_type)
    return svc.search_by_name(_model_for(session_id), phase, canonical,
                              name_query)


@mcp.tool()
def resolve_model_uuids(session_id: str, uuids: list[str]) -> dict:
    """Resolve UUIDs to model objects and record them for fabric generation.

    The resolved set is what generate_fabric works from, so call this (or
    browse to collect uuids and then this) before generating.

    Args:
        session_id: From open_model.
        uuids: Capella object UUIDs.
    """
    record = _record(session_id)
    model = svc.open_model(Path(record["aird_path"]),
                           resources=record.get("resources") or None)
    resolved, not_found = svc.resolve_uuids(model, uuids)
    record["resolved_uuids"] = [row["uuid"] for row in resolved]
    svc.save_session(session_id, record)
    return {"resolved": resolved, "not_found": not_found}


@mcp.tool()
def verify_model(session_id: str, phase: str) -> dict:
    """Scan one model phase for common quality issues.

    Checks for elements with missing or empty names, and for functions not
    allocated to any component (SA/LA/PA). Findings come back grouped by
    category; an empty findings dict means none were found.

    Worth calling after apply_model_patch and before push_model_changes.

    Args:
        session_id: From open_model.
        phase: One of OA, SA, LA, PA.
    """
    return svc.verify_phase(_record(session_id), phase)


@mcp.tool()
def generate_fabric(session_id: str) -> dict:
    """Generate the YAML fabric for this session's resolved UUIDs.

    Call resolve_model_uuids first to populate the selection. The YAML comes
    back inline and is also written beside the model, so generate_viewer_link
    can hand the engineer the file.

    `traceability_artifacts` counts published requirements folded in from a
    <model>.traceability file beside the .aird, when the repository carries
    one. Those answer "which requirements bear on what I am looking at" — it
    is not a coverage list, and 0 means either no traceability file or none
    linked to this selection, not that the model has no requirements.

    Args:
        session_id: From open_model.
    """
    cfg, _sync = _opened(session_id)
    record = _record(session_id)
    yaml_path, object_count, artifact_count = svc.generate_fabric(record)
    content = yaml_path.read_text(encoding="utf-8")
    published = _publish(cfg, yaml_path, move=False)
    return {
        "yaml_content": content,
        "object_count": object_count,
        "traceability_artifacts": artifact_count,
        "filename": yaml_path.name,
        **_links(cfg, published),
    }


@mcp.tool()
def render_diagram_view(session_id: str, target: str, aspect: str = "",
                        include_html: bool = False) -> dict:
    """Render one view of the model as a self-contained HTML page to hand over.

    Use this when the engineer asks to SEE something rather than be told about
    it. `target` is a uuid or a name: it resolves to an existing diagram, or
    to a model element whose view is GENERATED around it — which works even
    when no authored diagram shows that element well, the usual case for a
    single component someone is asking about.

    **Hand over the link, not the page.** Call generate_viewer_link (or read
    `viewer_markdown` here) and give the engineer that, on its own line. The
    page is 2KB–110KB; a 44KB one relayed through a conversation could not be
    passed into another tool call and the diagram ended up re-described in
    prose rather than shown. `include_html=True` returns the body for the rare
    case where you must have the bytes — check `bytes` first.

    A pasted diagram image is a **selector, not content**: read its title off
    the image and render that diagram from the model. The rendered page
    carries full resolution, an element table with uuids and the model commit,
    none of which a screenshot has.

    `layout` says "stored" for an authored diagram, drawn from the positions
    saved in the model, or "computed" for a generated view.
    `aspects_available` lists the aspects this element offers — typically a
    context view and a realization view across layers — so offer the choice
    rather than guessing. Some elements have none at all (a FunctionalExchange
    has no generated view) and the error says so.

    Args:
        session_id: From open_model.
        target: uuid or name of a model element, or of an existing diagram.
        aspect: Which view, when the element offers several; omit for its default.
        include_html: Return the page body too; prefer the link.
    """
    cfg, _sync = _opened(session_id)
    record = _record(session_id)
    # Rendering is serialized inside capella_service, which flips
    # capellambse's SNAPPING and svgwrite's debug flag process-globally. One
    # process per session means fewer concurrent renders, not none -- a
    # harness can issue parallel tool calls -- so going through the service's
    # own entry point is what keeps that lock in force here.
    result = svc.render_diagram_view(record, target, aspect=aspect,
                                     commit=_head_commit(cfg))
    rendered = Path(result.pop("path"))
    published = _publish(cfg, rendered, move=True)
    if include_html:
        result["html"] = published.read_text(encoding="utf-8")
    result.update(_links(cfg, published))
    return result


def _head_commit(cfg: ModelConfig) -> str:
    """The commit this clone is on, which keys a rendered view's name.

    Best-effort: an odd checkout returns "" rather than failing a render that
    would otherwise have succeeded. ``capella_service`` then reports
    ``artifact_name`` as None, which is its own documented "nothing to key
    on" case.
    """
    try:
        from mcp_desktop import gitwork  # noqa: PLC0415

        return str(gitwork.head_summary(cfg.clone_dir).get("commit") or "")
    except Exception:
        return ""


@mcp.tool()
def generate_viewer_link(session_id: str, filename: str = "") -> dict:
    """A link to hand the engineer for a rendered view or a generated fabric.

    Call it after render_diagram_view or generate_fabric and print the
    returned `viewer_markdown` verbatim, on its own line. Do **not** hand over
    the filesystem path instead: a path is not a link, a chat window will not
    open one, and the engineer is left copying it into a browser by hand.

    Args:
        session_id: From open_model.
        filename: The file to link to, as render_diagram_view or
            generate_fabric reported it. Omit it to list what this model has
            produced, newest first.
    """
    cfg, _sync = _opened(session_id)
    wanted = (filename or "").strip()

    if not wanted:
        produced = sorted(
            (p for p in cfg.views_dir.glob("*") if p.is_file()),
            key=lambda p: p.stat().st_mtime, reverse=True,
        ) if cfg.views_dir.is_dir() else []
        return {
            "model": cfg.name,
            "produced": [p.name for p in produced],
            "then": ("Call generate_viewer_link(filename=...) for one of "
                     "these, or render_diagram_view to make a new one."
                     if produced else
                     "Nothing has been rendered or generated for this model "
                     "yet. Call render_diagram_view or generate_fabric "
                     "first."),
        }

    # Name only, never a path: a caller passing "../../unpacked/.git/config"
    # must not reach outside the views directory, and the views directory is
    # outside the model directory precisely because the clone's .git/config
    # holds this model's token.
    if Path(wanted).name != wanted:
        raise bsession.ModelError(
            f"{wanted!r} is a path, not a file name. Pass the `filename` that "
            f"render_diagram_view or generate_fabric reported."
        )

    target = cfg.views_dir / wanted
    if not target.is_file():
        raise bsession.ModelError(
            f"There is no {wanted!r} among the files rendered for "
            f"{cfg.name!r}. Call generate_viewer_link() with no filename to "
            f"see what this model has produced."
        )
    return {"model": cfg.name, "filename": wanted, **_links(cfg, target)}


# ---------------------------------------------------------------------------
# Writing to the model
# ---------------------------------------------------------------------------


@mcp.tool()
def apply_model_patch(session_id: str, patch_yaml: str, commit_message: str,
                      author_name: str = "", author_email: str = "") -> dict:
    """Apply a declarative YAML patch to the model, save, and commit locally.

    **The commit stays on this machine until push_model_changes.** Do not tell
    the engineer the change is saved anywhere but their own computer before
    that call succeeds.

    Uses py-capellambse's `decl.apply()` format. Target existing elements with
    `!uuid <uuid>`; `set:` updates attributes, `extend:` adds children, and
    `promise_id:` / `!promise` forward-reference within the same patch.

    `_type` is auto-injected in three cases, so it can be omitted:

      - Function/activity children of `extend: functions:` /
        `owned_functions:` / `activities:` / `owned_activities:` get
        OperationalActivity (OA), SystemFunction (SA), LogicalFunction (LA) or
        PhysicalFunction (PA) from the parent's phase.
      - Component children of `extend: components:` (SA, LA) or
        `extend: owned_components:` (PA) get the matching component type.
        **At PA always use `owned_components:`** — `components:` is a
        deprecated, non-model-coupled property on current capellambse and
        extending it raises "not model-coupled".
      - Property values under `extend: property_value_groups:` get
        String/Float/Integer/BooleanPropertyValue from the Python value's
        type, and the group itself is automatically applied to the parent in
        the same patch — no follow-up patch needed.

    An explicit `_type` is always respected; injection only fills in absences.

    OA interactions between operational activities can be created with the
    `exchange:` form, and the owning activity is computed for you as the
    endpoints' lowest common ancestor, which is where Capella files an
    interaction:

        - exchange:
            name: Charger State
            description: The charger's current state, each second
            source: !uuid <source-activity-uuid>
            target: !uuid <target-activity-uuid>

    Endpoints must be `!uuid`; an activity created in the same patch is not
    supported, so create activities in one patch and wire them in the next.

    **Refused rather than silently written as invalid XML:**
    `extend: component_exchanges:` and `extend: physical_links:` always, and
    `extend: exchanges:` whose parent is not an OperationalActivity — those
    connect ports this tool does not create or validate. Draw them in the
    Capella desktop editor. Renaming or retagging an existing exchange or link
    via `set: name:` works fine throughout. Child PhysicalFunctions under a
    PhysicalFunction cannot be created at all: capellambse 0.8.1 defines no
    owned-child-function attribute on PhysicalFunction, which is an upstream
    metamodel defect and reported as one.

    Args:
        session_id: From open_model.
        patch_yaml: A declarative YAML document (a list of patch entries).
        commit_message: What this change is, for the git history.
        author_name: Git author name. Defaults to this server's own identity.
        author_email: Git author e-mail.
    """
    cfg, sync = _opened(session_id)
    refusal = cfg.write_refusal(sync)
    if refusal:
        raise bsession.ModelError(refusal)

    record = _record(session_id)
    result = svc.apply_patch(record, patch_yaml)
    if result.get("status") != "ok":
        return result
    commit = git_svc.commit_changes(session_id, commit_message,
                                    author_name, author_email)
    return {
        "patch": result,
        "commit": commit,
        "pushed": False,
        "next": ("This is committed on this machine only. Call verify_model "
                 "to check the phase, then push_model_changes when the "
                 "engineer wants it on the remote."),
    }


@mcp.tool()
def push_model_changes(session_id: str) -> dict:
    """Push this model's committed changes to its remote repository.

    Call it when a piece of work is finished — nothing reaches the remote
    before this. The credential is re-applied from the engineer's credential
    store first, so rotating a token on the setup page is all they have to do.

    Args:
        session_id: From open_model.
    """
    cfg, sync = _opened(session_id)
    refusal = cfg.write_refusal(sync)
    if refusal:
        raise bsession.ModelError(refusal)
    _record(session_id)               # the readable unknown-session error
    bsession.refresh_credential(cfg)
    try:
        return git_svc.push_changes(session_id)
    except Exception as exc:
        raise bsession.ModelError(_scrubbed(str(exc), cfg)) from None


@mcp.tool()
def pull_model_changes(session_id: str,
                       discard_local_changes: bool = False) -> dict:
    """Take changes someone else pushed, or reset this clone to the remote.

    open_model already fetches and fast-forwards, so most of the time this is
    not needed. It is here for the one thing open_model deliberately will not
    do: `discard_local_changes=True` resets this clone to the remote and lists
    the commits it dropped, so they can be reapplied.

    **Never set that without telling the engineer exactly what will be
    discarded** — list the commits from a plain call first, which changes
    nothing. Fast-forward only otherwise; a Capella model is never merged
    automatically, because git merges it textually and a merge that reports
    success can still leave a model Capella will not open.

    After a pull, element uuids may have changed: re-browse before patching
    rather than reusing uuids from before it.

    Args:
        session_id: From open_model.
        discard_local_changes: Reset to the remote, dropping this clone's
            unpushed commits. Only when the engineer has agreed to lose them.
    """
    cfg, _sync = _opened(session_id)
    record = _record(session_id)
    bsession.refresh_credential(cfg)
    try:
        result = git_svc.pull_changes(
            session_id, discard_local_changes=discard_local_changes)
    except Exception as exc:
        raise bsession.ModelError(_scrubbed(str(exc), cfg)) from None

    if result.get("status") == "pulled":
        # A pull can rename or move the model file; keep the session pointing
        # at it, and re-measure the ceiling, which was taken at open and is
        # now out of date in this clone's favour.
        found = bsession.find_aird(cfg.clone_dir)
        if found is None:
            result["warning"] = ("No .aird model file is in the clone after "
                                 "pulling. Re-verify this model on the setup "
                                 "page.")
        elif str(found) != record.get("aird_path"):
            record["aird_path"] = str(found)
            record["resolved_uuids"] = []
            svc.save_session(session_id, record)
            result["warning"] = (f"The model file moved to {found.name}; this "
                                 f"session now points at it, and the resolved "
                                 f"uuid selection was cleared.")
        fresh = bsession.fast_forward(cfg)
        _OPEN[session_id] = (cfg, fresh)
        result.update({k: v for k, v in fresh.items()
                       if k in ("synced", "behind", "ahead", "diverged",
                                "sync_note", "sync_error", "sync_detail")})
    return result


def _scrubbed(text: str, cfg: ModelConfig) -> str:
    """An error message with this model's credential removed.

    Two passes, because a token reaches a git error two ways: embedded in the
    remote URL that git echoes back, and as itself.
    """
    cleaned = re.sub(r"(https?://)[^/@\s]*@", r"\1", text)
    if cfg.pat:
        cleaned = cleaned.replace(cfg.pat, "***")
    return cleaned


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def launch_command(setup: bool = False, profile: str = "") -> str:
    """The command line that starts this server, for the setup page to print.

    The full interpreter path, not a bare ``python``: on a Windows machine
    with no Python from python.org on PATH, ``python`` is the Microsoft Store
    stub, which answers "Python was not found" and exits 49. A harness started
    with that command reports a transport error, which reads like a broken
    install and is not.
    """
    parts = [f'"{sys.executable}"',
             f'"{Path(__file__).resolve().parent / "run_capella_fabric.py"}"']
    if setup:
        parts.append("--setup")
    name = (profile or PROFILE_NAME).strip()
    if name:
        parts.append(f'--profile "{name}"')
    return " ".join(parts)


def _profile_argument(argv: list[str]) -> str:
    """``--profile NAME`` or ``--profile=NAME``, else empty.

    Deliberately the only launch argument: what the engineer stops typing is
    the repository, the branch, the access level, the identity and the token.
    A profile name is neither secret nor structural, and a stdio server
    started by a harness has no other way to know which agent it is serving.
    """
    for index, arg in enumerate(argv):
        if arg == "--profile" and index + 1 < len(argv):
            return argv[index + 1].strip()
        if arg.startswith("--profile="):
            return arg.split("=", 1)[1].strip()
    return ""


def main(argv: Optional[list[str]] = None) -> int:
    """Start the server, or the setup page.

        run_capella_fabric.py [--profile NAME]
            The MCP server itself, over stdio. This is what a harness runs,
            and the setup page prints it in full.

        run_capella_fabric.py --setup [--profile NAME]
            The local setup page in a browser: this agent's identity, its
            models, their credentials, and the command line above.
    """
    global PROFILE_NAME

    # Checked before the server starts, rather than when the first tool runs.
    # git is a PATH requirement with no bundled substitute, so without it this
    # server would accept a connection and fail every call -- which an
    # engineer experiences as a broken product rather than a missing
    # dependency.
    try:
        require_git()
    except ConfigError as exc:
        print(f"Capella Fabric cannot start: {exc}", file=sys.stderr)
        return 1

    args = sys.argv[1:] if argv is None else argv
    PROFILE_NAME = _profile_argument(args)

    if "--setup" in args:
        # Imported here rather than at module scope, so that starting the MCP
        # server never pulls the web application into its import graph.
        try:
            from capella_bundle.setup_app import serve  # noqa: PLC0415
        except ImportError as exc:
            print(f"Capella Fabric has no setup page in this build: {exc}",
                  file=sys.stderr)
            return 1
        return serve(PROFILE_NAME, open_browser="--no-browser" not in args)

    # The profile is deliberately NOT loaded or required here. A client that
    # connects should see the tool list and get a readable error from a tool,
    # rather than a process that exits before saying anything -- a server that
    # dies at start-up gives the harness nothing to show but a transport
    # failure.
    banner = "Capella Fabric — stdio"
    if PROFILE_NAME:
        banner += f" — profile {PROFILE_NAME}"
    print(banner, file=sys.stderr, flush=True)
    mcp.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
