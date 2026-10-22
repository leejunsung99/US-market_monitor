"""Generate local LLM previews from retained events; send reviewed output to test only."""

import argparse
import hashlib
import json
import os
import re
import sys
from html import escape
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from us_market_monitor.config import Config  # noqa: E402
from us_market_monitor.database.repository import Repository  # noqa: E402
from us_market_monitor.notifications.preview_layout import company_label  # noqa: E402
from us_market_monitor.notifications.telegram import Telegram  # noqa: E402
from us_market_monitor.processors.ollama_preview import (  # noqa: E402
    PROMPT_VERSION,
    OllamaPreview,
    validate_result,
)


def render(result, event, model, seconds, companies=None):
    def polish(text):
        text = re.sub(r"(\d)\s+(월|년|분기|대)(?=\s|$|[가-힣])", r"\1\2", text)
        text = re.sub(r"(\d+(?:\.\d+)?)\s+percent\b", r"\1%", text)
        if "seasonally adjusted annual rate" in event.summary:
            text = text.replace("계절 조정 연평균", "계절조정 연율").replace("계절 조정 연율", "계절조정 연율")
            text = text.replace("사설 소유", "민간")
        text = text.replace("자유 현금 흐름", "잉여현금흐름").replace("관리층", "경영진")
        return escape(text)

    title = result["title_ko"]
    if "earnings" in event.title.lower() or "2.02" in event.metadata.get("items", []):
        issuer = company_label(event, companies or {})
        title = (issuer + " | " if issuer else "") + "실적 요약"
    elif event.source.startswith("Finnhub/"):
        issuer = company_label(event, companies or {})
        title = (issuer + " | " if issuer else "") + "기사 핵심 요약"
    lines = ["<b>[로컬 LLM 테스트] " + polish(title) + "</b>", "",
             "<b>핵심 요약</b>", polish(result["summary"]["text_ko"]), "", "<b>주요 내용</b>"]
    lines += ["• " + polish(p["text_ko"]) for p in result["points"]]
    if result["limitations_ko"]:
        lines += ["", "※ " + polish(result["limitations_ko"])]
    if event.source.startswith("Finnhub/"):
        lines += ["", "※ 기사에 보도된 내용 기준"]
    publication = event.published_at.astimezone(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d %H:%M KST")
    if event.metadata.get("publication_precision") == "date":
        publication = event.metadata["publication_date"] + " (시각 미제공)"
    lines += ["", escape(event.source) + " · " + escape(publication),
              '<a href="' + escape(event.source_url, quote=True) + '">원문 보기</a>',
              "", escape(model) + f" · 생성 {seconds:.1f}초 · 원문 근거/숫자 검사 통과"]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event-ids", type=int, nargs="+", required=True)
    parser.add_argument("--send-existing", action="store_true")
    args = parser.parse_args()
    config = Config.load(root=ROOT)
    model = os.getenv("OLLAMA_MODEL", "qwen3.5:9b")
    llm = OllamaPreview(model, os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434"))
    output = ROOT / "data" / "ollama-preview"
    output.mkdir(exist_ok=True)
    target = os.getenv("TELEGRAM_TEST_CHAT_ID", "").strip()
    if args.send_existing and (not target or target == config.telegram_chat_id):
        parser.error("A separate test channel is required")
    telegram = Telegram(config.telegram_token, target) if args.send_existing else None
    repo = Repository(config.data_dir / "radar.sqlite3", readonly=True)
    try:
        for event_id in args.event_ids:
            row = repo.db.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
            if row is None:
                raise ValueError("event_not_found")
            event = repo.row_event(row)
            source = event.body or event.article_summary or event.summary
            # Retain the complete statistical paragraph, including revision/error band.
            if event.event_type == "economic_indicator":
                source = event.summary
            source = event.title + "\n" + source
            key = hashlib.sha256((PROMPT_VERSION + model + event.title + source).encode()).hexdigest()
            path = output / f"event-{event_id}.json"
            if args.send_existing:
                record = json.loads(path.read_text(encoding="utf-8"))
                recorded_key = hashlib.sha256((record["prompt_version"] + model + event.title + source).encode()).hexdigest()
                if record["input_hash"] != recorded_key:
                    raise ValueError("stale_preview")
                validate_result(record["result"], source)
            else:
                print(json.dumps({"event_id": event_id, "state": "generating", "model": model}), flush=True)
                category = "news"
                if event.event_type == "economic_indicator":
                    category = "economic"
                elif event.event_type == "earnings" or "2.02" in event.metadata.get("items", []) or "earnings" in event.title.lower():
                    category = "earnings"
                try:
                    result, stats = llm.summarize(source, {"title": event.title, "source": event.source,
                                                          "tickers": event.related_tickers,
                                                          "category": category})
                except ValueError as error:
                    failed = {"event_id": event_id, "error": str(error), "source_text": source,
                              "result": getattr(error, "result", None), "stats": getattr(error, "stats", None)}
                    (output / f"failed-{event_id}.json").write_text(
                        json.dumps(failed, ensure_ascii=False, indent=2), encoding="utf-8")
                    raise
                record = {"event_id": event_id, "input_hash": key, "source_text": source,
                          "result": result, "stats": stats, "prompt_version": PROMPT_VERSION}
                path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
            message = render(record["result"], event, model, record["stats"]["wall_seconds"],
                             config.watchlist["companies"])
            (output / f"event-{event_id}.html").write_text(message, encoding="utf-8")
            if len(message) > 4096:
                raise ValueError("message_too_long")
            status = {"event_id": event_id, "seconds": record["stats"]["wall_seconds"], "validated": True}
            if telegram:
                message_id = telegram.send(message)
                status["message_id"] = message_id
                with (output / "receipts.jsonl").open("a", encoding="utf-8") as log:
                    log.write(json.dumps(status) + "\n")
            print(json.dumps(status), flush=True)
    finally:
        repo.close()


if __name__ == "__main__":
    main()
