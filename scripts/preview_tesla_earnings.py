"""Build a source-verified Tesla earnings layout for the test channel only."""

import argparse
import json
import os
import re
import sys
from decimal import Decimal
from html import escape
from pathlib import Path

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from us_market_monitor.config import Config  # noqa: E402
from us_market_monitor.notifications.telegram import Telegram  # noqa: E402

SOURCE = "https://www.sec.gov/Archives/edgar/data/1318605/000162828026066738/exhibit9914.htm"


def build_message(text):
    # This preview is bound to this filing and its five explicitly ordered quarters.
    if not re.search(r"Q3-2025\s+Q4-2025\s+Q1-2026\s+Q2-2026\s+Q3-2026\s+YoY", text):
        raise ValueError("Unexpected reporting periods")
    if "$ in millions, except percentages and per share data" not in text:
        raise ValueError("Unverified financial units")

    def values(label):
        number = r"\(?\d[\d,]*(?:\.\d+)?\)?"
        match = re.search(re.escape(label) + r"\s+((?:" + number + r"\s+){4}" + number + r")", text)
        if not match:
            raise ValueError("Missing financial row: " + label)
        return [Decimal(n.replace(",", "").replace("(", "-").replace(")", ""))
                for n in match[1].split()]

    def change(row):
        return f"{(row[-1] / row[0] - 1) * 100:+.1f}%"

    def money(label, name):
        row = values(label)
        return f"• {name}: <b>{row[-1] / 100:,.2f}억 달러</b> (전년 대비 {change(row)})"

    revenue = values("Total revenues")
    operating = values("Income from operations")
    eps = values("EPS attributable to common stockholders, diluted (GAAP)")
    adjusted_eps = values("EPS attributable to common stockholders, diluted (non-GAAP)")
    deliveries = values("Total deliveries(1)")
    storage = values("Storage deployed (GWh)")
    # Business statements below are quotations translated for this specific source.
    for phrase in ["We started providing Robotaxi rides using Cybercabs on September 3 in Austin.",
                   "Megafactory Texas began production", "battery availability"]:
        if phrase not in text:
            raise ValueError("Missing business evidence: " + phrase)
    return "\n".join([
        "<b>[테스트 시안] 테슬라(TSLA) | 2026년 3분기 실적</b>",
        "", "매출은 전년 수준, 영업이익은 감소했습니다.",
        "", "<b>① 핵심 실적</b>",
        money("Total revenues", "매출"),
        money("Income from operations", "영업이익"),
        f"• 영업이익률: <b>{operating[-1] / revenue[-1] * 100:.2f}%</b> (공시 금액으로 계산)",
        money("Net income attributable to common stockholders (GAAP)", "순이익 · GAAP"),
        f"• 희석 EPS: <b>${eps[-1]:.2f}</b> · 조정 EPS: <b>${adjusted_eps[-1]:.2f}</b>",
        money("Free cash flow", "잉여현금흐름(FCF)"),
        "", "<b>② 사업별 매출</b>",
        money("Total automotive revenues", "자동차"),
        money("Energy generation and storage revenue", "에너지 발전·저장"),
        money("Services and other revenue", "서비스·기타"),
        "", "<b>③ 사업 진행 상황 · 회사 발표</b>",
        f"• 차량 인도: {deliveries[-1]:,.0f}대 (전년 대비 {change(deliveries)})",
        f"• 에너지 저장장치 배치: {storage[-1]:.1f}GWh (전년 대비 {change(storage)})",
        "• 9월 3일 오스틴에서 Cybercab을 이용한 로보택시 운행 시작",
        "• 텍사스 메가팩 공장 생산 시작. 배터리 공급은 차량 생산 확대의 제약으로 언급",
        "", "<b>비교 기준</b>",
        "전년 대비는 2025년 3분기와 비교했습니다. 조정 EPS는 non-GAAP 기준입니다.",
        "시장 예상치는 이 자료에 없어 예상 상회·하회 판단은 넣지 않았습니다.",
        "", "공시 접수: 2026-10-22 05:41 KST",
        f'<a href="{escape(SOURCE, quote=True)}">원문 · 테슬라 3분기 실적자료(SEC 첨부)</a>',
    ])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--send", action="store_true")
    args = parser.parse_args()
    config = Config.load(root=ROOT)
    response = requests.get(SOURCE, headers={"User-Agent": config.sec_user_agent}, timeout=25)
    response.raise_for_status()
    text = BeautifulSoup(response.text, "html.parser").get_text(" ", strip=True)
    message = build_message(text)
    output = ROOT / "data" / "tesla-earnings-test-preview.html"
    output.write_text(message, encoding="utf-8")
    if len(message) > 4096:
        raise ValueError("Message exceeds Telegram text limit")
    result = {"preview": str(output), "characters": len(message)}
    if args.send:
        target = os.getenv("TELEGRAM_TEST_CHAT_ID", "").strip()
        if not target or target == config.telegram_chat_id:
            raise ValueError("A separate test channel must be configured")
        result["message_id"] = Telegram(config.telegram_token, target).send(message)
    print(json.dumps(result, ensure_ascii=True))


if __name__ == "__main__":
    main()
