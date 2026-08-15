"""Fetch a GitHub pull request's changed files and turn them into IR ranges."""

from __future__ import annotations

import os
import re
from pathlib import Path

import requests

from repograph.auth.github import GITHUB_API, cached_token
from repograph.config import Config
from repograph.query.diff import FileDiff, parse_unified_diff, ranges_from_file_diffs, remote_identity

_PR_URL_RE = re.compile(
    r"github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+)/pull/(?P<number>\d+)"
)
_PR_SHORTHAND_RE = re.compile(r"^(?P<owner>[^/]+)/(?P<repo>[^/#]+)[#/](?P<number>\d+)$")


class PRError(RuntimeError):
    pass


def parse_pr_spec(spec: str, repo_root: Path | None = None) -> tuple[str, str, int]:
    """Accept 42, owner/repo#42, owner/repo/42, or a github.com pull URL."""
    spec = spec.strip()
    if spec.isdigit():
        identity = remote_identity(repo_root) if repo_root else None
        if identity is None:
            raise PRError(
                "PR number alone needs a GitHub remote: run this inside the repo, "
                "or pass --pr owner/repo#42"
            )
        return identity[0], identity[1], int(spec)
    if m := _PR_URL_RE.search(spec):
        return m.group("owner"), m.group("repo"), int(m.group("number"))
    if m := _PR_SHORTHAND_RE.match(spec):
        return m.group("owner"), m.group("repo"), int(m.group("number"))
    raise PRError(f"could not parse PR spec {spec!r}; use 42, owner/repo#42, or a pull URL")


def github_token(cfg: Config | None = None) -> str:
    if cfg and cfg.github.token:
        return cfg.github.token
    env = os.environ.get("GITHUB_TOKEN") or os.environ.get("REPOGRAPH_TOKEN") or ""
    if env:
        return env
    return cached_token() or ""


def fetch_pr(owner: str, repo: str, number: int, token: str = "") -> dict:
    data = _get(f"{GITHUB_API}/repos/{owner}/{repo}/pulls/{number}", token)
    return {
        "number": data["number"],
        "title": data.get("title") or "",
        "html_url": data.get("html_url") or "",
        "base": (data.get("base") or {}).get("ref") or "",
        "head": (data.get("head") or {}).get("ref") or "",
        "full_name": f"{owner}/{repo}",
        "repo": repo,
    }


def fetch_pr_file_diffs(owner: str, repo: str, number: int, token: str = "") -> list[FileDiff]:
    files = _get_paginated(f"{GITHUB_API}/repos/{owner}/{repo}/pulls/{number}/files", token)
    diffs: list[FileDiff] = []
    for f in files:
        path = f.get("filename") or ""
        previous = f.get("previous_filename")
        status = f.get("status") or "modified"
        patch = f.get("patch") or ""
        parsed = parse_unified_diff(patch, default_path=path)
        if parsed:
            d = parsed[0]
            d.path = path
            d.old_path = previous or (None if status == "added" else path)
            d.added = status == "added"
            d.deleted = status == "removed"
            diffs.append(d)
        else:
            diffs.append(
                FileDiff(
                    path=path,
                    old_path=previous or (None if status == "added" else path),
                    added=status == "added",
                    deleted=status == "removed",
                    old_ranges=[] if status == "added" else [(1, 10_000_000)],
                    new_ranges=[] if status == "removed" else [(1, 10_000_000)],
                )
            )
    return diffs


def pr_diff_ranges(owner: str, repo: str, number: int, token: str = "") -> tuple[dict, dict[str, list[tuple[int, int]]]]:
    meta = fetch_pr(owner, repo, number, token)
    ranges = ranges_from_file_diffs(fetch_pr_file_diffs(owner, repo, number, token))
    return meta, ranges


def _headers(token: str) -> dict:
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _get(url: str, token: str) -> dict:
    resp = requests.get(url, headers=_headers(token), timeout=30)
    if resp.status_code == 404:
        raise PRError(f"PR not found: {url}")
    if resp.status_code == 401:
        raise PRError("GitHub rejected the token; set GITHUB_TOKEN or re-run `repograph activate`.")
    if resp.status_code == 403 and not token:
        raise PRError("GitHub rate-limited an anonymous request; set GITHUB_TOKEN.")
    resp.raise_for_status()
    return resp.json()


def _get_paginated(url: str, token: str) -> list[dict]:
    items: list[dict] = []
    while url:
        resp = requests.get(url, headers=_headers(token), timeout=30)
        if resp.status_code >= 400:
            _get(url, token)  # reuse error mapping
        items.extend(resp.json())
        url = resp.links.get("next", {}).get("url")
    return items
