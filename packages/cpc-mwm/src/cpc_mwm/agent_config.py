"""Agent YAML config loading."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)


@dataclass
class PerceptionConfig:
    prompt: str
    model: str = "claude-haiku-4-5-20251001"


@dataclass
class ActionConfig:
    enabled: bool = True


@dataclass
class ResponseConfig:
    prompt: str
    model: str = "claude-sonnet-4-20250514"


@dataclass
class AgentConfig:
    name: str
    persona: str
    model: str
    perception: PerceptionConfig
    response: ResponseConfig
    actions: dict[str, ActionConfig] = field(default_factory=dict)


def _resolve_prompt(value: str, base_dir: Path) -> str:
    """If value ends with .md, load file contents; otherwise return as-is."""
    if value.strip().endswith(".md"):
        path = base_dir / value.strip()
        if not path.exists():
            logger.warning("Prompt file not found: %s", path)
            return value
        return path.read_text()
    return value


def load_agent_config(path: str | Path) -> AgentConfig:
    """Load an agent config from a YAML file.

    Prompt fields that end with ``.md`` are treated as file references
    and their contents are loaded from disk.
    """
    path = Path(path)
    with path.open() as f:
        raw = yaml.safe_load(f)

    base_dir = path.parent

    default_model = raw.get("model", ResponseConfig.model)

    # Perception — falls back to top-level model
    perc_raw = raw.get("perception", {})
    perception = PerceptionConfig(
        prompt=_resolve_prompt(perc_raw.get("prompt", ""), base_dir),
        model=perc_raw.get("model", default_model),
    )

    # Response
    resp_raw = raw.get("response", {})
    response = ResponseConfig(
        prompt=_resolve_prompt(resp_raw.get("prompt", ""), base_dir),
        model=resp_raw.get("model", default_model),
    )

    # Actions — default to all enabled if omitted
    actions_raw = raw.get("actions", {})
    actions: dict[str, ActionConfig] = {}
    for action_name, action_data in actions_raw.items():
        if isinstance(action_data, dict):
            actions[action_name] = ActionConfig(
                enabled=action_data.get("enabled", True),
            )
        else:
            actions[action_name] = ActionConfig(enabled=bool(action_data))

    config = AgentConfig(
        name=raw["name"],
        persona=raw["persona"],
        model=default_model,
        perception=perception,
        response=response,
        actions=actions,
    )
    logger.info(
        "Loaded agent config: %s (persona=%s, actions=%s)",
        config.name,
        config.persona,
        [k for k, v in config.actions.items() if v.enabled],
    )
    return config
