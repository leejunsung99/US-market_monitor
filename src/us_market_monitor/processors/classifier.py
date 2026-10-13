import json
import re
from datetime import timedelta

from us_market_monitor.database.models import Event, utcnow
from us_market_monitor.processors.industry import IndustryRules
from us_market_monitor.processors.normalizer import canonical_url, digest, plain_text


def any_match(patterns, text):
    return any(re.search(pattern, text, re.I) for pattern in patterns)


class Classifier:
    """Conservative, auditable event evidence; no headline sentiment or fabricated consensus."""

    def __init__(self, watchlist, filters):
        self.companies = watchlist["companies"]
        self.filters = filters
        self.industry_rules = IndustryRules(watchlist, filters)
        signature = json.dumps({"filters": filters, "watchlist": watchlist}, sort_keys=True)
        self.version = str(filters.get("rules_version", "legacy")) + ":" + digest(signature)[:12]

    def prepare(self, event):
        event.metadata["rules_version"] = self.version
        event.metadata["review_retention_days"] = self.filters.get("review_retention_days", 7)
        event.metadata["review_max_rows"] = self.filters.get("review_max_rows", 500)

    def _decision(self, event, status, reason, **details):
        event.metadata["classification_decision"] = {
            "status": status,
            "reason": reason,
            "rules_version": self.version,
            **details,
        }
        return event if status == "approve" else None

    def classify(self, event: Event) -> Event | None:
        if not event.article_summary:
            event.article_summary = event.summary[:1500]
        self.prepare(event)
        if not event.title.strip() or not event.source.strip():
            return self._decision(event, "reject", "missing_source_metadata")
        try:
            event.source_url = canonical_url(event.source_url)
        except ValueError:
            return self._decision(event, "reject", "invalid_source_url")
        if event.published_at > utcnow() + timedelta(minutes=5):
            return self._decision(event, "reject", "future_publication")
        headline, summary = plain_text(event.title), plain_text(event.summary)
        focus = headline + ". " + summary
        if any_match(self.filters.get("hard_exclusions", []), focus):
            return self._decision(event, "reject", "promotion_or_historical_content")
        fragments = [headline, *re.split(r"(?<=[.!?])\s+", summary)]
        if event.metadata.get("official"):
            fragments.extend(re.split(r"(?<=[.!?])\s+", event.body))
        industry = self.industry_rules.assess(event, fragments)
        if industry and industry["status"] == "approve":
            event.priority = "HIGH"
            event.event_type = industry["rule"]["type"]
            event.reason = industry["rule"]["label"]
            event.event_stage = industry["rule"]["stage"]
            event.evidence_level = industry["confidence"]
            event.actor = industry["actor"]
            event.impact_tickers = industry["impact_tickers"]
            event.importance_score = industry["score"]
            event.score_breakdown = industry["score_breakdown"]
            event.industry = industry["industry"]
            event.entity_key = event.actor + ":" + industry["family"]
            # Relationship targets are never mislabeled as mentioned source tickers.
            evidence_text = industry["evidence"]
            event.related_tickers = tuple(
                sorted(
                    t
                    for t, info in self.companies.items()
                    if any(
                        re.search(r"(?<!\w)" + re.escape(alias) + r"(?!\w)", evidence_text, re.I)
                        for alias in info["aliases"]
                    )
                    or (
                        len(t) >= 4
                        and re.search(r"(?<!\w)" + re.escape(t) + r"(?!\w)", evidence_text, re.I)
                    )
                )
            )
            event.summary = industry["evidence"]
            return self._decision(
                event,
                "approve",
                industry["reason"],
                score=event.importance_score,
                stage=event.event_stage,
                score_breakdown=event.score_breakdown,
            )
        approved = self._classify_legacy(event)
        if approved:
            return self._decision(
                event,
                "approve",
                "material_current_action",
                score=event.importance_score,
                score_breakdown=event.score_breakdown,
            )
        if industry:
            return self._decision(
                event,
                "review",
                industry["reason"],
                score=industry["score"],
                evidence=industry["evidence"],
            )
        reason = "no_material_action_or_tracked_actor"
        if any_match(self.filters["exclusions"], headline):
            reason = "routine_headline"
        elif any_match(self.filters["speculation"], headline):
            reason = "speculative_headline"
        return self._decision(event, "reject", reason)

    def _classify_legacy(self, event: Event) -> Event | None:
        if not event.title.strip() or not event.source.strip():
            return None
        try:
            event.source_url = canonical_url(event.source_url)
        except ValueError:
            return None
        if event.published_at > utcnow() + timedelta(minutes=5):
            return None
        headline = plain_text(event.title)
        if any_match(self.filters["exclusions"], headline):
            return None
        if any_match(self.filters["speculation"], headline):
            return None
        focus = f"{headline}. {plain_text(event.summary)}"
        text = focus
        if event.metadata.get("official"):
            text += " " + event.body
        tickers = set(event.related_tickers) if event.source == "SEC EDGAR" else set()
        for ticker, info in self.companies.items():
            # Short ambiguous tickers (MS, MU, GS) are not recognized as ordinary words.
            aliases = info["aliases"] + ([ticker] if len(ticker) >= 4 else [])
            if any(
                re.search(r"(?<!\w)" + re.escape(alias) + r"(?!\w)", focus, re.I)
                for alias in aliases
            ):
                tickers.add(ticker)
        matched, evidence = None, ""
        # Judge the sentence containing the action, not an unrelated headline.
        # Forward-looking boilerplate elsewhere must not negate a confirmed release.
        fragments = [headline, *re.split(r"(?<=[.!?])\s+", plain_text(event.summary))]
        if event.metadata.get("official"):
            fragments.extend(re.split(r"(?<=[.!?])\s+", event.body))
        for rule in self.filters["rules"]:
            for fragment in fragments:
                if any_match(self.filters["speculation"], fragment):
                    continue
                for pattern in rule["patterns"]:
                    result = re.search(pattern, fragment, re.I)
                    if result:
                        matched = rule
                        evidence = fragment[
                            max(0, result.start() - 80) : result.end() + 220
                        ].strip()
                        break
                if matched:
                    break
            if matched:
                break
        # SEC Item 2.02 explicitly identifies released financial results, but not a surprise.
        if (
            not matched
            and event.source == "SEC EDGAR"
            and "2.02" in event.metadata.get("items", [])
        ):
            if re.search(r"press release|financial results|earnings", event.body, re.I):
                matched = next((r for r in self.filters["rules"] if r["type"] == "earnings"), None)
                evidence = event.summary[:600]
        if not matched:
            return None
        if matched["type"] in {"acquisition", "contract_investment"}:
            scales = {
                "million": 1e6,
                "m": 1e6,
                "billion": 1e9,
                "bn": 1e9,
                "b": 1e9,
                "trillion": 1e12,
            }
            amounts = [
                float(amount.replace(",", "")) * scales[unit.lower()]
                for amount, unit in re.findall(
                    r"(?:\$|USD\s*)\s*(\d[\d,]*(?:\.\d+)?)\s*(trillion|billion|million|bn|b|m)\b",
                    focus,
                    re.I,
                )
            ]
            large = any(amount >= self.filters["minimum_deal_usd"] for amount in amounts)
            if not large and not any_match(self.filters["material_scale_patterns"], focus):
                return None
        scope = matched.get("scope", "company")
        if scope == "company" and not tickers:
            # Outside-watchlist exceptions require explicit systemic-scale evidence.
            # Unknown actors are not invented or assigned an arbitrary ticker.
            if not re.search(
                r"\b(world.s largest|systemically important|global.{0,20}crisis)\b", focus, re.I
            ):
                return None
        if scope == "policy":
            authority = re.search(
                r"\b(federal reserve|fomc|fed|government|president|white house|commerce "
                r"department|treasury|congress|bis|china|beijing|european union|eu)\b|연준|정부",
                text,
                re.I,
            )
            if not authority:
                return None
        # A current action pattern is mandatory; specificity and source establish importance.
        weights = self.filters.get("score_weights", {})
        parts = {
            "current_action": weights.get("current_action", 2),
            "relevant_actor": weights.get("relevant_actor", 1),
            "material_event": weights.get("material_technology", 1),
        }
        if event.metadata.get("official"):
            parts["official_source"] = weights.get("official_source", 1)
        critical = any_match(self.filters["critical_patterns"], focus)
        if matched["type"] in {"emergency_policy", "geopolitical"}:
            critical = True
        if matched["type"] == "export_restriction" and re.search(
            r"semiconductor|\bchips?\b|\bAI\b|반도체", focus, re.I
        ):
            critical = True
        if critical:
            parts["systemic_urgency"] = weights.get("systemic_urgency", 3)
        score = sum(parts.values())
        if score < self.filters["high_threshold"]:
            return None
        event.priority = "CRITICAL" if score >= self.filters["critical_threshold"] else "HIGH"
        event.importance_score = score
        event.score_breakdown = parts
        event.evidence_level = (
            "official_source" if event.metadata.get("official") else "reported_action"
        )
        event.event_type = matched["type"]
        event.reason = matched["label"]
        event.related_tickers = tuple(sorted(tickers))
        event.entity_key = (
            matched.get("entity", "")
            if scope != "company"
            else ",".join(event.related_tickers) or "systemic_company"
        )
        if matched["type"] in {"export_restriction", "tariff_policy"}:
            jurisdictions = [
                country
                for country in (
                    "china",
                    "canada",
                    "mexico",
                    "iran",
                    "russia",
                    "india",
                    "japan",
                    "taiwan",
                    "south korea",
                    "european union",
                )
                if re.search(r"\b" + re.escape(country) + r"\b", focus, re.I)
            ]
            if jurisdictions:
                event.entity_key += ":" + "|".join(sorted(jurisdictions))
        industries = sorted({self.companies[t]["industry"] for t in tickers if t in self.companies})
        event.industry = " · ".join(industries) or (
            "글로벌 금융시장" if scope == "global" else "미국·글로벌 정책"
        )
        # Store only a short source quotation, never an inferred translation or full body.
        event.summary = evidence[:1000] or event.summary[:1000] or event.title
        return event
