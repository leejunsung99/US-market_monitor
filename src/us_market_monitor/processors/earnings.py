"""Source quality and comparable reported financial figures; no inferred consensus."""

import re
from decimal import Decimal

EARNINGS_TYPES = {"earnings", "earnings_guidance"}


def earnings_facts(event):
    text = f"{event.title}. {event.article_summary or event.summary}"
    if event.metadata.get("official"):
        text += ". " + event.body
    periods = re.findall(r"\bQ([1-4])\s*[-,]?\s*(20\d{2})\b", text, re.I)
    metrics = {}
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        context = (
            "guidance" if re.search(r"guidance|outlook|forecast", sentence, re.I) else "actual"
        )
        for name, label in {
            "revenue": r"\b(?:revenue|sales)\b",
            "eps": r"\b(?:EPS|earnings per share|profit per share)\b",
            "profit": r"\b(?:net income|net profit)\b",
        }.items():
            for match in re.finditer(label, sentence, re.I):
                nearby = sentence[match.end() : match.end() + 85]
                # Do not borrow a number belonging to a different financial metric.
                nearby = re.split(
                    r"\b(?:revenue|sales|EPS|net income|net profit|market capitalization|share price|stock price|consensus|versus|compared with|vs)\b",
                    nearby,
                    flags=re.I,
                )[0]
                numbers = re.findall(
                    r"(?:\$|USD\s*)(\d[\d,]*(?:\.\d+)?)\s*(trillion|billion|million|bn|b|m)?\b",
                    nearby,
                    re.I,
                )
                values = []
                for number, unit in numbers:
                    scale = {
                        "trillion": 10**12,
                        "billion": 10**9,
                        "bn": 10**9,
                        "b": 10**9,
                        "million": 10**6,
                        "m": 10**6,
                    }.get(unit.lower(), 1)
                    values.append(
                        format((Decimal(number.replace(",", "")) * scale).normalize(), "f")
                    )
                if values:
                    key = f"{context}:{name}"
                    metrics[key] = sorted(set(values + metrics.get(key, [])))
        for match in re.finditer(r"\$(\d[\d,]*(?:\.\d+)?)\s*(?:a|per)\s+share\b", sentence, re.I):
            metrics[f"{context}:eps"] = [
                format(Decimal(match[1].replace(",", "")).normalize(), "f")
            ]
    return {
        "periods": sorted({f"{year}-Q{quarter}" for quarter, year in periods}),
        "metrics": metrics,
        "guidance_status": "withdrawn"
        if re.search(
            r"\b(?:withdraws|withdrew|withdrawn).{0,35}(?:guidance|outlook|forecast)\b", text, re.I
        )
        else "",
    }


def earnings_update(new, old):
    if new.get("guidance_status") == "withdrawn" and old.get("guidance_status") != "withdrawn":
        return True
    new_metrics, old_metrics = new.get("metrics", {}), old.get("metrics", {})
    # A new reported figure qualifies; an action verb alone never does.
    if new_metrics.keys() - old_metrics.keys():
        return True
    return any(
        set(new_metrics[key]) - set(old_metrics[key])
        for key in new_metrics.keys() & old_metrics.keys()
    )
