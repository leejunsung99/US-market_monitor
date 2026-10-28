"""Select complete source sentences; never invent figures, causality or forecasts."""

import re
from decimal import Decimal
from difflib import SequenceMatcher

from us_market_monitor.processors.normalizer import digest, plain_text

VERSION = "source-brief-v4-clean"

NOISE = re.compile(
    r"share on|subscribe|read more|click here|all rights reserved|privacy policy|"
    r"forward.looking statements|safe harbor|actual results (?:may|could) differ|"
    r"securities and exchange commission|pursuant to|exact name of registrant|"
    r"principal executive offices|registrant.s telephone|check the appropriate box|"
    r"emerging growth company|commission file number|signature|exhibit index|"
    r"incorporated (?:herein|by reference)|shall (?:not )?be deemed|not deemed.*filed|"
    r"^Item\s+\d\.\d{2}\b|exhibit number|press release of the company, dated|"
    r"financial data supplement.*quarter ended|for media inquiries|last update|for release at",
    re.I,
)
PROMOTION = re.compile(
    r'marketbeat all access|lock in .{0,40}access|should you invest|'
    r'get every upgrade|this instant news alert was generated|'
    r'view the five stocks|this report names|free report|'
    r'preferred source on google|sign up for|'
    r'may earn commission or revenue|contributed to this report|'
    r'has written well over|editor:|to learn more|see related.{0,80}announcement|'
    r'TIKR lets you|TIKR.s valuation model|full breakdown of Buy, Hold, and Sell ratings|'
    r'^(?:about .{0,70} )?.{0,70} is a technology company that develops', re.I,
)
ACTION = re.compile(
    r"\b(?:announce\w*|introduc\w*|launch\w*|report\w*|release\w*|adopt\w*|"
    r"approv\w*|agree\w*|acquir\w*|complet\w*|declar\w*|enter\w*|terminat\w*|"
    r"rais\w*|withdraw\w*|expand\w*|expect\w*|plan\w*)\b|발표|출시|도입|계약|승인",
    re.I,
)
TOPICS = (
    (
        "향후 계획",
        r"\b(?:guidance|outlook|forecast|expects?|plans?|will|early.access)\b|전망|예정|계획",
    ),
    (
        "주요 실적",
        r"\b(?:revenue|net income|earnings per share|EPS|net profit|sales)\b|매출|순이익",
    ),
    ("배당·자사주", r"\b(?:dividend|repurchase|buyback)\b|배당|자사주"),
    ("계약·거래", r"\b(?:agreement|acquisition|merger|contract|acquire)\b|인수|합병|계약"),
    ("참여·도입", r"\b(?:adopt\w*|collaborat\w*|partner\w*)\b|도입|협력"),
    ("기술·제품", r"\b(?:library|libraries|technology|chip|GPU|AI|manufactur\w*)\b|기술|반도체"),
)


def topic(text):
    if re.search(r"\b(?:revenue|net income|earnings per share|EPS|net profit|sales)\b", text, re.I):
        return (
            "실적 전망"
            if re.search(r"guidance|outlook|forecast|expects?", text, re.I)
            else "주요 실적"
        )
    # Test conditions belong with performance claims, not a separate forecast.
    if re.search(r"\d.*(?:[x×%]|times|배)|accelerat|speedup", text, re.I):
        return "성능·수치"
    for label, pattern in TOPICS:
        if re.search(pattern, text, re.I):
            return label
    return "주요 내용"


