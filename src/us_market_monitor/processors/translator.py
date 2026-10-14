"""Optional standard Azure NMT translation after classification. Never changes source evidence."""

import logging
import re
import time
from html import escape

import requests

from us_market_monitor.notifications.article import source_excerpt
from us_market_monitor.processors.normalizer import digest, plain_text

log = logging.getLogger(__name__)


class Translator:
    def __init__(self, config):
        self.key = config.translator_key
        self.region = config.translator_region
        self.endpoint = config.translator_endpoint.rstrip("/")
        self.disabled_until = 0

    def translate(self, event):
        texts = [
            event.title[:350],
            source_excerpt(event.article_summary or event.summary, event.title),
        ]
        fingerprint = digest("azure-nmt-ko-v1:" + "\n".join(texts))
        if event.translation_hash == fingerprint and event.title_ko and event.summary_ko:
            return True
        event.title_ko = event.summary_ko = event.translation_hash = ""
        if not self.key or time.monotonic() < self.disabled_until:
            return False
        tickers = "|".join(
            re.escape(t)
            for t in sorted(
                set(event.related_tickers + event.impact_tickers), key=len, reverse=True
            )
        )
        pattern = (
            r"(?:[$€£]\s*)?\d[\d,]*(?:\.\d+)?(?:\s*(?:billion|million|trillion|bn|percent|%))?"
        )
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
            response = requests.post(
                self.endpoint + "/translate",
                params={"api-version": "3.0", "from": "en", "to": "ko", "textType": "html"},
                headers=headers,
                json=payload,
                timeout=(5, 10),
            )
            if response.status_code != 200:
                raise ValueError("provider_rejected")
            data = response.json()
            if not isinstance(data, list) or len(data) != 2:
                raise ValueError("invalid_response")
            result = [plain_text(item["translations"][0]["text"]) for item in data]
            for text, tokens in zip(result, protected, strict=True):
                if not text or any(
                    text.count(token) < tokens.count(token) for token in set(tokens)
                ):
                    raise ValueError("protected_value_changed")
        except (requests.RequestException, ValueError, KeyError, TypeError, IndexError):
            self.disabled_until = time.monotonic() + 60
            log.warning("Translation unavailable or invalid; sending original English text")
            return False
        event.title_ko, event.summary_ko = result
        event.translation_hash = fingerprint
        return True
