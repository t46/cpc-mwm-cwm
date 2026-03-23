"""Persona loading with optional whitepaper injection."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import frontmatter


@dataclass
class Persona:
    name: str
    style: str
    avatar_emoji: str
    system_prompt: str


def load_persona(path: str | Path, whitepaper_content: str = "") -> Persona:
    """Load persona definition from a Markdown file with YAML frontmatter.

    If the persona file contains a ``{{whitepaper}}`` placeholder, it will be
    replaced with *whitepaper_content*.  When the placeholder is present but
    no content is provided, a fallback message is inserted instead.  Files
    without the placeholder are loaded as-is (backward compatible).
    """
    post = frontmatter.load(str(path))
    system_prompt = post.content

    if "{{whitepaper}}" in system_prompt:
        replacement = (
            whitepaper_content
            if whitepaper_content
            else "（ホワイトペーパーはまだ生成されていません）"
        )
        system_prompt = system_prompt.replace("{{whitepaper}}", replacement)

    return Persona(
        name=post.get("name", "Bot"),
        style=post.get("style", ""),
        avatar_emoji=post.get("avatar_emoji", ":robot_face:"),
        system_prompt=system_prompt,
    )
