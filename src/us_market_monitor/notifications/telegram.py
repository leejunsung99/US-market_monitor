import threading
import time

import requests

from us_market_monitor.http import Stopped, retry_delay


class DeliveryError(RuntimeError):
    def __init__(self, code, *, retryable=False, ambiguous=False, retry_after=0):
        super().__init__(code)
        self.retryable, self.ambiguous, self.retry_after = retryable, ambiguous, retry_after


class Telegram:
    def __init__(self, token, chat_id, *, timeout=20, stop=None):
        self._token, self._chat_id, self.timeout = token, chat_id, timeout
        self.stop = stop or threading.Event()
        self._next_send = 0.0

    def send(self, text):
        return self._request(
            "sendMessage",
            {"text": text, "parse_mode": "HTML", "link_preview_options": {"is_disabled": True}},
        )

    def send_article(self, event, *, test=False):
        from us_market_monitor.notifications.formatter import format_rich_article

        if event.event_type in {"economic_indicator", "monitor_alert"}:
            from us_market_monitor.notifications.formatter import format_event

            text = format_event(event)
            return self.send(("[테스트] " if test else "") + text)

        rich = format_rich_article(event, test=test)
        try:
            return self._request("sendRichMessage", {"rich_message": rich})
        except DeliveryError as error:
            if str(error) != "telegram_rejected_400":
                raise
        # A definitive rejection did not create a message. Retry without the
        # remote photo, then use text if the rich layout itself is rejected.
        if any(block["type"] == "photo" for block in rich["blocks"]):
            rich["blocks"] = [block for block in rich["blocks"] if block["type"] != "photo"]
            try:
                return self._request("sendRichMessage", {"rich_message": rich})
            except DeliveryError as error:
                if str(error) != "telegram_rejected_400":
                    raise
        from us_market_monitor.notifications.formatter import format_event

        return self.send(("[테스트] " if test else "") + format_event(event))

    def _request(self, method, payload):
        if self.stop.wait(max(0, self._next_send - time.monotonic())):
            raise Stopped()
        self._next_send = time.monotonic() + 3.1  # <=20/min to the same group/channel
        try:
            response = requests.post(
                f"https://api.telegram.org/bot{self._token}/{method}",
                json={"chat_id": self._chat_id, **payload},
                timeout=(self.timeout, self.timeout),
            )
        except requests.ConnectTimeout:
            raise DeliveryError("connection_timeout", retryable=True) from None
        except requests.RequestException:
            # No Bot API idempotency key: timeout may follow successful delivery.
            raise DeliveryError("delivery_outcome_unknown", ambiguous=True) from None
        try:
            payload = response.json()
        except ValueError:
            raise DeliveryError("invalid_telegram_response", ambiguous=True) from None
        if not isinstance(payload, dict):
            raise DeliveryError("invalid_telegram_response", ambiguous=True)
        if payload.get("ok") is True:
            if not isinstance(payload.get("result"), dict):
                raise DeliveryError("missing_result", ambiguous=True)
            message_id = payload["result"].get("message_id")
            if isinstance(message_id, int):
                return message_id
            raise DeliveryError("missing_message_id", ambiguous=True)
        code = payload.get("error_code", response.status_code)
        if code == 429:
            delay = retry_delay(payload.get("parameters", {}).get("retry_after"))
            delay = max(delay, retry_delay(response.headers.get("Retry-After")), 1)
            self._next_send = time.monotonic() + delay
            raise DeliveryError("telegram_rate_limited", retryable=True, retry_after=delay)
        if isinstance(code, int) and code >= 500:
            raise DeliveryError("telegram_server_outcome_unknown", ambiguous=True)
        raise DeliveryError(f"telegram_rejected_{code}")
