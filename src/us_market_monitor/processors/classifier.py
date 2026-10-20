import json
import re
from datetime import timedelta
from urllib.parse import urlsplit

from us_market_monitor.collectors.economic import LABELS, trusted_release
from us_market_monitor.database.models import Event, utcnow
from us_market_monitor.processors.earnings import EARNINGS_TYPES, earnings_facts
from us_market_monitor.processors.industry import IndustryRules
from us_market_monitor.processors.normalizer import canonical_url, digest, plain_text
from us_market_monitor.processors.outlook import industry_outlook


def any_match(patterns, text):
    return any(re.search(pattern, text, re.I) for pattern in patterns)


def headline_excluded(patterns, text):
    # Comparing a current result with last year does not make it historical.
    text = re.sub(
        r"\b(?:compared (?:with|to)|versus|vs\.?|from)\b.{0,100}?\b(?:a year ago|last year|last month)\b",
        "",
        text,
        flags=re.I,
    )
    return any_match(patterns, text)


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
        if event.source in {"BLS", "BEA", "Census", "DOL", "EIA"}:
            if not trusted_release(event):
                return self._decision(event, "reject", "invalid_economic_release")
            indicator = event.metadata["indicator"]
            event.event_type = "economic_indicator"
            event.priority = "HIGH"
            event.entity_key = "us:" + indicator + ":" + event.source_event_id
            event.industry = "미국 경제지표"
            event.actor = event.source
            event.event_stage = "released"
            event.evidence_level = "official_source"
            event.reason = LABELS[indicator] + " 공식 발표"
            event.importance_score = self.filters["high_threshold"]
            event.score_breakdown = {"official_economic_release": event.importance_score}
            return self._decision(event, "approve", "official_economic_release")
        official = event.metadata.get("official") and (
            event.source == "Federal Reserve"
            and urlsplit(event.source_url).hostname == "www.federalreserve.gov"
            or event.metadata.get("verified_official")
            and (event.source == "BIS" or event.source.startswith("Official/"))
        )
        # A newsroom's lifestyle story is not a market event. Check the headline
        # before reading body text, which can contain unrelated product boilerplate.
        if any_match(self.filters.get("lifestyle_headlines", []), plain_text(event.title)):
            return self._decision(event, "reject", "lifestyle_or_product_usage_story")
        # Broad coverage applies to policy releases. Company news still needs a
        # concrete material action even when the issuer is a verified source.
        if (
            official
            and not event.source.startswith("Official/")
            and self.filters.get("official_alert_scope", "material") == "all"
        ):
            if event.source == "Federal Reserve" and re.search(
                r"minutes of (?:the )?federal open market committee|FOMC minutes", event.title, re.I
            ):
                event.event_type, event.reason = "fomc_minutes", "FOMC 의사록 공식 발표"
            elif event.source == "Federal Reserve" and re.search(
                r"projection|economic projections", event.title, re.I
            ):
                event.event_type, event.reason = "fomc_projections", "연준 경제전망 공식 발표"
            else:
                matched = self._classify_legacy(event)
                if matched:
                    return self._decision(event, "approve", "verified_official_announcement")
                event.event_type, event.reason = "official_announcement", "공식 발표"
            event.priority = "HIGH"
            event.entity_key = event.metadata.get("issuer_ticker") or event.source
            event.evidence_level, event.event_stage = "official_source", "released"
            event.importance_score = self.filters["high_threshold"]
            event.industry = "공식 정책·기업 발표"
            return self._decision(event, "approve", "verified_official_announcement")
        headline, summary = plain_text(event.title), plain_text(event.summary)
        focus = headline + ". " + summary
        if any_match(self.filters.get("hard_exclusions", []), focus):
            return self._decision(event, "reject", "promotion_or_historical_content")
        if headline_excluded(self.filters.get("historical_headlines", []), headline):
            return self._decision(event, "reject", "historical_headline")
        if not event.metadata.get("official") and (
            re.search(
                self.filters.get("commentary_url_pattern", r"/(?:video|videos|podcast|podcasts)/"),
                urlsplit(event.source_url).path,
                re.I,
            )
            or any_match(self.filters.get("commentary_patterns", []), focus)
        ):
            return self._decision(event, "reject", "commentary_or_recap")
        fragments = [headline, *re.split(r"(?<=[.!?])\s+", summary)]
        if event.metadata.get("official"):
            fragments.extend(re.split(r"(?<=[.!?])\s+", event.body))
        fragments = [
            f
            for f in fragments
            if not headline_excluded(self.filters.get("historical_headlines", []), f)
        ]
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
            if event.event_type in EARNINGS_TYPES and not event.metadata.get("official"):
                facts = earnings_facts(event)
                if not facts["metrics"] and facts["guidance_status"] != "withdrawn":
                    return self._decision(
                        event, "review", "earnings_missing_reported_figures", evidence=focus[:700]
                    )
            return self._decision(
                event,
                "approve",
                "material_current_action",
                score=event.importance_score,
                score_breakdown=event.score_breakdown,
            )
        # Keep specific earnings, policy and emergency classifications ahead of
        # the broader trend fallback so their priority and deduplication survive.
        outlook = industry_outlook(
            event,
            fragments,
            {"companies": self.companies, "industry_actors": self.industry_rules.actors},
            self.filters,
        )
        if outlook:
            return self._decision(
                event, "approve", "material_industry_outlook", score=event.importance_score
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
        if headline_excluded(self.filters["exclusions"], headline):
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
        if headline_excluded(self.filters["exclusions"], headline):
            return None
        if any_match(self.filters["speculation"], headline):
            return None
        focus = f"{headline}. {plain_text(event.summary)}"
        text = focus
        if event.metadata.get("official"):
            text += " " + event.body
        tickers = (
            set(event.related_tickers)
            if event.source == "SEC EDGAR"
            or event.metadata.get("verified_official")
            and event.source.startswith("Official/")
            else set()
        )
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
        fragments = [
            f
            for f in fragments
            if not headline_excluded(self.filters.get("historical_headlines", []), f)
        ]
        for rule in self.filters["rules"]:
            for fragment in fragments:
                if any_match(self.filters["speculation"], fragment):
                    continue
                for pattern in rule["patterns"]:
                    result = re.search(pattern, fragment, re.I)
                    if result:
                        allow_untracked = rule["type"] in self.filters.get(
                            "untracked_event_types", []
                        )
                        if (
                            rule.get("evidence_actor_required")
                            and (tickers or not allow_untracked)
                            and not any(
                                re.search(r"(?<!\w)" + re.escape(alias) + r"(?!\w)", fragment, re.I)
                                for ticker, info in self.companies.items()
                                for alias in info["aliases"]
                                + ([ticker] if len(ticker) >= 4 else [])
                            )
                        ):
                            continue
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
        untracked = scope == "company" and not tickers
        if untracked and matched["type"] not in self.filters.get("untracked_event_types", []):
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
            "relevant_actor": weights.get("relevant_actor", 1)
            if tickers or scope != "company"
            else 0,
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
            else ",".join(event.related_tickers) or "market:" + digest(event.title.casefold())[:16]
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
            "글로벌 기업·산업"
            if scope == "company"
            else "글로벌 금융시장"
            if scope == "global"
            else "미국·글로벌 정책"
        )
        # Store only a short source quotation, never an inferred translation or full body.
        event.summary = evidence[:1000] or event.summary[:1000] or event.title
        return event
