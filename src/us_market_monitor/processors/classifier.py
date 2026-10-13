import re
from datetime import timedelta

from us_market_monitor.database.models import Event, utcnow
from us_market_monitor.processors.normalizer import canonical_url, plain_text


def any_match(patterns, text):
    return any(re.search(pattern, text, re.I) for pattern in patterns)


class Classifier:
    """Conservative, auditable event evidence; no headline sentiment or fabricated consensus."""

    def __init__(self, watchlist, filters):
        self.companies = watchlist["companies"]
        self.filters = filters

    def classify(self, event: Event) -> Event | None:
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
        for rule in self.filters["rules"]:
            for pattern in rule["patterns"]:
                result = re.search(pattern, text, re.I)
                if result:
                    matched = rule
                    evidence = text[max(0, result.start() - 80) : result.end() + 220].strip()
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
        score = 4
        if event.metadata.get("official"):
            score += 1
        critical = any_match(self.filters["critical_patterns"], focus)
        if matched["type"] in {"emergency_policy", "geopolitical"}:
            critical = True
        if matched["type"] == "export_restriction" and re.search(
            r"semiconductor|\bchips?\b|\bAI\b|반도체", focus, re.I
        ):
            critical = True
        if critical:
            score += 3
        if score < self.filters["high_threshold"]:
            return None
        event.priority = "CRITICAL" if score >= self.filters["critical_threshold"] else "HIGH"
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