def sentences(text, title=""):
    """Keep decimal points and ranges intact, omit clipped trailing fragments."""
    for paragraph in re.split(r"\n+|\s*•\s*", text or ""):
        paragraph = plain_text(paragraph)
        if re.search(r'By spreading exposure across multiple asset classes|'
                     r'^For accredited investors|^About (?:Stagwell|Palantir|Qualcomm)\b|'
                     r'^These statements may relate to|^These risks and uncertainties include',
                     paragraph, re.I):
            break
        paragraph = re.sub(
            r"^(?:News Summary|Highlights|뉴스 요약|주요 내용)\s*:\s*", "", paragraph, flags=re.I
        )
        if title and paragraph.casefold().startswith(title.casefold()):
            paragraph = paragraph[len(title) :].lstrip(" .:—–-")
        # Press-release feeds sometimes join sentences without a space.
        paragraph = re.sub(r"(?<=[a-z0-9])\.(?=[A-Z][a-z])", ". ", paragraph)
        # Date abbreviations and a.m./p.m. are not sentence endings. Preserve
        # the exact original spelling after splitting so evidence stays intact.
        abbreviation = re.compile(r'\b(?:Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)\.'
                                  r'|\b[ap]\.m\.', re.I)
        paragraph = abbreviation.sub(lambda m: m[0].replace('.', '\ue000'), paragraph)
        for sentence in re.split(r"(?<=[.!?。])\s+(?=[A-Z0-9\"“가-힣])", paragraph):
            sentence = sentence.replace('\ue000', '.')
            sentence = sentence.strip().lstrip("• ")
            if not sentence or NOISE.search(sentence) or PROMOTION.search(sentence):
                continue
            # Generic press-release benefit statements are not new financial
            # facts. Keep quantified details, investment periods and milestones.
            if (re.search(r'innovation|business value|business outcomes|business transformation|'
                          r'customer success|choice and flexibility', sentence, re.I)
                    and not re.search(r'[$%\d]|\b(?:one|two|three|four|five|six|seven|eight|nine|ten)'
                                      r'[- ]years?\b|\b(?:revenue|income|margin|costs?|'
                                      r'guidance|production|shipments?|capacity|launches?|launched)\b',
                                      sentence, re.I)):
                continue
            if re.match(
                r"\d+\s+(?:\d+\s+)?(?:Includes|Excludes|See|Refer|Amounts|Reflects)\b",
                sentence,
                re.I,
            ):
                continue
            if title and sentence.casefold().rstrip(".") == title.casefold().rstrip("."):
                continue
            if len(sentence) < 10:
                continue
            complete = bool(
                re.search(r"[.!?。][\"”’']?$|(?:습니다|이다|했다|한다|예정)\.?$", sentence)
            )
            if not complete:
                # A truncated feed teaser is never presented as a complete fact.
                continue
            yield sentence


def select_points(text, title=""):
    candidates = []
    for index, sentence in enumerate(sentences(text, title)):
        score = 2 * bool(ACTION.search(sentence))
        score += 3 * bool(
            re.search(r"[$€£]\s*\d|\d[\d.,–-]*\s*(?:%|x\b|billion|million|배)", sentence, re.I)
        )
        score += 1 * (index < 3)
        if re.search(
            r"\b(?:net income|earnings per share|per diluted share|EPS)\b", sentence, re.I
        ):
            score += 3
        candidates.append((index, sentence, score))
    if not candidates:
        return []
    # Preserve every distinct material fact; never stop at a sentence/character quota.
    first = next((c for c in candidates if ACTION.search(c[1])), candidates[0])
    material = [c for c in candidates if c[2] >= 2 or topic(c[1]) != '주요 내용'
                or re.search(r'cost|cash|margin|risk|constraint|capacity|regulat|litigat|'
                             r'monetiz|retention|engagement|비용|위험|제약|현금|이익률', c[1], re.I)]
    chosen = [first]
    for candidate in material or candidates:
        if candidate == first:
            continue
        if any(duplicate_fact(candidate[1], c[1]) for c in chosen):
            continue
        chosen.append(candidate)
    return [first[1], *(c[1] for c in sorted(chosen[1:]))]


