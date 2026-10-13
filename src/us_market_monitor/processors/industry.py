"""Extract attributable semiconductor events without inferring price moves or shipment success."""

import re


def mentions(alias, text):
    return bool(re.search(r"(?<!\w)" + re.escape(alias) + r"(?!\w)", text, re.I))


class IndustryRules:
    def __init__(self, watchlist, filters):
        self.companies = watchlist["companies"]
        self.actors = watchlist.get("industry_actors", {})
        self.filters = filters

    def assess(self, event, fragments):
        topic = self.filters.get("industry_topics", {}).get("semiconductor")
        if not topic:
            return None
        candidate = None
        for fragment in fragments:
            if not re.search(topic, fragment, re.I):
                continue
            # Hard uncertainty is checked on the evidence sentence, independently of
            # plans/roadmaps which are legitimate events only when explicitly announced.
            uncertain = any(
                re.search(p, fragment, re.I) for p in self.filters.get("hard_speculation", [])
            )
            actors = [
                (name, info)
                for name, info in self.actors.items()
                if any(mentions(alias, fragment) for alias in info["aliases"])
            ]
            named_companies = [
                ticker
                for ticker, info in self.companies.items()
                if any(mentions(alias, fragment) for alias in info["aliases"])
                or (len(ticker) >= 4 and mentions(ticker, fragment))
            ]
            action = None
            action_position = 0
            for rule in self.filters.get("industry_rules", []):
                if rule.get("stage") in {"launched", "mass_production"} and re.search(
                    r"\b(plans? to|will|intends? to|aims? to|expected to)\b|예정|계획",
                    fragment,
                    re.I,
                ):
                    continue
                matches = [
                    match
                    for pattern in rule["patterns"]
                    if (match := re.search(pattern, fragment, re.I))
                ]
                if matches:
                    action = rule
                    action_position = min(match.start() for match in matches)
                    break
            broad_action = re.search(
                r"\b(progress|breakthrough|plans?|roadmap|launch|mass production|denser)\b|진전|출시|양산",
                fragment,
                re.I,
            )
            if not action:
                if broad_action and (actors or named_companies):
                    candidate = {
                        "status": "review",
                        "reason": "announcement_not_attributed",
                        "evidence": fragment[:700],
                        "score": 2,
                    }
                continue
            if uncertain:
                candidate = {
                    "status": "review",
                    "reason": "uncertain_industry_claim",
                    "evidence": fragment[:700],
                    "score": 2,
                }
                continue
            if not actors and not named_companies:
                candidate = {
                    "status": "review",
                    "reason": "unknown_industry_actor",
                    "evidence": fragment[:700],
                    "score": 2,
                }
                continue
            # Avoid attributing an action to a company named only in another sentence.
            bound = []
            for name, info in [*actors, *((t, self.companies[t]) for t in named_companies)]:
                aliases = info["aliases"] + (
                    [name] if name in self.companies and len(name) >= 4 else []
                )
                for alias in aliases:
                    for match in re.finditer(
                        r"(?<!\w)" + re.escape(alias) + r"(?!\w)", fragment, re.I
                    ):
                        if 0 <= action_position - match.end() <= 100:
                            bound.append((match.end(), name, info))
            if not bound:
                candidate = {
                    "status": "review",
                    "reason": "action_actor_not_bound",
                    "evidence": fragment[:700],
                    "score": 2,
                }
                continue
            _, actor, info = max(bound, key=lambda item: item[0])
            impact = tuple(sorted(t for t in info.get("watch_tickers", []) if t in self.companies))
            if actor in self.actors and not impact and not named_companies:
                candidate = {
                    "status": "review",
                    "reason": "no_tracked_industry_relationship",
                    "evidence": fragment[:700],
                    "score": 2,
                }
                continue
            weights = self.filters.get("score_weights", {})
            parts = {
                "current_action": weights.get("current_action", 2),
                "relevant_actor": weights.get("relevant_actor", 1),
                "material_technology": weights.get("material_technology", 1),
                "attributed_announcement": weights.get("attributed_announcement", 1),
            }
            if event.metadata.get("official"):
                parts["official_source"] = weights.get("official_source", 1)
            score = sum(parts.values())
            family = (
                "nand"
                if re.search(r"\bNAND\b", fragment, re.I)
                else (
                    "hbm"
                    if re.search(r"\bHBM[234]?E?\b", fragment, re.I)
                    else (
                        "compute"
                        if re.search(r"\bGPU\b|AI accelerator", fragment, re.I)
                        else "dram"
                    )
                )
            )
            # A company-reported technology milestone is HIGH, never automatically
            # CRITICAL merely because price-drop language appears in a headline.
            return {
                "status": "approve" if score >= self.filters["high_threshold"] else "review",
                "reason": "confirmed_industry_action"
                if score >= self.filters["high_threshold"]
                else "below_importance_threshold",
                "score": score,
                "score_breakdown": parts,
                "evidence": fragment[:1000],
                "rule": action,
                "actor": actor,
                "family": family,
                "industry": info["industry"],
                "impact_tickers": impact,
                "confidence": "official_source"
                if event.metadata.get("official")
                else "attributed_report",
            }
        return candidate
