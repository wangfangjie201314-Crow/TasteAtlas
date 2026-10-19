"""TasteAtlas agent pipeline: Profiler -> Scout -> Planner -> Critic.

The design principle for this hackathon entry: Qloo is the brain's taste buds,
the LLM is the mouthpiece. Every recommendation MUST originate from a Qloo API
entity (the Critic enforces this in code). The LLM reasons, sequences and
explains -- it never invents venues, films or brands.

Agents:
  1. Profiler -- turns free-text "things I love" into Qloo entity IDs + tag IDs.
  2. Scout     -- queries Qloo Insights per itinerary slot, grounded in the
                 taste profile and the target city.
  3. Planner   -- LLM composes a timed itinerary from Scout's candidates only,
                 with a taste-grounded "why this fits you" for every stop.
  4. Critic    -- code-level verification: every stop must reference a real
                 Qloo entity_id returned by the Scout; hallucinations are
                 dropped, not shown.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Optional

from .llm_client import LLMClient, LLMError
from .qloo_client import (
    ENTITY_ARTIST,
    ENTITY_BRAND,
    ENTITY_DESTINATION,
    ENTITY_MOVIE,
    ENTITY_PLACE,
    QlooClient,
)

log = logging.getLogger("tasteatlas")

# --------------------------------------------------------------------------- #
# Data shapes
# --------------------------------------------------------------------------- #

@dataclass
class TasteProfile:
    city: str
    occasion: str
    liked_entities: list[dict[str, str]] = field(default_factory=list)  # name/kind/id
    taste_tags: list[dict[str, str]] = field(default_factory=list)       # term/tag_id
    signal_entity_ids: list[str] = field(default_factory=list)
    signal_tag_ids: list[str] = field(default_factory=list)

    def summary(self) -> str:
        names = ", ".join(e["name"] for e in self.liked_entities) or "—"
        tags = ", ".join(t["term"] for t in self.taste_tags) or "—"
        return f"Loves: {names} | Taste tags: {tags}"


@dataclass
class Candidate:
    slot: str
    name: str
    entity_id: str
    kind: str
    description: str = ""
    image_url: str = ""
    tags: list[str] = field(default_factory=list)
    affinity: Optional[float] = None

    @classmethod
    def from_qloo(cls, slot: str, e: dict[str, Any]) -> "Candidate":
        props = e.get("properties") or {}
        img = (props.get("image") or {}).get("url", "")
        tags = [t.get("name", "") for t in (e.get("tags") or []) if isinstance(t, dict)]
        return cls(
            slot=slot,
            name=e.get("name", "Unknown"),
            entity_id=e.get("entity_id", ""),
            kind=str(e.get("subtype") or e.get("type") or ""),
            description=props.get("description", "") or "",
            image_url=img,
            tags=[t for t in tags if t],
            affinity=e.get("affinity"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "slot": self.slot,
            "name": self.name,
            "entity_id": self.entity_id,
            "kind": self.kind,
            "description": self.description,
            "image_url": self.image_url,
            "tags": self.tags,
            "affinity": self.affinity,
        }


@dataclass
class Stop:
    slot: str
    time: str
    name: str
    entity_id: str
    kind: str
    why: str            # taste-grounded explanation
    tip: str = ""
    image_url: str = ""
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "slot": self.slot, "time": self.time, "name": self.name,
            "entity_id": self.entity_id, "kind": self.kind, "why": self.why,
            "tip": self.tip, "image_url": self.image_url, "tags": self.tags,
        }


@dataclass
class PlanResult:
    profile: TasteProfile
    stops: list[Stop]
    dropped: list[dict[str, str]]          # stops the Critic removed + reason
    qloo_calls: list[str]                 # transparency log for judges
    occasion: str
    city: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "city": self.city,
            "occasion": self.occasion,
            "taste_profile": self.profile.summary(),
            "stops": [s.to_dict() for s in self.stops],
            "dropped_by_critic": self.dropped,
            "qloo_calls": self.qloo_calls,
        }


# --------------------------------------------------------------------------- #
# Agent 1: Profiler
# --------------------------------------------------------------------------- #

_PROFILER_SYSTEM = """You extract structured taste seeds from a user's free-text description
of things they love. Return ONLY a JSON object:
{"entities": [{"name": "...", "kind": "movie|tv_show|artist|brand|book|cuisine|place|podcast|game"}],
 "keywords": ["..."]}
