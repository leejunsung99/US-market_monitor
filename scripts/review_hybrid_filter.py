"""Replay retained data through hybrid routing; never alter production state."""

import argparse
import hashlib
import json
import os
import sys
from collections import Counter
from dataclasses import asdict
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from us_market_monitor.config import Config  # noqa: E402
from us_market_monitor.database.models import Event  # noqa: E402
from us_market_monitor.database.repository import Repository  # noqa: E402
from us_market_monitor.notifications.telegram import Telegram  # noqa: E402
from us_market_monitor.processors.classifier import Classifier  # noqa: E402
from us_market_monitor.processors.hybrid_filter import (  # noqa: E402
    VERSION,
    judge_candidate,
    route_event,
)
from us_market_monitor.processors.normalizer import parse_time  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--judge-limit", type=int, default=0, choices=range(0, 11))
    parser.add_argument("--send-report", action="store_true")
    args = parser.parse_args()
    config = Config.load(root=ROOT)
    classifier = Classifier(config.watchlist, config.filters)
    model = os.getenv("OLLAMA_MODEL", "qwen3.5:9b")
    base = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11435")
    target = os.getenv("TELEGRAM_TEST_CHAT_ID", "").strip()
    if args.send_report and (not target or target == config.telegram_chat_id):
        parser.error("Separate test channel required")
    output = ROOT / "data/hybrid-filter"
    output.mkdir(exist_ok=True)
    repo = Repository(config.data_dir / "radar.sqlite3", readonly=True)
    try:
        records, events, counts = [], [], Counter()
        for row in repo.db.execute("SELECT * FROM events ORDER BY published_at DESC"):
            events.append((f"event:{row['id']}", repo.row_event(row)))
        # Review candidates retain only an excerpt, not the full collected article.
        for row in repo.db.execute("SELECT * FROM review_candidates ORDER BY published_at DESC"):
            event = Event(row["source"], row["observation_hash"], row["title"], row["evidence"],
                          row["source_url"], parse_time(row["published_at"]))
            events.append(("review:" + row["observation_hash"][:12], event))
        events.sort(key=lambda pair: pair[1].published_at, reverse=True)
        judged = 0
        for identity, event in events:
            route = route_event(event, classifier)
            counts[route.route] += 1
            record = {"id": identity, "title": event.title, "source": event.source,
                      "source_url": event.source_url, **asdict(route)}
            if route.route == "llm_review" and judged < args.judge_limit:
                fingerprint = hashlib.sha256(json.dumps(
                    [VERSION, model, event.title, event.summary, event.article_summary], ensure_ascii=False
                ).encode()).hexdigest()
                cache_path = output / (fingerprint + ".json")
                if cache_path.exists():
                    verdict = json.loads(cache_path.read_text(encoding="utf-8"))
                else:
                    print(json.dumps({"id": identity, "state": "judging"}), flush=True)
                    try:
                        verdict = judge_candidate(event, model, base)
                    except Exception as error:
                        # Timeout/invalid answer stays held; it never becomes a direct pass.
                        verdict = {"decision": "uncertain", "reason_ko": "모델 판정 실패로 보류",
                                   "evidence_ids": [], "error_type": type(error).__name__}
                    cache_path.write_text(json.dumps(verdict, ensure_ascii=False, indent=2), encoding="utf-8")
                record["judgment"] = verdict
                judged += 1
                print(json.dumps({"id": identity, **verdict}, ensure_ascii=True), flush=True)
            records.append(record)
        report = {"version": VERSION, "scope": "retained events and review excerpts; historical replay",
                  "counts": dict(counts), "judged": judged, "records": records}
        (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"counts": dict(counts), "judged": judged}), flush=True)
        if args.send_report:
            labels = {"alert": "알림 후보", "ignore": "제외", "uncertain": "보류"}
            lines = ["<b>[테스트] 중요도 3단계 분기 + 로컬 LLM</b>",
                     f"과거 보존 자료 재검토: 바로 통과 {counts['pass']} / LLM 검토 후보 {counts['llm_review']} / 제외 {counts['reject']}",
                     "본 채널 적용 전 시험 결과입니다."]
            for record in records:
                verdict = record.get("judgment")
                if verdict:
                    lines += ["", "<b>" + labels[verdict["decision"]] + "</b>",
                              escape(record["title"][:200]), escape(verdict["reason_ko"]),
                              f"처리 {verdict.get('seconds', 0):.1f}초 · 인용 근거 문장 {verdict['evidence_ids']}"]
            message = "\n".join(lines)
            if len(message) > 4096:
                raise ValueError("report_too_long")
            message_id = Telegram(config.telegram_token, target).send(message)
            with (output / "receipts.jsonl").open("a", encoding="utf-8") as receipt:
                receipt.write(json.dumps({"message_id": message_id, "judged": judged}) + "\n")
            print(json.dumps({"message_id": message_id}))
    finally:
        repo.close()


if __name__ == "__main__":
    main()
