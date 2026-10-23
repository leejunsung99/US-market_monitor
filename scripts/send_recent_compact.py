"""Replay the latest five sent alerts in the approved layout, to an explicitly selected channel."""

import argparse
import json
import os
import re
import sys
import time
from html import escape
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from send_compact_preview import heading, polish  # noqa: E402

from us_market_monitor.config import Config  # noqa: E402
from us_market_monitor.database.repository import Repository  # noqa: E402
from us_market_monitor.notifications.compact import bullet_lines  # noqa: E402
from us_market_monitor.notifications.telegram import DeliveryError, Telegram  # noqa: E402
from us_market_monitor.processors.ollama_preview import validate_result  # noqa: E402


def format_message(title, sections, url):
    lines = [heading("🇺🇸" + title + "🇺🇸")]
    for label, texts in sections.items():
        if texts:
            from html import unescape

            lines += ["", heading("📍" + label), *bullet_lines([unescape(polish(t)) for t in texts])]
    lines += ["", heading("📍원문 링크") + " : " + escape(url), "",
              heading("🇺🇸그로쓰리서치 U.S 채널[실시간 U.S 주요 알림]"), "https://t.me/growth_us"]
    return "\n".join(lines)


def build_preview(event, event_id):
    text = event.article_summary
    photo = None
    if event_id == 94:
        record = json.loads((ROOT / "data/ollama-preview/event-94.json").read_text(encoding="utf-8"))
        validate_result(record["result"], record["source_text"])
        for fragment in ["revenue growth of 26%", "nearly $10 billion legal charge",
                         "Net income was $9.9 billion, or $3.83 per share", "$73 billion to $76 billion"]:
            if fragment not in record["source_text"]:
                raise ValueError("Meta article evidence changed")
        sections = {
            "핵심내용": ["2026년 3분기 매출 성장과 비용 급증",
                       "광고 수요·사용자 참여 증가 및 AI 개선 효과"],
            "주요 실적": ["매출 증가율: +26%",
                       "순이익: 약 99억 달러", "주당순이익(EPS): 3.83달러",
                       "법률 비용: 약 100억 달러"],
            "경영진 발언": ["Muse AI 에이전트 채택 확대 강조"],
            "향후 전망": ["4분기 매출 전망: 730억~760억 달러"],
        }
        for fragment in ["rapid adoption of its Muse AI agent", "commerce fees, subscriptions, APIs and business services"]:
            if fragment not in record["source_text"]:
                raise ValueError("Missing earnings-call evidence")
        sections["향후 전망"].append("커머스 수수료·구독·API·기업 서비스를 통한 수익화 구상")
        title = "메타(META) | 어닝콜 관련 보도"
    elif event_id == 93:
        if "revenue flattened out in the third quarter" not in text or "launched robotaxi service and humanoid production" not in text:
            raise ValueError("Tesla article evidence changed")
        title = "테슬라(TSLA) | 실적 관련 보도"
        sections = {
            "핵심내용": ["3분기 매출 정체 및 예상보다 적은 지출 보도"],
            "사업 진행 상황": ["로보택시 서비스 개시 보도", "휴머노이드 생산 개시 보도"],
        }
    elif event_id == 92:
        from bs4 import BeautifulSoup
        from preview_tesla_earnings import build_message

        raw = (ROOT / "data/tesla-q3-2026-source.html").read_text(encoding="utf-8")
        verified = build_message(BeautifulSoup(raw, "html.parser").get_text(" ", strip=True))
        # Reuse the period/unit-checked original SEC table extraction.
        def line(label):
            match = re.search(r"• " + re.escape(label) + r": (.+)", verified)
            if not match:
                raise ValueError("Missing verified Tesla metric")
            return re.sub(r"</?b>", "", match[1])

        title = "테슬라(TSLA) | 실적발표 요약"
        sections = {
            "핵심내용": ["2026년 3분기 매출 정체·영업이익 감소"],
            "주요 실적": ["매출: " + line("매출"), "영업이익: " + line("영업이익"),
                       "순이익(GAAP): " + line("순이익 · GAAP"), "잉여현금흐름: " + line("잉여현금흐름(FCF)")],
        }
        photo = ROOT / "data/exhibit9914004.jpg"
    elif event_id == 91:
        for figure in ["GAAP diluted EPS of $2.19", "non-GAAP diluted EPS of $2.20", "52.4%", "40.2%"]:
            if figure not in text:
                raise ValueError("Lam financial evidence changed")
        title = "램리서치(LRCX) | 실적발표 요약"
        sections = {
            "핵심내용": ["2026년 9월 27일 종료 분기 실적 공개"],
            "주요 실적": ["희석 EPS(GAAP): 2.19달러",
                       "조정 희석 EPS(non-GAAP): 2.20달러",
                       "조정 매출총이익률: 52.4%", "조정 영업이익률: 40.2%"],
        }
    elif event_id == 90:
        for fragment in ["Revenue $ 64,601 $ 51,242 26", "Net income $ 9,860 $ 2,709 264", "2026 2025", "In millions"]:
            if fragment not in text:
                raise ValueError("Meta table structure changed")
        title = "메타(META) | 실적발표 요약"
        sections = {
            "핵심내용": ["2026년 3분기 매출 증가·영업이익 감소"],
            "주요 실적": ["매출: 646.01억 달러 (전년 동기 대비 +26%)",
                       "영업이익: 129.88억 달러 (전년 동기 대비 -37%)",
                       "순이익: 98.60억 달러 (전년 동기 대비 +264%)",
                       "희석 EPS: 3.83달러",
                       "영업이익률: 20% (전년 동기 40%)"],
        }
        for fragment in ["Income from operations $ 12,988 $ 20,535 (37)", "Operating margin 20 % 40 %", "3.83 $ 1.05"]:
            if fragment not in text:
                raise ValueError("Meta metric evidence changed")
    else:
        raise ValueError("No reviewed compact template for event " + str(event_id))
    return format_message(title, sections, event.source_url), photo