Rules:
- entities: concrete named things (films, shows, musicians, brands, books, restaurants...).
- keywords: taste/aesthetic descriptors (e.g. "ramen", "jazz", "minimalist", "neon-noir").
- Keep it tight: max 6 entities, max 8 keywords. No prose, JSON only."""

_TAG_TYPE_HINTS = {
    "cuisine": "urn:tag:cuisine",
    "movie": "urn:tag:genre:movie",
    "artist": "urn:tag:genre:music",
}


class Profiler:
    def __init__(self, qloo: QlooClient, llm: LLMClient) -> None:
        self.qloo = qloo
        self.llm = llm

    def run(self, likes_text: str, city: str, occasion: str) -> tuple[TasteProfile, list[str]]:
        calls: list[str] = []
        seeds = self.llm.chat_json(
            _PROFILER_SYSTEM,
            f"User description of things they love:\n{likes_text}",
            max_tokens=600,
        )
        profile = TasteProfile(city=city, occasion=occasion)

        items, keywords = seeds.get("entities", []), seeds.get("keywords", [])
        if (not isinstance(items, list) or not isinstance(keywords, list)
                or any(not isinstance(i, dict) or not isinstance(i.get("name"), str)
                       or not isinstance(i.get("kind", ""), str) for i in items)
                or any(not isinstance(k, str) for k in keywords)):
            raise LLMError("Model returned an invalid taste profile.")
        for item in items[:6]:
            name = (item.get("name") or "").strip()
            kind = (item.get("kind") or "").strip().lower()
            if not name:
                continue
            hits = self.qloo.search_entities(name, take=3)
            calls.append(f"GET /search query={name!r}")
            if not hits:
                continue
            best = hits[0]
            eid = best.get("entity_id", "")
            profile.liked_entities.append({"name": best.get("name", name), "kind": kind, "id": eid})
            if eid:
                profile.signal_entity_ids.append(eid)

        for kw in keywords[:8]:
            kw = str(kw).strip()
            if not kw:
                continue
            tags = self.qloo.search_tags(kw, take=4)
            calls.append(f"GET /v2/tags filter.query={kw!r}")
            if not tags:
                continue
            tag = tags[0]
            tid = tag.get("tag_id") or tag.get("id", "")
            if tid:
                profile.taste_tags.append({"term": kw, "tag_id": tid})
                profile.signal_tag_ids.append(tid)

        if not profile.signal_entity_ids and not profile.signal_tag_ids:
            raise RuntimeError("No taste matches were found. Try named films, artists or broader interests.")
        return profile, calls


# --------------------------------------------------------------------------- #
# Agent 2: Scout
# --------------------------------------------------------------------------- #

# (slot, filter.type, count, hard tag filters)
_SLOT_SPECS: dict[str, list[tuple[str, str, int]]] = {
    "foodie_day": [
        ("lunch", ENTITY_PLACE, 8),
        ("cafe", ENTITY_PLACE, 6),
        ("dinner", ENTITY_PLACE, 8),
        ("evening_watch", ENTITY_MOVIE, 6),
        ("shopping", ENTITY_BRAND, 6),
    ],
    "date_night": [
        ("dinner", ENTITY_PLACE, 8),
        ("drinks", ENTITY_PLACE, 6),
        ("evening_watch", ENTITY_MOVIE, 6),
        ("live_music", ENTITY_ARTIST, 6),
    ],
    "solo_adventure": [
        ("lunch", ENTITY_PLACE, 8),
        ("explore", ENTITY_PLACE, 6),
        ("dinner", ENTITY_PLACE, 8),
        ("evening_watch", ENTITY_MOVIE, 6),
    ],
    "family": [
        ("lunch", ENTITY_PLACE, 8),
        ("explore", ENTITY_PLACE, 6),
        ("dinner", ENTITY_PLACE, 8),
        ("evening_watch", ENTITY_MOVIE, 6),
    ],
}

_SLOT_TIMES = {
    "lunch": "12:30", "cafe": "15:00", "dinner": "19:00", "drinks": "21:00",
    "evening_watch": "21:30", "live_music": "20:30", "shopping": "16:00",
    "explore": "14:00", "read": "22:00",
}


class Scout:
    def __init__(self, qloo: QlooClient) -> None:
        self.qloo = qloo

    def run(self, profile: TasteProfile) -> tuple[dict[str, list[Candidate]], list[str]]:
        calls: list[str] = []
        specs = _SLOT_SPECS.get(profile.occasion, _SLOT_SPECS["foodie_day"])
        pools: dict[str, list[Candidate]] = {}
        category_cache: dict[str, list[str]] = {}
        categories = {"lunch": "restaurant", "dinner": "restaurant", "cafe": "cafe", "drinks": "bar", "explore": "museum"}
        for slot, ftype, take in specs:
            if take <= 0:
                continue
            category = categories.get(slot)
            tag_ids = None
            if category:
                if category not in category_cache:
                    tags = self.qloo.search_tags(category, types="urn:tag:genre:place", take=10)
                    aliases = {category, category + "s", "café" if category == "cafe" else category}
                    category_cache[category] = [t.get("tag_id") or t.get("id") for t in tags
                        if t.get("name", "").casefold() in aliases and (t.get("tag_id") or t.get("id"))][:1]
                    calls.append(f"GET /v2/tags filter.query={category!r} purpose=slot-category")
                tag_ids = category_cache[category]
                if not tag_ids:
                    pools[slot] = []
                    continue
            entities = self.qloo.insights(
                    filter_type=ftype,
                    signal_interests_entities=profile.signal_entity_ids or None,
                    signal_interests_tags=profile.signal_tag_ids or None,
                    signal_location_query=profile.city,
                    filter_tags=tag_ids,
                    take=take,
                )
            calls.append(
                    f"GET /v2/insights filter.type={ftype} "
                    f"location={profile.city!r} signals={len(profile.signal_entity_ids)}e+{len(profile.signal_tag_ids)}t"
                )
            pools[slot] = [Candidate.from_qloo(slot, e) for e in entities if e.get("entity_id")]
        return pools, calls


# --------------------------------------------------------------------------- #
# Agent 3: Planner
# --------------------------------------------------------------------------- #

_PLANNER_SYSTEM = """You are the itinerary composer of TasteAtlas. You receive:
1. A user's taste profile (things they love + taste tags from Qloo).
2. Candidate venues/films/brands PER TIME SLOT, each with a Qloo entity_id.
   These candidates were retrieved from Qloo's taste graph BECAUSE they match
   the user's taste in the target city.

