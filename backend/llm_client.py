"""Minimal client for any OpenAI-compatible chat-completions endpoint.

The agent's reasoning brain is deliberately provider-agnostic: point
LLM_BASE_URL / LLM_MODEL at OpenAI, Azure OpenAI, or any compatible gateway.
Qloo remains the source of truth for taste -- the LLM never invents venues,
it only reasons over candidates returned by the Qloo API.
"""
from __future__ import annotations

import json
from typing import Any, Optional

import httpx


class LLMError(RuntimeError):
    pass


class LLMClient:
    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.openai.com/v1",
        model: str = "gpt-4o-mini",
        timeout_s: float = 60.0,
        transport: Optional[httpx.BaseTransport] = None,
    ) -> None:
        if not api_key:
            raise LLMError("LLM_API_KEY is not set.")
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._client = httpx.Client(timeout=timeout_s, transport=transport)

    def chat_json(self, system: str, user: str, max_tokens: int = 2000) -> Any:
        """Ask the LLM to return *only* a JSON document; parse and return it."""
        try:
            return self._chat_json(system, user, max_tokens)
        except httpx.HTTPError as exc:
            raise LLMError("Model connection failed. Please try again later.") from exc

    def _chat_json(self, system: str, user: str, max_tokens: int) -> dict[str, Any]:
        resp = self._client.post(
            f"{self._base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": self._model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "temperature": 0.4,
                "max_tokens": max_tokens,
                "response_format": {"type": "json_object"},
            },
        )
        if resp.status_code >= 400:
            raise LLMError(f"Model API error (HTTP {resp.status_code}).")
        try:
            content = resp.json()["choices"][0]["message"]["content"]
            data = json.loads(content)
            if not isinstance(data, dict):
                raise ValueError("expected object")
            return data
        except (KeyError, ValueError, IndexError, TypeError) as exc:
            raise LLMError("Model returned an invalid JSON object.") from exc

    def close(self) -> None:
        self._client.close()
