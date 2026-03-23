"""UX pattern YAML config loading."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)


@dataclass
class PatternConfig:
    name: str
    label: str
    description: str
    response_prompt: str
    allowed_actions: list[str] = field(default_factory=lambda: ["reply", "new_topic"])


def load_pattern_config(path: str | Path) -> PatternConfig:
    """Load a single pattern config from a YAML file."""
    path = Path(path)
    with path.open() as f:
        raw = yaml.safe_load(f)

    config = PatternConfig(
        name=raw["name"],
        label=raw.get("label", raw["name"]),
        description=raw.get("description", ""),
        response_prompt=raw.get("response_prompt", ""),
        allowed_actions=raw.get("allowed_actions", ["reply", "new_topic"]),
    )
    logger.info("Loaded pattern: %s (%s)", config.name, config.label)
    return config


def load_patterns(names: list[str], patterns_dir: Path) -> dict[str, PatternConfig]:
    """Load multiple patterns by name from a directory.

    Returns a dict mapping pattern name to PatternConfig.
    """
    patterns: dict[str, PatternConfig] = {}
    for name in names:
        path = patterns_dir / f"{name}.yml"
        if not path.exists():
            logger.warning("Pattern file not found: %s", path)
            continue
        patterns[name] = load_pattern_config(path)
    return patterns