Return ONLY a JSON object:
{"stops": [{"slot": "...", "time": "HH:MM", "entity_id": "...",
            "why": "...", "tip": "..."}]}

HARD RULES:
- Every stop's entity_id MUST be copied verbatim from the candidates list.
- NEVER invent a venue, film, artist or brand. If a slot has no good candidate, SKIP the slot.
- Order stops chronologically. Keep times realistic for one day/evening.
- "why" (1-2 sentences) must connect the pick to the user's taste profile
  (name the loved thing / tag it matches). No generic praise.
- "tip": leave empty. Opening hours, prices and availability have not been verified.
- Explain taste matches as suggestions; do not invent factual claims about venues."""


class Planner:
    def __init__(self, llm: LLMClient) -> None:
        self.llm = llm

    def run(
        self,
        profile: TasteProfile,
        pools: dict[str, list[Candidate]],
    ) -> list[dict[str, Any]]:
        cand_text: list[str] = []
        for slot, cands in pools.items():
            for c in cands:
                tag_str = ", ".join(c.tags[:5])
                cand_text.append(
                    f"[{slot}] id={c.entity_id} | {c.name} ({c.kind}) | "
                    f"tags: {tag_str} | {c.description[:160]}"
                )
        if not cand_text:
            raise RuntimeError(
                "Qloo returned no candidates for this city/taste combination. "
                "Try a larger city or broader tastes."
            )
        user_msg = (
            f"Taste profile: {profile.summary()}\n"
            f"City: {profile.city} | Occasion: {profile.occasion}\n\n"
            f"Candidates:\n" + "\n".join(cand_text)
        )
        plan = self.llm.chat_json(_PLANNER_SYSTEM, user_msg, max_tokens=2000)
        stops = plan.get("stops") or []
        return stops if isinstance(stops, list) else []


# --------------------------------------------------------------------------- #
# Agent 4: Critic
# --------------------------------------------------------------------------- #

class Critic:
    """Code-level anti-hallucination gate.

    A stop survives only if its entity_id appears verbatim in the Scout's
    candidate pools. Duplicates across slots are removed (first wins).
    """

    def run(
        self,
        planned: list[dict[str, Any]],
        pools: dict[str, list[Candidate]],
    ) -> tuple[list[Stop], list[dict[str, str]]]:
        stops: list[Stop] = []
        dropped: list[dict[str, str]] = []
        seen: set[str] = set()
        for p in planned:
            if not isinstance(p, dict):
                dropped.append({"slot": "", "reason": "invalid stop object -- dropped"})
                continue
            eid = str(p.get("entity_id", "")).strip()
            slot = str(p.get("slot", ""))
            cand = next((c for c in pools.get(slot, []) if c.entity_id == eid), None)
            if not cand:
                dropped.append({"slot": slot, "reason": f"entity_id {eid!r} not in Qloo results -- dropped"})
                continue
            time = str(p.get("time") or _SLOT_TIMES.get(slot, ""))
            if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", time):
                dropped.append({"slot": slot, "reason": "invalid time -- dropped"})
                continue
            if eid in seen:
                dropped.append({"slot": slot, "reason": f"{cand.name!r} already used -- duplicate dropped"})
                continue
            seen.add(eid)
            stops.append(
                Stop(
                    slot=slot or cand.slot,
                    time=time,
                    name=cand.name,
                    entity_id=eid,
                    kind=cand.kind,
                    why=str(p.get("why") or ""),
                    tip=("Check opening hours and availability before visiting." if cand.kind == ENTITY_PLACE
                         else "Cultural inspiration only; local availability has not been verified."),
                    image_url=cand.image_url,
                    tags=cand.tags,
                )
            )
        # chronological order
        stops.sort(key=lambda s: s.time)
        return stops, dropped


# --------------------------------------------------------------------------- #
# Orchestrator
# --------------------------------------------------------------------------- #

class TasteAtlasPipeline:
    def __init__(self, qloo: QlooClient, llm: LLMClient) -> None:
        self.profiler = Profiler(qloo, llm)
        self.scout = Scout(qloo)
        self.planner = Planner(llm)
        self.critic = Critic()

    def plan(self, city: str, likes: str, occasion: str = "foodie_day") -> PlanResult:
        qloo_calls: list[str] = []

        profile, calls = self.profiler.run(likes, city, occasion)
        qloo_calls += calls

        pools, calls = self.scout.run(profile)
        qloo_calls += calls

        planned = self.planner.run(profile, pools)
        stops, dropped = self.critic.run(planned, pools)

        if not stops:
            raise RuntimeError(
                "The Critic dropped every planned stop (none traced to Qloo entities). "
                "Try different tastes or a different city."
            )
        return PlanResult(
            profile=profile, stops=stops, dropped=dropped,
            qloo_calls=qloo_calls, occasion=occasion, city=city,
        )
