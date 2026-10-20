import re
from difflib import SequenceMatcher

from us_market_monitor.processors.earnings import EARNINGS_TYPES, earnings_facts, earnings_update
from us_market_monitor.processors.normalizer import digest, normalized_title


def identities(event):
    # Finnhub IDs are provider-wide even when the publisher varies.
    namespace = "finnhub" if event.source.startswith("Finnhub/") else event.source
    return [
        digest(f"id:{namespace}:{event.source_event_id}"),
        digest(
            f"url:{event.source_url}"
            + (":" + event.source_event_id if event.event_type == "economic_indicator" else "")
        ),
        digest(f"title:{normalized_title(event.title)}:{event.published_at.date()}"),
    ]


def material_facts(event, filters):
    text = f"{event.title} {event.summary}".casefold()
    amounts = re.findall(
        r"(?:[$€£]\s*\d[\d,.]*(?:\s*(?:billion|million|trillion|bn|b))?|"
        r"\b\d[\d,.]*\s*(?:billion|trillion|million|percent|%))",
        text,
    )
    stages = [
        name
        for name, pattern in {
            "final": r"\b(finalizes|finalized|enacts|enacted|signs into law)\b|확정|시행",
            "expanded": r"\b(expands|expanded|doubles)\b|확대",
            "raised": r"\b(raises|raised|boosts|boosted)\b|상향",
            "cut": r"\b(cuts|cut|slashes|slashed|lowers|lowered)\b|하향",
            "withdrawn": r"\b(withdraws|withdrawn)\b|철회",
            "amended": r"\b(amends|amended|revises|revised)\b|수정|정정",
        }.items()
        if re.search(pattern, text)
    ]
    update_signal = any(
        re.search(pattern, text, re.I) for pattern in filters["material_update_patterns"]
    )
    event_stage = getattr(event, "event_stage", "")
    if event_stage:
        stages.append(event_stage)
        update_signal = event_stage in {"announced_plan", "launched", "mass_production"}
    facts = {
        "amounts": sorted(set(amounts)),
        "stages": sorted(stages),
        "update_signal": update_signal,
    }
    if event.event_type in EARNINGS_TYPES:
        facts["earnings"] = earnings_facts(event)
    return facts


def meaningful_update(new_facts, old_facts):
    if "earnings" in new_facts:
        return earnings_update(new_facts["earnings"], old_facts.get("earnings", {}))
    # Newly reported detail alone is insufficient: require a changed action/stage,
    # or explicit revision plus two independently supplied, different quantities.
    if not new_facts.get("update_signal"):
        return False
    new_stages, old_stages = set(new_facts["stages"]), set(old_facts["stages"])
    industry_order = {
        "technical_progress": 1,
        "announced_plan": 2,
        "launched": 3,
        "mass_production": 4,
    }
    if new_stages & industry_order.keys() or old_stages & industry_order.keys():
        return max((industry_order.get(stage, 0) for stage in new_stages), default=0) > max(
            (industry_order.get(stage, 0) for stage in old_stages), default=0
        )
    if new_stages - old_stages:
        return bool(new_stages & {"final", "expanded", "raised", "cut", "withdrawn", "amended"})
    return (
        bool(new_stages & {"amended", "raised", "cut", "expanded"})
        and bool(new_facts["amounts"] and old_facts["amounts"])
        and new_facts["amounts"] != old_facts["amounts"]
    )


def same_story(event, row, filters):
    if event.event_type in EARNINGS_TYPES and row["event_type"] in EARNINGS_TYPES:
        if not set(event.entity_key.split(",")).intersection(row["entity_key"].split(",")):
            return False
        import json

        old = json.loads(row["material_facts"]).get("earnings", {})
        new = earnings_facts(event)
        if (
            old.get("periods")
            and new["periods"]
            and not set(old["periods"]).intersection(new["periods"])
        ):
            return False
        return True
    if event.event_type != row["event_type"]:
        return False
    old_entities = set(row["entity_key"].split(","))
    new_entities = set(event.entity_key.split(","))
    policy_type = event.event_type in {"export_restriction", "tariff_policy"}
    if policy_type:
        old_base, _, old_scope = row["entity_key"].partition(":")
        new_base, _, new_scope = event.entity_key.partition(":")
        if old_base != new_base or (old_scope and new_scope and old_scope != new_scope):
            return False
    elif not new_entities.intersection(old_entities):
        return False
    a, b = normalized_title(event.title), normalized_title(row["title"])
    similarity = SequenceMatcher(None, a, b).ratio()

    def semantic_tokens(title):
        stop = {
            "the",
            "a",
            "an",
            "to",
            "for",
            "in",
            "of",
            "and",
            "will",
            "agrees",
            "announces",
            "reports",
            "signs",
            "says",
            "new",
            "major",
        }
        synonyms = {
            "acquires": "acquire",
            "acquisition": "acquire",
            "buys": "acquire",
            "buy": "acquire",
            "purchase": "acquire",
            "agreement": "deal",
            "contract": "deal",
            "billion": "bn",
        }
        return {synonyms.get(token, token) for token in title.split() if token not in stop}

    left, right = semantic_tokens(a), semantic_tokens(b)
    semantic = len(left & right) / max(1, len(left | right))
    # A single issuer+event type within a short event window also covers paraphrases
    # and SEC-vs-news duplicates. This deliberately prefers suppressing noise.
    narrow = event.event_type in {
        "earnings",
        "earnings_guidance",
        "monetary_policy",
        "export_restriction",
        "tariff_policy",
        "emergency_policy",
        "production_disruption",
        "semiconductor_technology",
    }
    if policy_type:
        # A missing jurisdiction cannot safely imply two distinct country policies are one.
        narrow = bool(old_scope and new_scope and old_scope == new_scope)
    return narrow or similarity >= filters["dedup_similarity"] or semantic >= 0.6
