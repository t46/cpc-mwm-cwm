"""FEP (Free Energy Principle) agent YAML config loading and saving."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)


@dataclass
class FEPAgentConfig:
    name: str
    persona: str
    model: str
    generative_model: str  # immutable DNA/identity
    cwm_stance: str  # mutable
    perception_policy: str  # mutable
    action_policy: str  # mutable
    config_path: Path
    reflection_delay: int = 3600


def load_fep_agent_config(path: str | Path) -> FEPAgentConfig:
    """Load an FEP agent config from a YAML file."""
    path = Path(path)
    with path.open() as f:
        raw = yaml.safe_load(f)

    config = FEPAgentConfig(
        name=raw["name"],
        persona=raw["persona"],
        model=raw.get("model", "claude-sonnet-4-20250514"),
        generative_model=raw["generative_model"],
        cwm_stance=raw.get("cwm_stance", ""),
        perception_policy=raw.get("perception_policy", ""),
        action_policy=raw.get("action_policy", ""),
        config_path=path.resolve(),
        reflection_delay=raw.get("reflection_delay", 3600),
    )
    logger.info(
        "Loaded FEP agent config: %s (persona=%s)",
        config.name,
        config.persona,
    )
    return config


def save_fep_agent_config(config: FEPAgentConfig) -> None:
    """Write updated mutable policies back to the YAML file.

    Preserves immutable fields (name, persona, model, generative_model)
    and only updates cwm_stance, perception_policy, action_policy.
    """
    path = config.config_path
    with path.open() as f:
        raw = yaml.safe_load(f)

    raw["cwm_stance"] = config.cwm_stance
    raw["perception_policy"] = config.perception_policy
    raw["action_policy"] = config.action_policy

    with path.open("w", encoding="utf-8") as f:
        yaml.dump(raw, f, default_flow_style=False, allow_unicode=True, sort_keys=False)

    logger.info("Saved FEP agent config: %s", config.name)
