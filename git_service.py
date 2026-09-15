# Copyright 2024-2026 Open Sun Power, LLC
# SPDX-License-Identifier: Apache-2.0
# git_service.py — clone a GitHub repo into a Capella Fabric Generator session directory.
# Replaces save_upload() + unpack_archive() for the MCP server workflow.

import git
import re
from pathlib import Path
import capella_service as svc


def clone_repo(repo_url: str, pat: str, session_id: str, branch: str = "") -> None:
    """Clone a GitHub repo using a PAT into <session>/unpacked/.

    The PAT is scrubbed from any error message raised here, but note that
    git.Repo.clone_from persists the authenticated URL as the clone's `origin`
    remote -- so the credential DOES land on disk, in <dest>/.git/config, for
    the life of the session directory. That is in fact what makes
    push_model_changes work with no credential of its own. Session dirs are
    removed by cleanup_session (cousin_back_log/note-0086).
    """
    clone_dir = svc._session_dir(session_id) / 'unpacked'
    clone_dir.mkdir(exist_ok=True)
    _clone(repo_url, pat, clone_dir, branch)


def clone_to_dir(repo_url: str, pat: str, target_dir: Path, branch: str = "") -> None:
    """Clone a repo to an arbitrary directory (used for dependency libraries)."""
    target_dir.mkdir(parents=True, exist_ok=True)
    _clone(repo_url, pat, target_dir, branch)


def _clone(repo_url: str, pat: str, dest: Path, branch: str) -> None:
    auth_url = _inject_pat(repo_url, pat)
    kwargs = {'branch': branch} if branch else {}
    try:
        git.Repo.clone_from(auth_url, str(dest), **kwargs)
    except git.GitCommandError as exc:
        raise RuntimeError(_scrub_pat(str(exc), pat)) from None


def _inject_pat(url: str, pat: str) -> str:
    """Return the URL with oauth2:<pat>@ injected after the scheme."""
    if '://' in url:
        scheme, rest = url.split('://', 1)
        return f"{scheme}://oauth2:{pat}@{rest}"
    return f"https://oauth2:{pat}@{url}"


def _scrub_pat(msg: str, pat: str) -> str:
    """Remove the raw PAT from an error string before it can be logged."""
    return msg.replace(pat, '***') if pat else msg


def commit_changes(
    session_id: str,
    message: str,
    author_name: str = "",
    author_email: str = "",
) -> dict:
    """Stage all modified files and commit in the session clone (unpacked/)."""
    repo_dir = svc._session_dir(session_id) / 'unpacked'
    repo = git.Repo(str(repo_dir))
    repo.git.add(A=True)
    if not repo.is_dirty(index=True):
        return {"status": "no_changes"}
    author = git.Actor(author_name, author_email) if author_name else None
    kwargs = {"author": author} if author else {}
    repo.index.commit(message, **kwargs)
    return {"status": "committed", "sha": repo.head.commit.hexsha[:8]}


# PushInfo.flags bits worth naming in an error message, most specific first.
# GitPython sets several at once (REJECTED almost always arrives with ERROR),
# so these are reported as a set rather than a single cause.
_PUSH_FLAG_NAMES = (
    ('REJECTED',        'rejected'),
    ('REMOTE_REJECTED', 'rejected by the remote'),
    ('REMOTE_FAILURE',  'remote failure'),
    ('ERROR',           'error'),
)


def _describe_push(pi) -> str:
    """Render one PushInfo as something a caller can act on.

    git.remote.PushInfo defines no __str__, so str(pi) yields
    '<git.remote.PushInfo object at 0x...>' -- a raw repr that reached real
    users as the entire error message and cost them two extra re-clones just to
    find out whether a push had landed (After_Treatment_System_Notebook/
    Fabric_MCP_Issues OBS-0002). Everything useful is in .summary and .flags.
    """
    flags = getattr(pi, 'flags', 0) or 0
    reasons = [label for name, label in _PUSH_FLAG_NAMES
               if flags & getattr(type(pi), name, 0)]
    summary = (getattr(pi, 'summary', '') or '').strip()
    ref = getattr(pi, 'remote_ref_string', '') or ''

    parts = []
    if ref:
        parts.append(ref)
    if reasons:
        parts.append(', '.join(reasons))
    if summary:
        parts.append(summary)
    detail = ' — '.join(parts) if parts else 'push failed for an unreported reason'

    # By far the most common real cause, and the one whose remedy isn't
    # obvious from git's own wording.
    if 'non-fast-forward' in summary.lower() or 'fetch first' in summary.lower():
        detail += (" — the remote has commits this session doesn't have, so this "
                   "session and the remote have diverged. Call pull_model_changes to see "
                   "what changed on each side. To take the remote's version without "
                   "re-authorizing, call pull_model_changes(discard_local_changes=True) "
                   "and reapply your change on top.")
    return detail


def repoint_origin(session_id: str, pat: str) -> None:
    """Re-point origin at a freshly issued credential.

    clone_repo bakes the credential into origin's URL, so a session that
    outlives it -- GitHub's OAuth access tokens last 8 hours -- pushes with a
    dead one and fails after the model edits are already committed locally
    (cousin_back_log/note-0093). Any credential already in the URL is stripped
    first so this stays idempotent across repeated pushes.
    """
    repo = git.Repo(str(svc._session_dir(session_id) / 'unpacked'))
    origin = repo.remote('origin')
    origin.set_url(_inject_pat(_strip_credential(origin.url), pat))


