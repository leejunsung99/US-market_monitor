"""Experimental source-aware layouts. Not imported by the production scheduler."""

import re
from dataclasses import dataclass, field
from decimal import Decimal
from html import escape
from zoneinfo import ZoneInfo

from us_market_monitor.collectors.economic import LABELS

COMPANY_NAMES = {
    "TSLA": "테슬라", "META": "메타", "NVDA": "엔비디아", "TSM": "TSMC",
    "LRCX": "램리서치", "AAPL": "애플", "GOOGL": "알파벳", "GOOG": "알파벳",
    "MSFT": "마이크로소프트", "AMZN": "아마존", "AVGO": "브로드컴",
    "MU": "마이크론", "AMD": "AMD", "INTC": "인텔", "QCOM": "퀄컴",
    "SMCI": "슈퍼마이크로", "NFLX": "넷플릭스", "LLY": "일라이릴리",
    'ORCL': '오라클', 'CRWD': '크라우드스트라이크', 'PANW': '팔로알토네트웍스',
    'OKTA': '옥타', 'GGG': '그라코', 'QS': '퀀텀스케이프',
}
NOISE = re.compile(
    r"our .{0,30}portfolio|price prediction|subscribe|trade.in|unlimited plan|"
    r"receive credit|all rights reserved|net income per diluted share - amortization|"
    r"forward.looking statements|last year|announced in July", re.I
)
FORECAST = re.compile(r"\b(?:expects?|guidance|outlook|forecast|projected|plans?|will)\b", re.I)
METRIC = re.compile(r"revenue|income|profit|EPS|earnings per|cash flow|margin", re.I)
TABLE = re.compile(r"(?:\(?\d[\d,.]*\)?\s+){6,}")
NUMBER = re.compile(r"(?<![\w])[-+]?\d[\d,]*(?:\.\d+)?")


def numeric_tokens(text):
    return [m.replace(",", "") for m in NUMBER.findall(text)]


def safe_translation(original, translated):
    # Order matters: swapping current/prior values or range endpoints is unsafe.
    return bool(translated.strip()) and numeric_tokens(original) == numeric_tokens(translated)


@dataclass
class PreviewCard:
    event_id: int
    family: str
    title: str
    source: str
    source_url: str
    publication: str
    headline: str = ""
    sections: dict[str, list[str]] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    omitted: int = 0


def source_family(event):
    if event.source in {"BLS", "BEA", "Census", "DOL", "EIA"}:
        return "경제지표"
    if event.source == "Federal Reserve":
        if event.metadata.get("feed") == "speeches":
            return "연준 연설·증언"
        if re.search(r"minutes", event.title, re.I):
            return "연준 회의록"
        return "연준 공식 발표"
    if event.source == "BIS":
        return "수출통제·규제"
    # Structured SEC disclosure metadata outranks incidental product sentences.
    if event.source == "SEC EDGAR" and "2.02" in event.metadata.get("items", []):
        return "실적 발표"
    if event.event_type == "earnings_guidance" or re.search(
        r"(?:raises?|raised|raising|lifts?|lifted|cuts?|cutting|lowers?|lowered).{0,65}(?:forecast|outlook|guidance)",
        event.title, re.I,
    ):
        return "실적 전망"
    if event.event_type == "earnings" or re.search(
        r"earnings call|reports .{0,30}(?:EPS|results)|quarter.{0,15}results", event.title, re.I
    ):
        return "실적 발표"
    return {
        "acquisition": "인수·합병", "contract_investment": "계약·투자",
        "core_product": "제품·사업 발표", "industry_outlook": "산업·시장 동향",
        "financial_distress": "재무 위험", "earnings_guidance": "실적 전망",
    }.get(event.event_type, "기업·정책 발표")


def company_label(event, companies):
    # News mentioning multiple stocks must not be attributed to the first ticker.
    issuer = event.metadata.get("issuer_ticker")
    if not issuer and event.source == "SEC EDGAR" and event.related_tickers:
        issuer = event.related_tickers[0]
    tickers = [issuer] if issuer else list(event.related_tickers)
    if len(tickers) != 1:
        return ""
    ticker = tickers[0]
    aliases = companies.get(ticker, {}).get("aliases", [])
    korean = next((a for a in aliases if re.search(r"[가-힣]", a)), "")
    name = COMPANY_NAMES.get(ticker) or korean or (aliases[0] if aliases else ticker)
    return f"{name}({ticker})" if name != ticker else ticker


def candidate_sentences(event):
    # Keep U.S., decimals and the original paragraph boundaries intact.
    text = event.article_summary or event.summary
    result, omitted = [], 0
    for paragraph in text.splitlines():
        paragraph = re.sub(r"\bU\.S\.", "US", paragraph)
        for sentence in re.split(r"(?<=[.!?])\s+(?=[A-Z])", paragraph):
            sentence = sentence.strip()
            sentence = re.sub(r"^EX-99\.1.*?(?=(?:Meta|Lam|TSMC|Tesla) (?:Reports|reports))", "", sentence)
            if (NOISE.search(sentence) or TABLE.search(sentence)
                    or len(sentence) > 550 or len(sentence) < 20):
                omitted += 1
                continue
            if sentence.endswith(("...", "…")):
                omitted += 1
                continue
            if sentence not in result:
                result.append(sentence)
    return result, omitted


