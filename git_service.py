# Copyright 2024-2026 Open Sun Power, LLC
# SPDX-License-Identifier: Apache-2.0
# git_service.py — clone a GitHub repo into a Capella Fabric Generator session directory.
# Replaces save_upload() + unpack_archive() for the MCP server workflow.

import git
from pathlib import Path
import capella_service as svc


def clone_repo(repo_url: str, pat: str, session_id: str, branch: str = "") -> None:
    """Clone a GitHub repo using a PAT into <session>/unpacked/.

    The PAT is injected into the HTTPS URL at call time and never written
    to disk or exposed in log messages.
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
