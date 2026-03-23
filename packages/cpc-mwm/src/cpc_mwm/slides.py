from __future__ import annotations

import logging

import pymupdf
from slack_sdk.web.async_client import AsyncWebClient

logger = logging.getLogger(__name__)


async def download_file_from_slack(
    client: AsyncWebClient, file_info: dict
) -> bytes | None:
    """Download a file from Slack using the bot token."""
    url = file_info.get("url_private")
    if not url:
        logger.warning("No url_private in file info")
        return None

    try:
        import aiohttp

        headers = {"Authorization": f"Bearer {client.token}"}
        async with aiohttp.ClientSession() as http_session:
            async with http_session.get(url, headers=headers) as resp:
                if resp.status == 200:
                    data = await resp.read()
                    logger.info(
                        "Downloaded file: %s (%d bytes)",
                        file_info.get("name", "unknown"),
                        len(data),
                    )
                    return data
                else:
                    logger.error("Failed to download file: HTTP %d", resp.status)
                    return None
    except Exception:
        logger.exception("Error downloading file from Slack")
        return None


def extract_slide_texts(pdf_bytes: bytes) -> list[str]:
    """Extract text from each page of a PDF using PyMuPDF."""
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    texts: list[str] = []
    for page in doc:
        text = page.get_text()
        texts.append(text)
    doc.close()
    logger.info("Extracted text from %d PDF pages", len(texts))
    return texts
