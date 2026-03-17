"""Unified LLM client for Anthropic and OpenAI-compatible APIs."""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Dict, List, Optional

import httpx

from ..config.settings import settings

logger = logging.getLogger(__name__)

Message = Dict[str, str]


def _strip_think_tags(text: str) -> str:
    """Remove reasoning blocks produced by thinking models (MiniMax, DeepSeek-R1, etc.)."""
    # <think>...</think> and <thinking>...</thinking>
    text = re.sub(r"<think(?:ing)?[^>]*>.*?</think(?:ing)?>", "", text, flags=re.DOTALL | re.IGNORECASE)
    # HTML comments <!-- ... -->
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    # [thinking]...[/thinking]
    text = re.sub(r"\[thinking\].*?\[/thinking\]", "", text, flags=re.DOTALL | re.IGNORECASE)
    return text.strip()


class LLMClient:
    """Small wrapper around the configured real-model provider."""

    def __init__(self) -> None:
        self._anthropic = None

    def provider(self) -> Optional[str]:
        if settings.LLM_API_KEY and settings.LLM_BASE_URL:
            return "openai_compatible"
        if settings.ANTHROPIC_API_KEY:
            return "anthropic"
        return None

    def is_enabled(self) -> bool:
        return settings.LLM_MODE == "real" and self.provider() is not None

    def model_for(self, fast: bool = False) -> str:
        if self.provider() == "openai_compatible":
            if fast and settings.LLM_FAST_MODEL:
                return settings.LLM_FAST_MODEL
            return settings.LLM_MODEL
        if fast:
            return settings.CLAUDE_FAST_MODEL
        return settings.CLAUDE_MODEL

    async def complete(
        self,
        *,
        messages: List[Message],
        max_tokens: int,
        system: Optional[str] = None,
        model: Optional[str] = None,
    ) -> str:
        if not self.is_enabled():
            raise RuntimeError("Real LLM mode is not configured")

        provider = self.provider()
        resolved_model = model or self.model_for()

        if provider == "anthropic":
            return await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self._complete_anthropic(
                    messages=messages,
                    max_tokens=max_tokens,
                    system=system,
                    model=resolved_model,
                ),
            )

        if provider == "openai_compatible":
            return await self._complete_openai_compatible(
                messages=messages,
                max_tokens=max_tokens,
                system=system,
                model=resolved_model,
            )

        raise RuntimeError("No supported real-model provider configured")

    def _get_anthropic_client(self):
        if self._anthropic is None:
            import anthropic

            self._anthropic = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
        return self._anthropic

    def _complete_anthropic(
        self,
        *,
        messages: List[Message],
        max_tokens: int,
        system: Optional[str],
        model: str,
    ) -> str:
        client = self._get_anthropic_client()
        kwargs = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": messages,
        }
        if system:
            kwargs["system"] = system

        response = client.messages.create(**kwargs)
        parts = []
        for block in response.content:
            text = getattr(block, "text", "")
            if text:
                parts.append(text)
        return _strip_think_tags("\n".join(parts).strip())

    async def _complete_openai_compatible(
        self,
        *,
        messages: List[Message],
        max_tokens: int,
        system: Optional[str],
        model: str,
    ) -> str:
        payload_messages: List[Message] = []
        if system:
            payload_messages.append({"role": "system", "content": system})
        payload_messages.extend(messages)

        payload = {
            "model": model,
            "messages": payload_messages,
            "max_tokens": max_tokens,
        }
        headers = {
            "Authorization": f"Bearer {settings.LLM_API_KEY}",
            "Content-Type": "application/json",
        }
        url = f"{settings.LLM_BASE_URL.rstrip('/')}/chat/completions"

        async with httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=15.0)) as client:
            response = await client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()

        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            logger.error("Unexpected OpenAI-compatible response: %s", data)
            raise ValueError("Unexpected LLM response payload") from exc

        if isinstance(content, list):
            text_parts = [
                part.get("text", "")
                for part in content
                if isinstance(part, dict)
            ]
            return _strip_think_tags("".join(text_parts).strip())

        return _strip_think_tags(str(content).strip())
