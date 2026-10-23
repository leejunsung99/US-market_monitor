"""Optional standard Azure NMT translation after classification. Never changes source evidence."""

import logging
import re
import time
from collections import Counter
from html import escape

import requests

from us_market_monitor.processors.briefing import brief_points, translation_fingerprint
from us_market_monitor.processors.normalizer import plain_text

log = logging.getLogger(__name__)
MONTHS = {
    'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4, 'may': 5, 'jun': 6,
    'jul': 7, 'aug': 8, 'sep': 9, 'oct': 10, 'nov': 11, 'dec': 12,
}
MONTH_PATTERN = (r'\b(?:January|February|March|April|May|June|July|August|September|'
                 r'October|November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)'
                 r'\b\.?')


class Translator:
    def __init__(self, config):
        self.key = config.translator_key
        self.region = config.translator_region
        self.endpoint = config.translator_endpoint.rstrip("/")
        self.disabled_until = 0

    def translate(self, event):
        if event.event_type in {"economic_indicator", "monitor_alert"}:
            return False  # Economic facts use verified templates or official source excerpts.
        points = brief_points(event)
        texts = [event.title[:350], *points]
        fingerprint = translation_fingerprint(event)
        if event.translation_hash == fingerprint and event.title_ko and event.summary_ko:
            return True
        event.title_ko = event.summary_ko = event.translation_hash = ""
        if not points or not self.key or time.monotonic() < self.disabled_until:
            return False
        tickers = "|".join(
            re.escape(t)
            for t in sorted(
                set(event.related_tickers + event.impact_tickers), key=len, reverse=True
            )
        )
        pattern = (
            r"\b(?:8-K|6-K)(?:/A)?\b|(?<!\w)(?:(?:(?:US|NT|HK|A|C)?\$|[€£])\s*)?[−-]?\d[\d,]*(?:\.\d+)?"
            r"(?:\s*[-–—]\s*\d[\d,]*(?:\.\d+)?)?"
            r"(?:\s*(?:(?:billion|million|trillion|bn|percent|times|[xDBMKT])\b|[×%]))?"
        )
        # Protect date month names too: Oct. -> 10월 otherwise appears to add
        # another protected numeric value "10" to a line containing 10/22/2026.
        # Require a nearby numeric date to avoid interpreting the verb "may".
        month_date = MONTH_PATTERN + r'(?=\s+\d|\s*$)'
        pattern = r'\b\d{1,2}:\d{2}(?:\s*[ap]\.m\.)?|' + month_date + '|' + pattern
        if tickers:
            pattern += r"|(?<!\w)(?:" + tickers + r")(?!\w)"
        protected = []
        payload = []
        for text in texts:
            tokens, parts, start = [], [], 0
            for match in re.finditer(pattern, text):
                parts.extend(
                    [
                        escape(text[start : match.start()]),
                        '<span class="notranslate">' + escape(match[0]) + "</span>",
                    ]
                )
                tokens.append(match[0])
                start = match.end()
            parts.append(escape(text[start:]))
            payload.append({"Text": "<div>" + "".join(parts) + "</div>"})
            protected.append(tokens)
        headers = {"Ocp-Apim-Subscription-Key": self.key, "Content-Type": "application/json"}
        if self.region and self.region.lower() != "global":
            headers["Ocp-Apim-Subscription-Region"] = self.region
        try:
            batches, batch, size = [], [], 0
            for item in payload:
                item_size = len(item['Text'])
                if batch and (size + item_size > 40000 or len(batch) >= 100):
                    batches.append(batch)
                    batch, size = [], 0
                batch.append(item)
                size += item_size
            if batch:
                batches.append(batch)
            data = []
            for batch in batches:
                response = requests.post(
                    self.endpoint + "/translate",
                    params={"api-version": "3.0", "from": "en", "to": "ko", "textType": "html"},
                    headers=headers,
                    json=batch,
                    timeout=(5, 10),
                )
                if response.status_code != 200:
                    raise ValueError("provider_rejected")
                items = response.json()
                if not isinstance(items, list) or len(items) != len(batch):
                    raise ValueError("invalid_response")
                data.extend(items)
            if not isinstance(data, list) or len(data) != len(texts):
                raise ValueError("invalid_response")
            result = [plain_text(item["translations"][0]["text"]) for item in data]
            for text, tokens in zip(result, protected, strict=True):
                # Count non-overlapping matches, longest first. A value like
                # "2026" must not also be counted inside protected "2026,".
                alternatives = "|".join(
                    re.escape(token) for token in sorted(set(tokens), key=len, reverse=True)
                )
                actual = (
                    Counter(
                        re.findall(
                            r"(?<![A-Za-z0-9])(?:" + alternatives + r")(?![A-Za-z0-9])",
                            text,
                        )
                    )
                    if tokens
                    else Counter()
                )
                if not text or actual != Counter(tokens):
                    raise ValueError("protected_value_changed")
                if re.search(r'[A-Za-z]{3,}', text) and not re.search(r'[가-힣]', text):
                    raise ValueError('translation_not_korean')
        except (requests.RequestException, ValueError, KeyError, TypeError, IndexError) as error:
            self.disabled_until = time.monotonic() + 60
            reason = str(error) if isinstance(error, ValueError) else type(error).__name__
            # Never log provider response bodies, request URLs or credentials.
            if reason not in {"provider_rejected", "invalid_response", "protected_value_changed",
                              'translation_not_korean'}:
                reason = type(error).__name__
            log.warning("Translation failed (%s); Korean delivery must be held for retry", reason)
            return False
        result = [re.sub(month_date, lambda m: str(MONTHS[m[0][:3].lower()]) + '월',
                         text, flags=re.I) for text in result]
        event.title_ko, event.summary_ko = result[0], "\n".join(result[1:])
        event.translation_hash = fingerprint
        return True