def send_photo(config, target, path, caption):
    if len(caption.encode("utf-16-le")) // 2 > 1024:
        raise ValueError("Photo caption too long")
    try:
        with path.open("rb") as photo:
            response = requests.post(
                f"https://api.telegram.org/bot{config.telegram_token}/sendPhoto",
                data={"chat_id": target, "caption": caption, "parse_mode": "HTML"},
                files={"photo": (path.name, photo, "image/jpeg")}, timeout=(20, 40),
            )
    except requests.RequestException:
        raise DeliveryError("photo_delivery_outcome_unknown", ambiguous=True) from None
    try:
        payload = response.json()
    except ValueError:
        raise DeliveryError("invalid_photo_response", ambiguous=True) from None
    if payload.get("ok") is not True:
        raise DeliveryError("photo_rejected_" + str(payload.get("error_code", response.status_code)))
    message_id = payload.get("result", {}).get("message_id")
    if not isinstance(message_id, int):
        raise DeliveryError("missing_photo_message_id", ambiguous=True)
    return message_id


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--send", action="store_true")
    parser.add_argument("--event-ids", type=int, nargs="+", default=[90, 91, 92, 93, 94])
    parser.add_argument("--target", choices=["test", "main"], default="test")
    args = parser.parse_args()
    config = Config.load(root=ROOT)
    target = config.telegram_chat_id if args.target == "main" else os.getenv("TELEGRAM_TEST_CHAT_ID", "").strip()
    if not target or (args.target == "test" and target == config.telegram_chat_id):
        raise ValueError("Separate test channel required")
    output = ROOT / "data/recent-compact"
    output.mkdir(exist_ok=True)
    repo = Repository(config.data_dir / "radar.sqlite3", readonly=True)
    try:
        placeholders = ",".join("?" for _ in args.event_ids)
        rows = repo.db.execute(
            "SELECT e.* FROM events e JOIN notifications n ON n.event_id=e.id "
            f"WHERE n.status='sent' AND e.id IN ({placeholders}) ORDER BY n.sent_at DESC",
            args.event_ids,
        ).fetchall()
        if {row["id"] for row in rows} != set(args.event_ids):
            raise ValueError("Requested sent events not found")
        prepared = []
        for row in rows:
            message, photo = build_preview(repo.row_event(row), row["id"])
            (output / f"event-{row['id']}.html").write_text(message, encoding="utf-8")
            if photo and (not photo.exists() or len(message.encode('utf-16-le')) // 2 > 1024):
                raise ValueError("Invalid photo/caption")
            prepared.append((row["id"], message, photo))
        print(json.dumps({"prepared": [p[0] for p in prepared], "photos": [p[0] for p in prepared if p[2]]}))
        if args.send:
            telegram = Telegram(config.telegram_token, target)
            for event_id, message, photo in reversed(prepared):
                message_id = send_photo(config, target, photo, message) if photo else telegram.send(message)
                receipt = {"event_id": event_id, "message_id": message_id, "photo": bool(photo), "target": args.target}
                with (output / "receipts.jsonl").open("a", encoding="utf-8") as log:
                    log.write(json.dumps(receipt) + "\n")
                print(json.dumps(receipt), flush=True)
                time.sleep(3.2)
    finally:
        repo.close()


if __name__ == "__main__":
    main()