def _strip_credential(url: str) -> str:
    """The bare remote URL, with any embedded credential removed.

    Control characters are stripped too, and that is not defensive noise. The
    first version of repoint_origin used a broken replacement template and
    substituted a literal 0x01 byte where the scheme should have gone. Because
    this runs on a URL this same function previously wrote, the damage
    accumulated one byte per push -- git reported https://?github.com/...,
    then https://??github.com/... -- which made retrying actively harmful
    rather than merely useless (Fabric_MCP_Issues). A session cloned before
    the fix still carries those bytes in .git/config, so stripping them here
    is what lets it recover on the next push instead of needing a re-clone.
    """
    url = re.sub(r"[\x00-\x1f\x7f]", "", url)
    return re.sub(r"^(https?://)[^/@]*@", r"\1", url)


def _commit_summary(commit) -> dict:
    return {"sha": commit.hexsha[:8], "author": commit.author.name, "message": commit.summary}


def pull_changes(session_id: str, discard_local_changes: bool = False) -> dict:
    """Bring the session's clone up to date with the remote -- fast-forward only.

    Before this existed, the only way to see a change someone pushed was a brand
    new clone, which on the credential-free path means a brand new browser
    authorization. For two people taking turns on one model, that was an
    authorization per turn (Fabric_MCP_Issues/OBS-0003, OBS-0005).

    Never merges. Capella's .capella/.aird files are large XML, and an automatic
    merge can produce a model that loads but is subtly wrong. So when both the
    remote and this session have new commits, this reports the divergence and
    changes nothing, unless discard_local_changes is explicitly set -- in which
    case the session is reset to the remote and the discarded commits are named,
    so the caller can reapply them.

    Every patch is committed as it's applied, so there is never uncommitted work
    in a session to protect: "local changes" means unpushed commits.
    """
    repo = git.Repo(str(svc._session_dir(session_id) / 'unpacked'))
    try:
        branch = repo.active_branch.name
    except TypeError:
        return {"status": "error",
                "message": "This session's clone isn't on a branch, so there is nothing to pull into."}

    repo.remote('origin').fetch()
    upstream = f"origin/{branch}"
    try:
        repo.commit(upstream)
    except (git.BadName, ValueError):
        return {"status": "error",
                "message": f"The remote has no branch '{branch}' -- it may have been renamed or deleted."}

    incoming = [_commit_summary(c) for c in repo.iter_commits(f"HEAD..{upstream}")]
    local = [_commit_summary(c) for c in repo.iter_commits(f"{upstream}..HEAD")]

    if not incoming:
        message = "Already up to date — the remote has nothing this session doesn't."
        if local:
            message += f" This session has {len(local)} commit(s) not yet pushed."
        return {"status": "up_to_date", "incoming": [], "local": local, "message": message}

    if local and not discard_local_changes:
        return {
            "status": "diverged",
            "incoming": incoming,
            "local": local,
            "message": (
                f"Nothing was changed. The remote has {len(incoming)} commit(s) this session "
                f"doesn't, and this session has {len(local)} not yet pushed -- both sides have "
                "moved, and model files aren't merged automatically. This session's commits are "
                "still safe here. To take the remote's version, call pull_model_changes("
                "discard_local_changes=True) and reapply your change on top of it; that needs no "
                "new authorization."
            ),
        }

    if local:  # discard_local_changes explicitly requested
        repo.git.reset('--hard', upstream)
        return {
            "status": "pulled",
            "incoming": incoming,
            "discarded": local,
            "message": (
                f"Reset to the remote: {len(incoming)} commit(s) taken, and this session's "
                f"{len(local)} unpushed commit(s) discarded as requested. Reapply those changes "
                "if they're still wanted. Element UUIDs may have changed -- re-browse before "
                "patching."
            ),
        }

    repo.git.merge('--ff-only', upstream)
    return {
        "status": "pulled",
        "incoming": incoming,
        "message": (
            f"Pulled {len(incoming)} commit(s). Element UUIDs may have changed -- re-browse "
            "before patching rather than reusing UUIDs from before the pull."
        ),
    }


def push_changes(session_id: str) -> dict:
    """Push committed changes to remote origin."""
    repo_dir = svc._session_dir(session_id) / 'unpacked'
    repo = git.Repo(str(repo_dir))
    push_info = repo.remote('origin').push()

    if not push_info:
        # No PushInfo at all -- nothing was transmitted. Previously this fell
        # through to push_info[0] and raised IndexError, surfacing as a generic
        # exception with no indication of what went wrong.
        return {"status": "error",
                "message": "The remote reported nothing for this push. Check that the "
                           "session's branch has commits and that origin is reachable."}

    failed = [pi for pi in push_info
              if pi.flags & (pi.ERROR | pi.REJECTED | pi.REMOTE_REJECTED | pi.REMOTE_FAILURE)]
    if failed:
        return {"status": "error",
                "message": "; ".join(_describe_push(pi) for pi in failed)}

    first = push_info[0]
    result = {"status": "ok", "ref": str(first.remote_ref_string)}
    if first.flags & first.UP_TO_DATE:
        # Distinct from a real push: nothing was sent. Callers were previously
        # unable to tell this apart from "changes pushed", which is half of why
        # re-clone-to-verify became a habit.
        result["message"] = "Already up to date — the remote already has these commits; nothing was pushed."
    else:
        result["message"] = f"Pushed to {first.remote_ref_string}."
    return result
