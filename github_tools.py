"""
github_tools.py — real GitHub integration: push, branch, open PRs.

Uses your GITHUB_TOKEN (set as a Replit Secret) against the GitHub REST API
directly — no gh CLI dependency, just requests, same as everything else
in this project.

Repo owner/name are auto-detected from `git remote origin` so you don't
have to pass them every time — works as long as the project folder is
already a git repo with a GitHub remote set up.
"""

import os
import re
import requests

from tools import run_bash

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")
GITHUB_API = "https://api.github.com"


def _get_repo_slug():
    """Parses 'owner/repo' out of `git remote get-url origin`, handling both
    HTTPS (https://github.com/owner/repo.git) and SSH (git@github.com:owner/repo.git)
    remote URL formats."""
    result = run_bash("git remote get-url origin", confirmed=True)
    if "exit code 0" not in result:
        return None, f"Could not read git remote: {result}"

    url_line = result.split("\n", 1)[1].strip() if "\n" in result else ""
    match = re.search(r"github\.com[:/]([^/]+)/([^/.\s]+)", url_line)
    if not match:
        return None, f"Could not parse a GitHub owner/repo from remote URL: {url_line}"
    return f"{match.group(1)}/{match.group(2)}", None


def _headers():
    return {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
    }


def git_push(branch=None, set_upstream=True):
    """Pushes the current branch (or `branch` if given) to origin."""
    if not branch:
        result = run_bash("git rev-parse --abbrev-ref HEAD", confirmed=True)
        if "exit code 0" not in result:
            return f"Could not determine current branch: {result}"
        branch = result.split("\n", 1)[1].strip()

    cmd = f"git push {'--set-upstream ' if set_upstream else ''}origin {branch}"
    return run_bash(cmd, confirmed=True, timeout=60)


def create_branch(branch_name, from_branch="main"):
    """Creates and checks out a new local branch off `from_branch`."""
    result = run_bash(f"git checkout {from_branch} && git pull && git checkout -b {branch_name}", confirmed=True)
    return result


def open_pull_request(title, body="", head=None, base="main"):
    """
    Opens a real pull request on GitHub via the API.
    `head` defaults to the current branch if not given.
    Requires GITHUB_TOKEN with repo scope, and the branch must already be
    pushed to origin (call git_push first).
    """
    if not GITHUB_TOKEN:
        return "ERROR: GITHUB_TOKEN not set. Add it as a Replit Secret first."

    repo_slug, err = _get_repo_slug()
    if err:
        return f"ERROR: {err}"

    if not head:
        result = run_bash("git rev-parse --abbrev-ref HEAD", confirmed=True)
        if "exit code 0" not in result:
            return f"Could not determine current branch: {result}"
        head = result.split("\n", 1)[1].strip()

    try:
        resp = requests.post(
            f"{GITHUB_API}/repos/{repo_slug}/pulls",
            headers=_headers(),
            json={"title": title, "body": body, "head": head, "base": base},
            timeout=20,
        )
    except requests.exceptions.RequestException as e:
        return f"ERROR: could not reach GitHub API: {e}"

    if resp.status_code == 201:
        data = resp.json()
        return f"Pull request opened: {data['html_url']}"

    return f"ERROR: GitHub API returned {resp.status_code}: {resp.text[:500]}"


def list_open_issues(limit=10):
    """Lists open issues on the repo — useful context before starting work."""
    if not GITHUB_TOKEN:
        return "ERROR: GITHUB_TOKEN not set."

    repo_slug, err = _get_repo_slug()
    if err:
        return f"ERROR: {err}"

    try:
        resp = requests.get(
            f"{GITHUB_API}/repos/{repo_slug}/issues",
            headers=_headers(),
            params={"state": "open", "per_page": limit},
            timeout=20,
        )
    except requests.exceptions.RequestException as e:
        return f"ERROR: could not reach GitHub API: {e}"

    if resp.status_code != 200:
        return f"ERROR: GitHub API returned {resp.status_code}: {resp.text[:500]}"

    issues = resp.json()
    if not issues:
        return "No open issues."
    return "\n".join(f"#{i['number']}: {i['title']}" for i in issues if "pull_request" not in i)


# --- Tool schema, merged into the main agent tool registry ---
GITHUB_TOOL_SCHEMA = [
    {"type": "function", "function": {
        "name": "git_push",
        "description": "Push the current git branch to GitHub (origin).",
        "parameters": {"type": "object", "properties": {
            "branch": {"type": "string", "description": "Branch to push, defaults to current branch"},
        }},
    }},
    {"type": "function", "function": {
        "name": "create_branch",
        "description": "Create and check out a new git branch, based off an up-to-date base branch.",
        "parameters": {"type": "object", "properties": {
            "branch_name": {"type": "string"},
            "from_branch": {"type": "string", "default": "main"},
        }, "required": ["branch_name"]},
    }},
    {"type": "function", "function": {
        "name": "open_pull_request",
        "description": "Open a real pull request on GitHub. The branch must already be pushed "
                        "(call git_push first).",
        "parameters": {"type": "object", "properties": {
            "title": {"type": "string"},
            "body": {"type": "string", "default": ""},
            "head": {"type": "string", "description": "Source branch, defaults to current branch"},
            "base": {"type": "string", "default": "main"},
        }, "required": ["title"]},
    }},
    {"type": "function", "function": {
        "name": "list_open_issues",
        "description": "List open GitHub issues on this repo, for context before starting work.",
        "parameters": {"type": "object", "properties": {
            "limit": {"type": "integer", "default": 10},
        }},
    }},
]

GITHUB_TOOL_FUNCTIONS = {
    "git_push": git_push,
    "create_branch": create_branch,
    "open_pull_request": open_pull_request,
    "list_open_issues": list_open_issues,
}
