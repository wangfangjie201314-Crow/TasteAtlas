"""CLI smoke test: run the full TasteAtlas pipeline once and print the plan.

Usage:
    cp .env.example .env   # then fill in QLOO_API_KEY and LLM_API_KEY
    .venv/bin/python demo.py "Austin" "Wes Anderson films, Miles Davis, ramen" foodie_day
"""
from __future__ import annotations

import sys

from backend.agents import TasteAtlasPipeline
from backend.config import settings
from backend.llm_client import LLMClient
from backend.qloo_client import QlooClient, QlooError


def main() -> int:
    city = sys.argv[1] if len(sys.argv) > 1 else "Austin"
    likes = sys.argv[2] if len(sys.argv) > 2 else "Wes Anderson films, Miles Davis, ramen"
    occasion = sys.argv[3] if len(sys.argv) > 3 else "foodie_day"

    try:
        qloo = QlooClient(settings.QLOO_API_KEY, settings.QLOO_BASE_URL, settings.REQUEST_TIMEOUT_S)
        llm = LLMClient(settings.LLM_API_KEY, settings.LLM_BASE_URL, settings.LLM_MODEL)
    except (QlooError, RuntimeError, ValueError) as e:
        print(f"[demo] 无法启动: {e}")
        print("[demo] 请复制 .env.example 为 .env 并填入 QLOO_API_KEY 和 LLM_API_KEY（步骤见 ../SUBMIT.md Step 0）。")
        return 2
    try:
        result = TasteAtlasPipeline(qloo, llm).plan(city, likes, occasion)
    finally:
        qloo.close()
        llm.close()

    print(f"\n=== Your day in {result.city} ({result.occasion}) ===")
    print(f"Taste profile: {result.profile.summary()}\n")
    for s in result.stops:
        print(f"  {s.time}  {s.name}  [{s.kind.replace('urn:entity:', '')}]")
        print(f"         Why: {s.why}")
        if s.tip:
            print(f"         Tip: {s.tip}")
        print()
    if result.dropped:
        print(f"Critic dropped {len(result.dropped)} stop(s):")
        for d in result.dropped:
            print(f"  - [{d['slot']}] {d['reason']}")
    print(f"\nQloo API calls made: {len(result.qloo_calls)}")
    for c in result.qloo_calls:
        print(f"  - {c}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
