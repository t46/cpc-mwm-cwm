"""Publish whitepaper markdown to a GitHub repository."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from github import Auth, Github

logger = logging.getLogger(__name__)


def publish_to_github(
    token: str,
    repo_name: str,
    content: str,
    path: Optional[str] = None,
    branch: str = "main",
    commit_message: Optional[str] = None,
) -> str:
    """Push a whitepaper markdown file to a GitHub repo.

    Args:
        token: GitHub personal access token.
        repo_name: Full repo name, e.g. "kojino/cpc-cwm".
        content: The markdown content to publish.
        path: File path in the repo. Defaults to "whitepapers/YYYY-MM-DD.md".
        branch: Target branch.
        commit_message: Commit message. Auto-generated if None.

    Returns:
        The URL of the created/updated file on GitHub.
    """
    auth = Auth.Token(token)
    g = Github(auth=auth)
    repo = g.get_repo(repo_name)

    if path is None:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        path = f"whitepapers/{today}.md"

    if commit_message is None:
        commit_message = f"Add whitepaper: {path}"

    # Check if file already exists (update vs create)
    try:
        existing = repo.get_contents(path, ref=branch)
        result = repo.update_file(
            path=path,
            message=commit_message,
            content=content,
            sha=existing.sha,
            branch=branch,
        )
        logger.info(f"Updated existing file: {path}")
    except Exception:
        result = repo.create_file(
            path=path,
            message=commit_message,
            content=content,
            branch=branch,
        )
        logger.info(f"Created new file: {path}")

    file_url = f"https://github.com/{repo_name}/blob/{branch}/{path}"
    logger.info(f"Published to: {file_url}")
    g.close()
    return file_url