def duplicate_fact(left, right):
    # Similar sentences with different values/periods must remain separate.
    quantities = r"\d[\d.,]*(?:[-–]\d[\d.,]*)?"
    if re.findall(quantities, left) != re.findall(quantities, right):
        return False
    if SequenceMatcher(None, left.casefold(), right.casefold()).ratio() > 0.82:
        return True
    words_left = set(re.findall(r"\w+", left.casefold()))
    words_right = set(re.findall(r"\w+", right.casefold()))
    smaller, larger = sorted((words_left, words_right), key=len)
    return len(smaller) >= 8 and len(smaller & larger) / len(smaller) > 0.85


def prepare_brief(event, text=None):
    """Keep the fetched source and select material excerpts without a count quota."""
    if text is not None:
        event.body = text
    source = text if text is not None else (event.article_summary or event.body or event.summary)
    points = select_points(source, event.title)
    event.article_summary = "\n".join(points)
    return points


def brief_points(event):
    # New summaries are newline-separated complete source sentences. Older records
    # also pass through the cleaner so cover pages and clipped teasers stay out.
    points = select_points(event.article_summary or event.summary, event.title)
    if (not points and event.source.startswith('Finnhub/')
            and event.title and not event.title.rstrip().endswith(('...', '…'))):
        # Short wire headlines are complete editorial items even without a full
        # stop. Never turn a clipped article teaser into a fabricated sentence.
        return [event.title]
    return points


def translation_fingerprint(event):
    return VERSION + ":" + digest("\n".join([event.title[:350], *brief_points(event)]))


def expand_money_ranges(text):
    """In $250-$275 million, the shared scale applies to both endpoints."""
    return re.sub(
        r'\$\s*(\d[\d,]*(?:\.\d+)?)\s*([-–]|to)\s*\$?\s*'
        r'(\d[\d,]*(?:\.\d+)?)\s*(trillion|billion|million|bn|[BMK]\b)',
        lambda m: '$' + m[1] + ' ' + m[4] + ' ' + m[2] + ' $' + m[3] + ' ' + m[4],
        text, flags=re.I,
    )


def polish_korean(text, source):
    """Deterministic display conversions after source-number validation."""
    text = expand_money_ranges(text)
    if re.search(r"additional(?:ly)?", source, re.I):
        text = re.sub(r"한\s+(?=\d[\d.]*[-–]\d[\d.]*x)", "추가로 ", text)
    text = text.replace("초기 접근 테스트", "초기 테스트")
    text = re.sub(r"(\d+(?:\.\d+)?)\s+percent\b", r"\1%", text, flags=re.I)
    text = text.replace("Institutional Securities", "기관증권 부문").replace(
        "Wealth Management", "자산관리 부문"
    )
    text = text.replace("부문는", "부문은").replace("부문가", "부문이").replace("부문를", "부문을")
    text = text.replace("순신규자산", "순신규 자산").replace("주식 프랜차이즈", "주식 사업")
    text = re.sub(r"(\d+(?:\.\d+)?)[-–](\d+(?:\.\d+)?)x\b", r"\1–\2배", text)
    text = re.sub(r"(\d+(?:\.\d+)?)x\b", r"\1배", text)

    def dollars(match):
        amount = Decimal(match[1].replace(",", ""))
        amount *= {"trillion": 10**12, "billion": 10**9, "million": 10**6, "bn": 10**9,
                   'b': 10**9, 'm': 10**6, 't': 10**12, 'k': 1000}.get(
            (match[2] or "").lower(), 1
        )
        for scale, unit in ((10**12, "조"), (10**8, "억"), (10**4, "만"), (1, "")):
            if abs(amount) >= scale or scale == 1:
                value = format(amount / Decimal(scale), "f")
                if "." in value:
                    value = value.rstrip("0").rstrip(".")
                return value + unit + " 달러"

    text = re.sub(
        r"(?<![A-Za-z])\$\s*(\d[\d,]*(?:\.\d+)?)(?:\s*(trillion|billion|million|bn|[BMTK])(?![A-Za-z]))?",
        dollars,
        text,
        flags=re.I,
    )
    for before, after in (("과", "와"), ("을", "를"), ("으로", "로"), ("로", "로"), ("와", "와")):
        text = re.sub(r"달러\s+" + before + r"(?=\s|[,.])", "달러" + after, text)
    text = re.sub(r'달러\s+달러', '달러', text)
    return text


