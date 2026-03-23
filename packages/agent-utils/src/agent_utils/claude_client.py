"""Anthropic Claude client helpers."""

from __future__ import annotations

import anthropic


def create_client(api_key: str | None = None) -> anthropic.Anthropic:
    """Create a synchronous Anthropic client."""
    kwargs = {}
    if api_key:
        kwargs["api_key"] = api_key
    return anthropic.Anthropic(**kwargs)


def create_async_client(api_key: str | None = None) -> anthropic.AsyncAnthropic:
    """Create an asynchronous Anthropic client."""
    kwargs = {}
    if api_key:
        kwargs["api_key"] = api_key
    return anthropic.AsyncAnthropic(**kwargs)
