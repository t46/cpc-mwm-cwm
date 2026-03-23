from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class VTTEntry:
    start_time: str
    end_time: str
    speaker: str | None
    text: str


def parse_vtt(content: str) -> list[VTTEntry]:
    """Parse WebVTT format content into entries."""
    entries: list[VTTEntry] = []
    # Remove BOM and WEBVTT header
    content = content.strip().lstrip("\ufeff")
    if content.startswith("WEBVTT"):
        content = content[len("WEBVTT"):]
        # Skip any header lines until first blank line
        idx = content.find("\n\n")
        if idx != -1:
            content = content[idx:]

    # Split into blocks
    blocks = re.split(r"\n\s*\n", content.strip())

    for block in blocks:
        lines = block.strip().split("\n")
        if not lines:
            continue

        # Skip cue identifiers (numeric lines)
        line_idx = 0
        if lines[line_idx].strip().isdigit():
            line_idx += 1
            if line_idx >= len(lines):
                continue

        # Look for timestamp line
        timestamp_pattern = r"(\d{2}:\d{2}:\d{2}\.\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}\.\d{3})"
        match = re.match(timestamp_pattern, lines[line_idx].strip())
        if not match:
            continue

        start_time = match.group(1)
        end_time = match.group(2)
        line_idx += 1

        # Remaining lines are text
        text_lines = [l.strip() for l in lines[line_idx:] if l.strip()]
        if not text_lines:
            continue

        full_text = " ".join(text_lines)

        # Check for speaker label (e.g., "<v Speaker Name>text</v>" or "Speaker: text")
        speaker = None
        speaker_match = re.match(r"<v\s+([^>]+)>(.*?)(?:</v>)?$", full_text)
        if speaker_match:
            speaker = speaker_match.group(1).strip()
            full_text = speaker_match.group(2).strip()
        else:
            colon_match = re.match(r"^([^:]{1,30}):\s+(.+)$", full_text)
            if colon_match:
                speaker = colon_match.group(1).strip()
                full_text = colon_match.group(2).strip()

        entries.append(VTTEntry(
            start_time=start_time,
            end_time=end_time,
            speaker=speaker,
            text=full_text,
        ))

    return entries


def parse_plain_text(text: str) -> list[VTTEntry]:
    """Wrap plain text as a single VTT entry (fallback)."""
    return [
        VTTEntry(
            start_time="00:00:00.000",
            end_time="00:00:00.000",
            speaker=None,
            text=text.strip(),
        )
    ]


def vtt_entries_to_text(entries: list[VTTEntry]) -> str:
    """Convert VTT entries to plain text for the session transcript."""
    parts: list[str] = []
    for entry in entries:
        prefix = f"{entry.speaker}: " if entry.speaker else ""
        parts.append(f"[{entry.start_time}] {prefix}{entry.text}")
    return "\n".join(parts)