def financial_fact_korean(source):
    """Render narrowly matched reported metrics; leave forecasts to translation."""
    if re.search(
        r"guidance|outlook|forecast|expects?|plans?|will|pre.tax|adjusted|non.GAAP", source, re.I
    ):
        return ""
    money = r"\$\s*\d[\d,]*(?:\.\d+)?(?:\s*(?:trillion|billion|million|bn))?"
    revenue = re.search(
        r"\b(?:net )?revenues?\s+(?:of|was|were|totaled|reached)\s+(" + money + ")", source, re.I
    )
    income = re.search(
        r"\bnet income\s+(?:applicable to [^,.]{1,80}?\s+)?(?:of|was|were)\s+(" + money + ")",
        source,
        re.I,
    )
    if bool(revenue) == bool(income):
        return ""
    match = revenue or income
    result = ("매출" if revenue else "순이익") + ": " + polish_korean(match[1], source)
    prior = re.search(r"compared (?:with|to)\s+(" + money + ")", source, re.I)
    annual = bool(re.search(r"(?:a|one) year ago|year.earlier|prior.year", source, re.I))
    if prior and annual:
        result += " (전년 동기 " + polish_korean(prior[1], source) + ")"
    if income:
        eps = re.findall("(" + money + r")\s+per diluted share", source, re.I)
        if eps:
            result += " / 희석 EPS: " + polish_korean(eps[0], source)
            if len(eps) == 2 and annual:
                result += " (전년 동기 " + polish_korean(eps[1], source) + ")"
    return result


def economic_digest(event):
    """Avoid translating statistical terms (SA/NSA) or changing metric roles."""
    indicator = event.metadata.get("indicator")
    text = event.summary
    if indicator == 'claims':
        from us_market_monitor.notifications.preview_layout import economic_points

        points = economic_points(event)
        if points and all(re.search(r'[가-힣]', point) for point in points):
            return '\n'.join('• ' + point for point in points)
    movement = r"\b(rose|increased|fell|declined|decreased|up|down|advanced)\s+(?:by\s+)?(\d+(?:\.\d+)?)\s+percent\b"

    def rate(match):
        value = Decimal(match[2])
        if match[1].lower() in {"fell", "declined", "decreased", "down"}:
            value = -value
        return ("+" if value > 0 else "") + format(value, "f") + "%"

    lines = []
    if indicator == "cpi":
        parts = re.split(r"(?<=\.)\s+(?=[A-Z])", text)
        for part in parts:
            if re.search(r"Consumer Price Index for All Urban Consumers|CPI-U", part, re.I):
                name = "헤드라인 CPI"
            elif re.search(r"index for all items less food and energy", part, re.I):
                name = "근원 CPI"
            else:
                continue
            matches = list(re.finditer(movement, part, re.I))
            annual = bool(re.search(r"over (?:the )?(?:year|last 12 months)", part, re.I))
            if len(matches) == 2 and annual and re.search(r"\bSA\b|seasonally adjusted", part):
                lines += [
                    f"• {name} 전월 대비: {rate(matches[0])} (계절조정)",
                    f"• {name} 전년 동월 대비: {rate(matches[1])} (계절조정 전)",
                ]
    elif indicator == "ppi":
        mom = re.search(
            r"Producer Price Index for final demand\s+(?:moved\s+)?" + movement, text, re.I
        )
        yoy = re.search(
            r"Prices for final demand\s+" + movement + r"\s+(?:for|over) the 12 months", text, re.I
        )
        if mom:
            lines.append("• 최종수요 PPI 전월 대비: " + rate(mom))
        if yoy:
            lines.append("• 최종수요 PPI 전년 동월 대비: " + rate(yoy))
    elif indicator == "employment":
        payroll = re.search(
            r"(?:nonfarm )?payroll employment\s*\(\s*([+-]?\d[\d,]*)\s*\)", text, re.I
        )
        unemployment = re.search(
            r"unemployment rate\s*\(\s*(\d+(?:\.\d+)?)\s*percent\s*\)", text, re.I
        )
        if payroll:
            change = int(payroll[1].replace(",", ""))
            lines.append(f"• 비농업 고용 증감: {change:+,}명")
        else:
            payroll = re.search(
                r"(?:nonfarm )?payroll employment\s+(increased|rose|declined|decreased|fell)\s+by\s+(\d[\d,]*)",
                text,
                re.I,
            )
            if payroll:
                change = int(payroll[2].replace(",", ""))
                if payroll[1].lower() in {"declined", "decreased", "fell"}:
                    change = -change
                lines.append(f"• 비농업 고용 증감: {change:+,}명")
        if not unemployment:
            unemployment = re.search(
                r"unemployment rate\s+(?:was\s+)?(?:unchanged\s+)?at\s+(\d+(?:\.\d+)?)\s*percent",
                text,
                re.I,
            )
        if unemployment:
            lines.append("• 실업률: " + unemployment[1] + "%")
    elif indicator == "gdp":
        gdp = re.search(
            r"Real (?:gross domestic product\s*\(GDP\)|GDP)\s+(increased|decreased)\s+at an annual rate of\s+(\d+(?:\.\d+)?)\s+percent",
            text,
            re.I,
        )
        if gdp:
            lines.append("• 실질 GDP 증가율(연율): " + rate(gdp))
    if lines:
        return "\n".join(lines)
    # Other releases retain complete official source sentences until a verified
    # metric template is available. Do not risk mistranslating statistical roles.
    return (
        "\n".join("• " + point for point in brief_points(event))
        + "\n※ 공식 발표 원문 발췌 · 예상치 비교 없음"
    )


