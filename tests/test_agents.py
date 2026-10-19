"""Self-tests for the TasteAtlas agent pipeline.

Everything external (Qloo API + LLM) is mocked via httpx.MockTransport, so
these run offline and prove the orchestration logic: profiling, scouting,
planning and -- critically -- the Critic's anti-hallucination gate.
Run:  python -m pytest tests/   (or: python tests/test_agents.py)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.agents import (  # noqa: E402
    Candidate,
    Critic,
    TasteAtlasPipeline,
    TasteProfile,
)
from backend.llm_client import LLMClient  # noqa: E402
from backend.qloo_client import QlooClient  # noqa: E402


# --------------------------------------------------------------------------- #
# Mock Qloo API
# --------------------------------------------------------------------------- #

def _entity(name, eid, subtype, desc="A great spot.", tags=("cozy",)):
    return {
        "name": name,
        "entity_id": eid,
        "type": "urn:entity",
        "subtype": subtype,
        "properties": {
            "description": desc,
            "image": {"url": f"https://images.qloo.com/i/{eid}.jpg"},
        },
        "tags": [{"name": t} for t in tags],
    }


def qloo_handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    params = dict(request.url.params)
    if path == "/search":
        q = params.get("query", "")
        return httpx.Response(200, json={
            "success": True,
            "results": {"entities": [
                _entity(q.title(), "ENT-SEARCH-1", "urn:entity:person"),
            ]},
        })
    if path == "/v2/tags":
        assert "term" not in params
        term = params["filter.query"]
        return httpx.Response(200, json={
            "success": True,
            "results": {"tags": [
                {"tag_id": f"urn:tag:mock:{term}", "name": term},
            ]},
        })
    if path == "/v2/insights":
        ftype = params.get("filter.type", "")
        n = int(params.get("take", "5"))
        ents = [
            _entity(f"Mock {ftype.split(':')[-1]} #{i+1}", f"ENT-{ftype.split(':')[-1].upper()}-{i+1}", ftype)
            for i in range(n)
        ]
        return httpx.Response(200, json={"success": True, "results": {"entities": ents}})
    return httpx.Response(404, json={"success": False})


# --------------------------------------------------------------------------- #
# Mock LLM: profiler seeds, then a plan that includes one hallucinated stop
# --------------------------------------------------------------------------- #

def llm_handler(request: httpx.Request) -> httpx.Response:
    body = json.loads(request.content.decode())
    user_text = body["messages"][1]["content"]
    if "User description of things they love" in user_text:
        content = json.dumps({
            "entities": [{"name": "Wes Anderson", "kind": "person"}],
            "keywords": ["ramen", "jazz"],
        })
    else:
        content = json.dumps({"stops": [
            {"slot": "lunch", "time": "12:30", "entity_id": "ENT-PLACE-1",
             "why": "Matches your ramen tag.", "tip": "Go early."},
            {"slot": "dinner", "time": "19:00", "entity_id": "FAKE-NOT-FROM-QLOO",
             "why": "The LLM invented this.", "tip": ""},
            {"slot": "cafe", "time": "15:00", "entity_id": "ENT-PLACE-1",
             "why": "Duplicate of lunch.", "tip": ""},
        ]})
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


def make_pipeline() -> TasteAtlasPipeline:
    qloo = QlooClient(
        api_key="test-key",
        transport=httpx.MockTransport(qloo_handler),
    )
    llm = LLMClient(
        api_key="test-key",
        transport=httpx.MockTransport(llm_handler),
    )
    return TasteAtlasPipeline(qloo, llm)


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #

def test_full_pipeline_end_to_end():
    pipe = make_pipeline()
    result = pipe.plan(city="Austin", likes="Wes Anderson films and ramen", occasion="foodie_day")

    assert result.city == "Austin"
    # Profiler resolved 1 entity + 2 tags
    assert result.profile.signal_entity_ids == ["ENT-SEARCH-1"]
    assert len(result.profile.signal_tag_ids) == 2
    # Critic: hallucinated + duplicate stops dropped, only the real one survives
    assert len(result.stops) == 1
    assert result.stops[0].entity_id == "ENT-PLACE-1"
    assert result.stops[0].name == "Mock place #1"
    reasons = " ".join(d["reason"] for d in result.dropped)
    assert "not in Qloo results" in reasons
    assert "duplicate" in reasons
    # Transparency log is populated
    assert any("/v2/insights" in c for c in result.qloo_calls)
    d = result.to_dict()
    assert d["stops"][0]["image_url"].startswith("https://")


def test_critic_drops_everything_unknown():
    critic = Critic()
    pools = {"lunch": [Candidate.from_qloo("lunch", _entity("Real", "ENT-1", "urn:entity:place"))]}
    stops, dropped = critic.run(
        [{"slot": "lunch", "time": "12:30", "entity_id": "NOPE", "why": "x", "tip": ""}],
        pools,
    )
    assert stops == [] and len(dropped) == 1


def test_qloo_client_401_hint():
    def h401(_req):
        return httpx.Response(401, text="unauthorized")

    client = QlooClient(api_key="bad", transport=httpx.MockTransport(h401))
    try:
        client.insights(filter_type="urn:entity:place")
    except Exception as exc:
        assert "hackathon.api.qloo.com" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected QlooError")


def test_api_plan_requires_keys():
    """Without env keys, POST /api/plan must fail with a clear message (not crash)."""
    from fastapi.testclient import TestClient
    from backend import main

    main.settings.QLOO_API_KEY = ""
    main.settings.LLM_API_KEY = ""
    tc = TestClient(main.app)
    r = tc.post("/api/plan", json={"city": "Austin", "likes": "ramen and jazz"})
    assert r.status_code == 400
    assert "QLOO_API_KEY" in r.json()["detail"]
    r = tc.get("/api/health")
    assert r.status_code == 200 and r.json()["ok"] is True


if __name__ == "__main__":
    test_full_pipeline_end_to_end()
    test_critic_drops_everything_unknown()
    test_qloo_client_401_hint()
    test_api_plan_requires_keys()
    print("All TasteAtlas self-tests passed.")
