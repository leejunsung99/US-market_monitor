"""Send the requested compact US-flag layout to the test channel only."""

import json
import os
import re
import sys
from decimal import Decimal
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from us_market_monitor.config import Config  # noqa: E402
from us_market_monitor.database.repository import Repository  # noqa: E402
from us_market_monitor.notifications.telegram import Telegram  # noqa: E402
from us_market_monitor.processors.ollama_preview import validate_result  # noqa: E402


def polish(text):
    text = re.sub(r"(\d)\s+(년|월|분기)", r"\1\2", text)
    text = re.sub(r"\)\s+(는|은|이|가)", r")\1", text)
    text = text.replace("26%만큼", "26%")
    text = re.sub(r"\$\s*(\d+(?:\.\d+)?)\s+billion",
                  lambda m: f"{Decimal(m[1]) * 10:g}억 달러", text)
    text = re.sub(r"\$(\d+(?:\.\d+)?)", r"\1달러", text)
    return escape(text)


def category_for(point):
    evidence = point["evidence"]
    if re.search(r"forecast|guidance|outlook|expects?|plans?|will\b", evidence, re.I):
        return "향후 전망"
    if re.search(r"revenue|income|profit|EPS|per share|cash flow|margin", evidence, re.I):
        return "주요 실적"
    if re.search(r"launch|introduc|unveil|production|deliver", evidence, re.I):
        return "사업 진행 상황"
    if re.search(r"agreement|acqui|contract|invest", evidence, re.I):
        return "주요 계약·투자"
    return "주요 내용"


def heading(text):
    return "<b><u>" + escape(text) + "</u></b>"


def main():
    config = Config.load(root=ROOT)
    target = os.getenv("TELEGRAM_TEST_CHAT_ID", "").strip()
    if not target or target == config.telegram_chat_id:
        raise ValueError("Separate test channel required")
    record = json.loads((ROOT / "data/ollama-preview/event-94.json").read_text(encoding="utf-8"))
    result = validate_result(record["result"], record["source_text"])
    repo = Repository(config.data_dir / "radar.sqlite3", readonly=True)
    try:
        url = repo.db.execute("SELECT source_url FROM events WHERE id=94").fetchone()[0]
    finally:
        repo.close()
    groups = {}
    for point in result["points"]:
        groups.setdefault(category_for(point), []).append(point["text_ko"])
    lines = [heading("🇺🇸메타(META) | 실적발표 요약🇺🇸"), "", heading("📍핵심내용"),
             "· " + polish(result["summary"]["text_ko"])]
    order = ["주요 실적", "주요 내용", "주요 계약·투자", "사업 진행 상황", "향후 전망"]
    for label in order:
        if groups.get(label):
            lines += ["", heading("📍" + label), *["· " + polish(p) for p in groups[label]]]
    if result["limitations_ko"]:
        lines += ["", heading("📍참고사항"), "· " + polish(result["limitations_ko"])]
    lines += ["", heading("📍원문 링크") + " : " + escape(url), "",
              heading("🇺🇸그로쓰리서치 U.S 채널[실시간 U.S 주요 알림]"), "https://t.me/growth_us"]
    message = "\n".join(lines)
    (ROOT / "data/compact-meta-preview.html").write_text(message, encoding="utf-8")
    message_id = Telegram(config.telegram_token, target).send(message)
    with (ROOT / "data/ollama-preview/receipts.jsonl").open("a", encoding="utf-8") as receipt:
        receipt.write(json.dumps({"event_id": 94, "template": "compact-us-categories-v2",
                                  "message_id": message_id}) + "\n")
    print(json.dumps({"message_id": message_id, "target": "test"}))


if __name__ == "__main__":
    main()