def economic_title(event):
    from us_market_monitor.collectors.economic import LABELS

    labels = LABELS
    period = ""
    quarter = re.search(r"([1-4])(?:st|nd|rd|th) Quarter (20\d{2})", event.title, re.I)
    if quarter:
        period = quarter[2] + "년 " + quarter[1] + "분기 "
    else:
        months = "January February March April May June July August September October November December".split()
        for number, month in enumerate(months, 1):
            if re.search(r"\b" + month + r"\b", event.title, re.I):
                period = str(number) + "월 "
                break
    return "미국 " + period + labels.get(event.metadata.get("indicator"), "경제지표") + " 공식 발표"


def display_summary(event):
    if event.event_type == "economic_indicator":
        return economic_digest(event)
    points = brief_points(event)
    translated = event.summary_ko.splitlines() if event.summary_ko else []
    if len(translated) != len(points) or event.translation_hash != translation_fingerprint(event):
        translated = []
    lines = []
    if event.source == "SEC EDGAR":
        form = event.metadata.get("form")
        if not form:
            match = re.search(r"\b(?:8-K|6-K)(?:/A)?\b", event.title)
            form = match[0] if match else ""
        if form:
            lines.append("• 공시 유형: " + form)
        items = event.metadata.get("items", [])
        if items:
            lines.append("• 공시 항목: " + ", ".join(items))
    for index, original in enumerate(points):
        financial = financial_fact_korean(original)
        text = financial or (polish_korean(translated[index], original) if translated else original)
        label = "핵심 발표" if index == 0 else topic(original)
        if event.event_type == "economic_indicator":
            label = "주요 수치" if index == 0 else "세부 지표"
        if financial:
            label, text = financial.split(": ", 1)
        lines.append(f"• {label}: {text}")
    if not points:
        lines.append("• 본문 요약을 확보하지 못했습니다. 원문에서 내용을 확인해 주세요.")
    elif not translated and not re.search(r"[가-힣]", points[0]):
        lines.append("※ 원문 발췌 · 한국어 번역 미적용")
    return "\n".join(lines)