def economic_points(event):
    """Use verified source-specific roles rather than a generic number grab."""
    indicator, text = event.metadata.get("indicator"), event.summary
    if indicator == "ppi":
        points = []
        for pattern, label in [
            (r"final demand (increased|decreased) ([\d.]+) percent in", "최종수요 PPI 전월 대비"),
            (r"final demand (?:moved )?(up|down) ([\d.]+) percent for the 12 months", "최종수요 PPI 전년 동월 대비"),
            (r"final demand goods (rose|fell) ([\d.]+) percent", "상품 전월 대비"),
            (r"final demand services (advanced|declined) ([\d.]+) percent", "서비스 전월 대비"),
        ]:
            match = re.search(pattern, text, re.I)
            if match:
                sign = "-" if match[1].lower() in {"decreased", "down", "fell", "declined"} else "+"
                points.append(f"{label}: {sign}{match[2]}%")
        if points:
            return points
    if indicator in {"cpi", "employment", "gdp"}:
        from us_market_monitor.processors.briefing import economic_digest

        digest = economic_digest(event)
        if "공식 발표 원문 발췌" not in digest:
            return [line.lstrip("• ") for line in digest.splitlines() if line.strip()]
    if indicator == "claims":
        points = []
        patterns = [
            (r"seasonally adjusted initial claims was ([\d,]+), a(?:n)? (increase|decrease) of ([\d,]+)",
             "신규 실업수당 청구"),
            (r"seasonally adjusted insured unemployment during the week ending (\w+ \d+) was ([\d,]+), a(?:n)? (decrease|increase) of ([\d,]+)",
             "계속 실업수당 수급"),
        ]
        for pattern, label in patterns:
            match = re.search(pattern, text, re.I)
            if match:
                parts = match.groups()
                value, direction, delta = parts[-3:]
                date = ''
                if len(parts) == 4:
                    month, day = parts[0].split()
                    months = 'January February March April May June July August September October November December'.split()
                    date = f' · {months.index(month) + 1}월 {day}일 종료 주'
                points.append(f"{label}: {value}명 (전주 수정치 대비 {'+' if direction == 'increase' else '-'}{delta}명{date})")
        match = re.search(r"4\s*-week moving average was ([\d,]+), a(?:n)? (increase|decrease) of ([\d,]+)", text)
        if match:
            points.append(f"신규 청구 4주 이동평균: {match[1]}명 (전주 수정치 대비 {'+' if match[2] == 'increase' else '-'}{match[3]}명)")
        if points:
            points.append("계절조정 기준 · 신규 청구와 계속 수급의 기준 주는 다름")
            return points
    if indicator == "census_housing_starts":
        level = re.search(r"in (\w+ 20\d{2}) were at a seasonally adjusted annual rate of ([\d,]+)", text)
        comparison = re.search(r"This is ([\d.]+) percent \(\+/- ([\d.]+)%\)\* (below|above).*?estimate of ([\d,]+)", text)
        if level and comparison:
            return [f"주택착공: {level[2]}호 (계절조정 연율 · {level[1]})",
                    f"전월 수정치 대비: {'-' if comparison[3] == 'below' else '+'}{comparison[1]}% · 전월 수정치 {comparison[4]}호",
                    f"발표 오차범위: ±{comparison[2]}% · 원문의 * 표기 유지"]
    # Keep revisions, seasonal adjustment and error bands in the same excerpt.
    if len(text) <= 1000:
        return [text.strip()]
    return candidate_sentences(event)[0]


def financial_points(event):
    """Extract explicitly labelled narrative figures, preserving currency and basis."""
    text = event.article_summary or event.summary
    points = []
    if event.source == "Official/tsmc":
        for pattern, label in [
            (r"consolidated revenue of NT\$([\d,.]+) billion", "매출 · 대만달러"),
            (r"net income of NT\$([\d,.]+) billion", "순이익 · 대만달러"),
            (r"In US dollars, third quarter revenue was \$([\d,.]+) billion", "매출 · 미국달러"),
        ]:
            match = re.search(pattern, text, re.I)
            if match:
                amount = Decimal(match[1].replace(",", "")) * 10
                points.append(f"{label}: {amount:,.2f}억")
        match = re.search(r"diluted earnings per share of NT\$([\d.]+) \(US\$([\d.]+) per ADR unit\)", text)
        if match:
            points.append(f"희석 EPS: NT${match[1]} · ADR 1단위당 US${match[2]}")
        match = re.search(r"Year-over-year, third quarter revenue increased ([\d.]+)%, while net income and diluted EPS both increased ([\d.]+)%", text)
        if match:
            points.append(f"전년 동기 대비: 매출 +{match[1]}% · 순이익·희석 EPS 각각 +{match[2]}%")
    return points


