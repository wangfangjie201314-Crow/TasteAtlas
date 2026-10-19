"""Thin, honest client for the Qloo Taste AI API (v2).

Implements exactly what the official Qloo hackathon developer guide documents:
  - Base URL: https://hackathon.api.qloo.com  (hackathon keys 401 elsewhere)
  - Auth:      X-Api-Key request header
  - GET /search            -> resolve a name to Qloo entity IDs
  - GET /v2/tags           -> resolve a keyword to Qloo tag IDs
  - GET /v2/insights       -> recommendations / taste insights
      (filter.type is mandatory; params go in the query string)

No caching of response payloads to disk or into the repo -- the Qloo key terms
allow private server-side caching only, and forbid storing response data in a
public repository.
"""
from __future__ import annotations

from typing import Any, Callable, Optional

import httpx
import time


# Entity categories supported by the hackathon Insights API.
ENTITY_PLACE = "urn:entity:place"
ENTITY_MOVIE = "urn:entity:movie"
ENTITY_TV_SHOW = "urn:entity:tv_show"
ENTITY_ARTIST = "urn:entity:artist"
ENTITY_BRAND = "urn:entity:brand"
ENTITY_DESTINATION = "urn:entity:destination"
ENTITY_BOOK = "urn:entity:book"
ENTITY_PODCAST = "urn:entity:podcast"
ENTITY_VIDEO_GAME = "urn:entity:video_game"
ENTITY_PERSON = "urn:entity:person"


class QlooError(RuntimeError):
    """Raised when the Qloo API call fails or returns an error payload."""


class QlooClient:
    def __init__(
        self,
        api_key: str,
        base_url: str = "https://hackathon.api.qloo.com",
        timeout_s: float = 30.0,
        transport: Optional[httpx.BaseTransport] = None,
        proxy: Optional[str] = None,
        before_request: Optional[Callable[[str], None]] = None,
        max_elapsed_s: Optional[float] = None,
    ) -> None:
        if not api_key:
            raise QlooError(
                "QLOO_API_KEY is not set. Request a hackathon key via the "
                "'API key request form' linked in the Qloo hackathon developer guide."
            )
        self.before_request = before_request
        self.request_count = 0
        self._deadline = time.monotonic()+max_elapsed_s if max_elapsed_s else None
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"X-Api-Key": api_key},
            timeout=timeout_s,
            transport=transport,
            proxy=proxy,
            trust_env=False,
        )

    # ------------------------------------------------------------------ #
    # low level
    # ------------------------------------------------------------------ #
    def _get(self, path: str, params: dict[str, Any], *, empty_on_404: bool = False) -> dict[str, Any]:
        # Drop Nones so we never send junk the API would silently ignore.
        clean = {k: v for k, v in params.items() if v is not None}
        if self._deadline is not None and time.monotonic()>self._deadline:
            raise QlooError('The Qloo workflow timed out. No automatic retry was sent.')
        if self.before_request is not None:
            self.before_request(path)
        self.request_count += 1
        try:
            with self._client.stream("GET", path, params=clean) as response:
                raw = bytearray()
                for chunk in response.iter_bytes():
                    if self._deadline is not None and time.monotonic()>self._deadline:
                        raise QlooError('The Qloo workflow timed out. No automatic retry was sent.')
                    raw.extend(chunk)
                    if len(raw) > 262144:
                        raise QlooError("Qloo response exceeded the safe size limit.")
                resp = httpx.Response(response.status_code, content=bytes(raw))
        except httpx.HTTPError as exc:
            raise QlooError("Qloo connection failed. Please try again later.") from exc
        if resp.status_code == 404 and empty_on_404:
            return {"results": {"entities": []}}
        if resp.status_code == 401:
            raise QlooError(
                "401 Unauthorized from Qloo. Hackathon keys only work against "
                "https://hackathon.api.qloo.com -- check QLOO_BASE_URL and your key."
            )
        if resp.status_code >= 400:
            raise QlooError(f"Qloo API error (HTTP {resp.status_code}). Please check access or try later.")
        try:
            data = resp.json()
        except ValueError as exc:
            raise QlooError("Qloo returned an invalid response.") from exc
        if not isinstance(data, dict):
            raise QlooError("Qloo returned an invalid response object.")
        if isinstance(data, dict) and data.get("success") is False:
            raise QlooError("Qloo could not complete the request.")
        return data

    @staticmethod
    def entities_of(payload: dict[str, Any]) -> list[dict[str, Any]]:
        """Extract the entity list from an insights/search response."""
        results = payload.get("results") or {}
        entities = results if isinstance(results, list) else results.get("entities", [])
        if not isinstance(entities, list) or any(not isinstance(e, dict) for e in entities):
            raise QlooError("Qloo returned an invalid entity list.")
        return entities

    # ------------------------------------------------------------------ #
    # documented endpoints
    # ------------------------------------------------------------------ #
    def search_entities(
        self,
        query: str,
        types: Optional[str] = None,
        take: int = 5,
    ) -> list[dict[str, Any]]:
        """Look up Qloo entity IDs by name (e.g. a movie, artist, restaurant)."""
        payload = self._get("/search", {"query": query, "types": types, "take": take}, empty_on_404=True)
        return self.entities_of(payload)

    def search_tags(
        self,
        term: str,
        types: Optional[str] = None,
        take: int = 10,
    ) -> list[dict[str, Any]]:
        """Find valid tag IDs for interests, cuisines, genres, etc."""
        payload = self._get("/v2/tags", {"filter.query": term, "filter.tag.types": types, "take": take})
        # /v2/tags returns results.tags
        results = payload.get("results") or {}
        tags = results.get("tags") or []
        if not isinstance(tags, list) or any(not isinstance(t, dict) for t in tags):
            raise QlooError("Qloo returned an invalid tag list.")
        return tags

    def insights(
        self,
        filter_type: str,
        signal_interests_entities: Optional[list[str]] = None,
        signal_interests_tags: Optional[list[str]] = None,
        signal_location_query: Optional[str] = None,
        filter_tags: Optional[list[str]] = None,
        take: int = 10,
    ) -> list[dict[str, Any]]:
        """Core recommendations endpoint. filter.type is mandatory.

        signal_interests_entities: Qloo entity IDs the user already likes.
        signal_interests_tags:     Qloo tag IDs describing taste affinities.
        signal_location_query:     natural-language place ("Austin", "SoHo"...).
        filter_tags:               hard attribute filters (e.g. brunch places).
        """
        params: dict[str, Any] = {"filter.type": filter_type, "take": take}
        if signal_interests_entities:
            params["signal.interests.entities"] = ",".join(signal_interests_entities)
        if signal_interests_tags:
            params["signal.interests.tags"] = ",".join(signal_interests_tags)
        if signal_location_query and filter_type == ENTITY_PLACE:
            params["filter.location.query"] = signal_location_query
            params["filter.location.radius"] = 0
        if filter_tags:
            params["filter.tags"] = ",".join(filter_tags)
        payload = self._get("/v2/insights", params)
        return self.entities_of(payload)

    def close(self) -> None:
        self._client.close()
