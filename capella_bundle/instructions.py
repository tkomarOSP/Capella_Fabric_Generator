# Copyright 2024-2026 Open Sun Power, LLC
# SPDX-License-Identifier: Apache-2.0
"""What the local Capella Fabric tells a client about itself at connect time.

**Written from scratch rather than trimmed.** The hosted server's
``instructions`` is ~180 lines and the majority of it is false here: connect
codes and their two expiry clocks, ``begin_connect``, "Connect via GitHub",
``/onboarding/agents``, signed four-hour view links, ``add_dependency_repo``,
sessions swept after four hours, and promoting a view through
``workspace-manager`` to ``cartenza.ai``. None of that exists locally. Adapting
it would have meant deleting more than it kept, and the deletions are the
dangerous part: a paragraph about an authorization step that is not there sends
an agent looking for a tool that was never registered.

What *is* carried over is the part that is about the model rather than about
the hosting: that a requirement imported from a document usually has an empty
``name``, that actors are components flagged ``is_actor``, and that a diagram's
``layer`` is not its ``viewpoint``. Those three cost real investigations, they
are true in every deployment, and each one changes a conclusion an agent would
otherwise draw confidently and wrongly.

The declarative-patch rules are **not** here. They are long, they apply to
exactly one tool, and ``apply_model_patch``'s own docstring is where an agent
reads them at the moment it needs them. The hosted server carries them in both
places; there is no reason to pay for them twice in a budget this tight.

Kept short on purpose. An MCP client freezes this text at connect and carries
every sentence for the whole session whether it is useful or not -- the same
freezing that makes a client's tool list a snapshot taken at connect. A
sentence that is wrong is worse than one that is missing, because an agent
acting on it cannot discover the error from in here.
"""