def guidance_points(event):
    text = event.article_summary or event.summary
    number = r"([\d.]+)"
    pattern = (r"preliminary revenue is now expected to stand at \$" + number
               + r" billion to \$" + number + r" billion.*?previous forecast of \$"
               + number + r" billion to \$" + number + r" billion")
    match = re.search(pattern, text, re.I | re.S)
    if match:
        amounts = [Decimal(value) * 10 for value in match.groups()]
        return [f"잠정 매출 전망: {amounts[0]:g}억 ~ {amounts[1]:g}억 달러",
                f"기존 전망: {amounts[2]:g}억 ~ {amounts[3]:g}억 달러",
                "확정 실적이 아닌 회사의 잠정 전망치"]
    return []


def build_card(event, event_id, companies):
    family = source_family(event)
    issuer = company_label(event, companies)
    if family == "경제지표":
        title = "미국 · " + LABELS.get(event.metadata.get("indicator"), event.title)
    elif event.source == "SEC EDGAR":
        title = f"{issuer or event.metadata.get('issuer', '기업')} | {family}"
    else:
        title = f"{issuer + ' | ' if issuer else ''}{family}"
    if event.metadata.get("publication_precision") == "date":
        publication = event.metadata.get("publication_date", "") + " (시각 미제공)"
    else:
        publication = event.published_at.astimezone(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d %H:%M KST")
    card = PreviewCard(event_id, family, title, event.source, event.source_url, publication)
    if event.source != "SEC EDGAR":
        card.headline = event.title
    if event.source == "Official/tsmc" and "Third Quarter" in event.title:
        card.headline = "3분기 실적 · 통화 및 ADR 단위 구분"
    if family == "경제지표":
        # Indicator is already in the heading; duplicate feed headline adds clutter.
        card.headline = ""
    candidates, card.omitted = candidate_sentences(event)
    if family == "경제지표":
        candidates = economic_points(event)
    extracted_financials = financial_points(event) if family == "실적 발표" else []
    extracted_guidance = guidance_points(event) if family == "실적 전망" else []
    if extracted_financials:
        card.sections["발표 실적"] = extracted_financials
        candidates = []
    elif extracted_guidance:
        card.sections["전망·계획 · 확정 실적과 구분"] = extracted_guidance
        candidates = []
    for sentence in candidates:
        if family == "경제지표":
            label = "발표 수치·비교 기준"
        elif family.startswith("연준"):
            label = "발언·회의 내용"
        elif FORECAST.search(sentence):
            label = "전망·계획 · 확정 실적과 구분"
        elif family == "실적 발표" and METRIC.search(sentence):
            label = "발표 실적"
        elif re.search(r"\d", sentence):
            label = "주요 수치·조건"
        else:
            label = "핵심 내용"
        section = card.sections.setdefault(label, [])
        limit = 6 if family == "경제지표" else 3
        if len(section) < limit and sum(map(len, card.sections.values())) < 6:
            section.append(sentence)
    order = ["발표 실적", "발표 수치·비교 기준", "핵심 내용", "주요 수치·조건",
             "발언·회의 내용", "전망·계획 · 확정 실적과 구분"]
    card.sections = {key: card.sections[key] for key in order if key in card.sections}
    if event.source.startswith("Finnhub/"):
        card.notes.append("언론 보도 기준 · 기업 공식 발표와 구분")
    if family == "연준 연설·증언":
        card.notes.append("연준 인사 발언 · FOMC 금리 결정과 구분")
    if family == "연준 회의록" and "discount rate" in event.title.lower():
        card.notes.append("할인율 회의록 · FOMC 의사록과 구분")
    if family == "실적 발표":
        card.notes.append("GAAP·조정 실적과 통화는 원문 표기 유지 · 예상치 비교 미생성")
        if not card.sections.get("발표 실적"):
            card.notes.append("실적 수치의 구조화가 완료되지 않아 실적표는 표시하지 않았습니다.")
    if card.omitted:
        card.notes.append("광고·과거 배경·표 형태 또는 불완전 문장은 요약에서 제외")
    if not card.sections:
        card.notes.append("구조화할 본문이 부족합니다. 원문 확인이 필요합니다.")
    return card


def render_card(card, translate=lambda text: text):
    lines = ["<b>[테스트 시안] " + escape(card.title) + "</b>"]
    if card.headline:
        translated = translate(card.headline)
        lines += [escape(translated if safe_translation(card.headline, translated) else card.headline)]
    for label, texts in card.sections.items():
        lines += ["", "<b>" + escape(label) + "</b>"]
        for text in texts:
            translated = translate(text)
            verified = safe_translation(text, translated)
            displayed = translated if verified else text
            suffix = " [원문]" if not re.search(r"[가-힣]", displayed) else ""
            lines.append("• " + escape(displayed) + suffix)
    if card.notes:
        lines += ["", *["※ " + escape(note) for note in card.notes]]
    lines += ["", escape(card.source) + " · " + escape(card.publication),
              '<a href="' + escape(card.source_url, quote=True) + '">원문 보기</a>']
    return "\n".join(lines)
