"""Detect material business trends from source evidence, independently of the watchlist."""

import re

from us_market_monitor.processors.industry import mentions
from us_market_monitor.processors.normalizer import digest


def industry_outlook(event, fragments, watchlist, filters):
    config = filters.get("industry_outlook", {})
    if not config.get("enabled", False):
        return None
    # An uncertain headline cannot become a confirmed event via unrelated background.
    uncertain = filters.get("hard_speculation", []) + filters.get("speculation", [])
    if any(re.search(p, event.title, re.I) for p in uncertain):
        return None
    for fragment in fragments:
        # Price performance alone does not establish a business trend. Exclude the
        # stock-move clause before looking for a metric, a change and material scale.
        for clause in re.split(r"[;；]|\bwhile\b|\bwhereas\b", fragment, flags=re.I):
            if re.search(r"\b(?:shares?|stock price)\b|주가", clause, re.I):
                continue
            if any(re.search(p, clause, re.I) for p in uncertain):
                continue
            years = re.findall(r"\b(?:in|during)\s+(?:Q[1-4]\s+)?(20\d{2})\b", clause, re.I)
            if any(int(year) < event.published_at.year for year in years):
                continue
            if re.search(
                r"\b(?:not|never|no)\b.{0,45}\b(?:slow\w*|decelerat\w*|declin\w*|"
                r"weaken\w*|compress\w*|drop\w*|fall\w*|increas\w*|decreas\w*|surge\w*|jump\w*)",
                clause,
                re.I,
            ):
                continue
            if not any(re.search(p, clause, re.I) for p in config.get("changes", [])):
                continue
            if not any(re.search(p, clause, re.I) for p in config.get("material_signals", [])):
                continue
            metric = next(
                (
                    name
                    for name, pattern in config.get("metrics", {}).items()
                    if re.search(pattern, clause, re.I)
                ),
                None,
            )
            if not metric:
                continue
            companies = watchlist["companies"]
            related = tuple(
                sorted(
                    ticker
                    for ticker, info in companies.items()
                    if any(mentions(alias, clause) for alias in info["aliases"])
                    or len(ticker) >= 4
                    and mentions(ticker, clause)
                )
            )
            actors = {
                name: info
                for name, info in watchlist.get("industry_actors", {}).items()
                if any(mentions(alias, clause) for alias in info["aliases"])
            }
            impact = tuple(
                sorted(
                    {
                        ticker
                        for info in actors.values()
                        for ticker in info.get("watch_tickers", [])
                        if ticker in companies and ticker not in related
                    }
                )
            )
            entity = ",".join(sorted(actors) + list(related)) or digest(event.title.casefold())[:16]
            score = filters.get("score_weights", {}).get("current_action", 2)
            if related or actors:
                score += filters.get("score_weights", {}).get("relevant_actor", 1)
            if score < filters["high_threshold"]:
                continue
            event.event_type = "industry_outlook"
            event.priority = "HIGH"
            event.reason = "산업 업황·성장·수익성 변화"
            event.entity_key = "outlook:" + entity + ":" + metric
            event.related_tickers, event.impact_tickers = related, impact
            event.actor = ",".join(sorted(actors) + list(related))
            event.industry = (
                " · ".join(
                    sorted(
                        {info["industry"] for info in actors.values()}
                        | {companies[ticker]["industry"] for ticker in related}
                    )
                )
                or "글로벌 기업·산업"
            )
            event.evidence_level = "reported_industry_change"
            event.event_stage = "reported_outlook"
            event.importance_score = score
            event.score_breakdown = {"reported_material_trend": score}
            event.summary = clause.strip()[:1000]
            return event
    return None