INSTRUCTIONS = """\
Capella Fabric, running locally: browse, search, verify, render and patch \
MBSE models in the engineer's own Capella git repositories, on this machine.

START BY CALLING list_my_scopes(). It tells you which models are open to this \
session: each with a nickname, its repository and branch, and an access of \
"read" or "write". Then call open_model(name="<nickname>") for the one you \
need; it returns a session_id that every other tool wants. With exactly one \
model configured, open_model() needs no argument.

IF IT SAYS NO PROFILE HAS BEEN CHOSEN, that is normal and it is yours to \
resolve in one exchange, with no restart and no configuration. A profile is a \
named set of models on this machine. Call use_profile() with no argument to \
see what each one holds, ASK THE ENGINEER WHICH ONE THEY WANT TO WORK IN, and \
call use_profile(name="<their answer>"). Never choose for them, not even when \
there is only one: a profile can carry write access to a model repository, \
and which one this session touches is theirs to say.

IF NOTHING IS SET UP AT ALL, both tools will say so -- there are no profiles, \
not merely none chosen. Call setup_model(), give the engineer the link it \
returns, and stop there. They name this agent, add their model repositories \
and any library repositories those depend on, and that page is the only place \
a token is ever entered. When they say they are finished, call \
list_my_scopes() again. Never ask them for a repository URL or an access \
token yourself.

No credential is ever passed to these tools, and none is ever returned by \
one. There is nothing to authorize, no connect code, and no browser step \
beyond the setup page itself.

THE CLONE IS PERMANENT AND open_model NEVER CLONES. Cloning a Capella model \
can take minutes and belongs on the setup page where the engineer can watch \
it. open_model fetches and fast-forwards an existing clone, which takes \
seconds, and tells you in `synced` whether that worked. If `synced` is false \
the model still opens and reads correctly, but you may be looking at content \
that is behind the remote -- say so if it bears on the answer. Working \
offline is expected and is not an error.

If a session_id stops being recognised, this server was restarted: call \
open_model again. It costs nothing and loses nothing, because the clone is \
already on disk.

READING THE MODEL. Call list_object_types() for the valid phase and \
object_type combinations; matching is case-insensitive. The "* Package" types \
list the model's own package tree for a phase, which is how you navigate \
structure and how you get the parent uuid a patch needs. Three things that \
look like model defects and are not:

  - A requirement imported from a document usually has an EMPTY `name`, with \
its title in `long_name` and its ID in `prefix` (REQ-000004/A). Search by the \
title or the ID -- name_query matches all three -- and never report such a \
requirement as unnamed. Its text arrives as HTML-escaped rich text, so \
unescape and strip tags to read the shall statement.
  - Actors are components (entities at OA) flagged `is_actor: true`. \
capellambse has no separate actor class, so an actor and a behaviour \
component of the same name are a deliberate pattern, not a duplicate. Browse \
object_type='Actor' for actors alone.
  - A diagram's `viewpoint` is the Sirius viewpoint and groups diagram KINDS, \
not Arcadia layers: functional-chain, sequence, state-machine and class \
diagrams all report 'Common' whichever layer they were drawn in. Read the \
`layer` field, which carries the real one.

SHOWING SOMETHING. When the engineer asks to SEE rather than be told, call \
render_diagram_view. It takes a uuid or a name and renders one self-contained \
HTML page: an existing diagram drawn from the layout saved in the model, or a \
view GENERATED around an element, which works when no authored diagram shows \
that element well. `aspects_available` lists what an element offers, so offer \
the choice rather than guessing; some elements have none and the error says \
so. Then call generate_viewer_link and give the engineer its `viewer_markdown` \
verbatim, on its own line. Do NOT hand over the filesystem path instead -- a \
path is not a link and a chat window will not open one. Never relay the page \
body: it is 2KB-110KB, and a 44KB page relayed once could not be passed into \
another tool call and ended up re-described in prose rather than shown.

GENERATING A FABRIC. resolve_model_uuids first, or browse to find them, then \
generate_fabric. It returns the YAML inline and also writes it beside the \
model, so generate_viewer_link can hand over a link to the file. A fabric \
folds in published requirements automatically when the repository carries a \
<model>.traceability file beside the .aird -- reported as \
`traceability_artifacts`. That answers "which requirements bear on what I am \
looking at"; it is NOT a coverage list, and a count of 0 means either no \
traceability file or none linked to this selection, not that the model has no \
requirements.

WRITING TO A MODEL. apply_model_patch applies a declarative YAML patch and \
commits it locally; its own docstring carries the patch rules and the shapes \
that are refused. Call verify_model afterwards, then push_model_changes when \
the engineer wants the change to reach the remote. NOTHING REACHES THE REMOTE \
UNTIL push_model_changes -- do not tell them the work is saved anywhere but \
their own machine before that.

A write can be refused for three reasons, and all three are stated with the \
model rather than discovered at push time. The model's access may be "read", \
which is the engineer's declaration and a setting on the setup page. Or this \
clone may be BEHIND the remote, in which case a patch would be built on model \
content somebody has already replaced: the push is then rejected as a \
non-fast-forward, after you have written the patch and told the engineer it \
was applied, and resolving that rejection means reconciling a divergence this \
server will not reconcile. Call open_model again to fetch, then patch. Or the \
two histories may have DIVERGED already, with commits on both sides.

A DIVERGENCE IS NOT YOURS TO RESOLVE. This server does not replay commits \
across one, deliberately: a Capella model is one file and git merges it \
textually, so a rebase of model XML can succeed with no conflict at all and \
still produce a model Capella will not open. Nothing is changed and every \
commit is still in git. Say which model diverged and what the report said, \
and leave the decision with the engineer -- the clone opens in Capella, which \
is the only tool that can judge it. Do not reach for git yourself: you have \
no git tools, and a shell is not one. pull_model_changes with \
discard_local_changes=True takes the remote's version and lists what it \
dropped, so it can be reapplied -- never set it without saying first exactly \
what will be discarded.

If a model depends on library repositories, they are declared and cloned on \
the setup page, not here. open_model reports any that are missing by name, \
because cross-references into a library that is not on disk fail in a way \
that reads as a fault in the main model.

There are no connect codes, signed links, workspaces, project artifacts, \
routines or teams here -- those belong to the hosted product. This server is \
one engineer, their own repositories, and their own machine.

(c) Open Sun Power, LLC -- Apache 2.0.
"""
