"""Media generation: images (Gemini Nano Banana) and music (Suno)."""

from __future__ import annotations

import io
import logging
import os
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)


async def generate_image(prompt: str) -> bytes | None:
    """Generate an image using Gemini Nano Banana 2.

    Returns PNG bytes, or None on failure.
    Requires GOOGLE_API_KEY environment variable.
    """
    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        logger.warning("GOOGLE_API_KEY not set, skipping image generation")
        return None

    try:
        from google import genai

        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model="gemini-2.5-flash-preview-image-generation",
            contents=[prompt],
        )

        for part in response.parts:
            if part.inline_data is not None:
                image = part.as_image()
                buf = io.BytesIO()
                image.save(buf, format="PNG")
                logger.info("Image generated: %d bytes", buf.tell())
                return buf.getvalue()

        logger.warning("No image in response for prompt: %s", prompt[:80])
        return None
    except Exception:
        logger.exception("Image generation failed")
        return None


async def generate_music(prompt: str, title: str = "AI Generated") -> Path | None:
    """Generate music using Suno API.

    Returns path to downloaded audio file, or None on failure.
    Requires SUNO_COOKIE environment variable.
    """
    cookie = os.environ.get("SUNO_COOKIE")
    if not cookie:
        logger.warning("SUNO_COOKIE not set, skipping music generation")
        return None

    try:
        from suno import Suno, ModelVersions

        client = Suno(cookie=cookie, model_version=ModelVersions.CHIRP_V3_5)
        songs = client.generate(
            prompt=prompt,
            is_custom=False,
            wait_audio=True,
        )

        if not songs:
            logger.warning("No songs generated for prompt: %s", prompt[:80])
            return None

        song = songs[0]
        file_path = client.download(song=song)
        logger.info("Music generated: %s", file_path)
        return Path(file_path)
    except Exception:
        logger.exception("Music generation failed")
        return None


async def upload_image_to_slack(
    client,
    channel: str,
    image_bytes: bytes,
    title: str = "AI Generated Image",
    comment: str = "",
    thread_ts: str = "",
) -> str:
    """Upload an image to Slack. Returns the message ts."""
    kwargs: dict = {
        "channels": channel,
        "content": image_bytes,
        "filename": "generated.png",
        "title": title,
    }
    if comment:
        kwargs["initial_comment"] = comment
    if thread_ts:
        kwargs["thread_ts"] = thread_ts

    result = await client.files_upload_v2(**kwargs)
    return result.get("file", {}).get("shares", {}).get("ts", "")


async def upload_file_to_slack(
    client,
    channel: str,
    file_path: Path,
    title: str = "AI Generated Music",
    comment: str = "",
    thread_ts: str = "",
) -> str:
    """Upload a file to Slack. Returns the message ts."""
    kwargs: dict = {
        "channels": channel,
        "file": str(file_path),
        "title": title,
    }
    if comment:
        kwargs["initial_comment"] = comment
    if thread_ts:
        kwargs["thread_ts"] = thread_ts

    result = await client.files_upload_v2(**kwargs)
    return result.get("file", {}).get("shares", {}).get("ts", "")
