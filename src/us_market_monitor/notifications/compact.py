"""Approved channel layout, using the existing evidence and translation pipeline."""

import re
import unicodedata
from html import escape

from us_market_monitor.notifications.preview_layout import company_label, source_family
from us_market_monitor.processors.summarizer import RuleSummarizer


def heading(text):
    return '<b><u>' + escape(text) + '</u></b>'


def bullet_lines(items):
    """Separate paragraphs that are likely to wrap on a mobile screen."""
    lines, previous_long = [], False
    for item in items:
        wide = sum(2 if unicodedata.east_asian_width(c) in {'W', 'F'} else 1 for c in item) > 90
        if lines and (previous_long or wide):
            lines.append('')
        lines.append('· ' + escape(item))
        previous_long = wide
    return lines


def nominal(text):
    text = re.sub(r'(\d)\s+(년|월|분기)', r'\1\2', text)
    for ending, replacement in [('발표했습니다', '발표'), ('증가했습니다', '증가'),
                                ('감소했습니다', '감소'), ('기록했습니다', '기록'),
                                ('예상됩니다', '예상'), ('예정입니다', '예정'),
                                ('계획입니다', '계획'), ('전망입니다', '전망')]:
        text = re.sub(re.escape(ending) + r'[.!。]?$', replacement, text.strip())
    for ending, replacement in [('밝혔다', '밝혔음'), ('나타났다', '나타났음'),
                                ('보였다', '보였음'), ('했다', '했음'),
                                ('예상된다', '예상'), ('전망된다', '전망'), ('될 것이다', '예상')]:
        text = re.sub(re.escape(ending) + r'[.!。]?$', replacement, text.strip())
    return text


def notification_category(event):
    """Separate primary releases from reporting, independently of importance."""
    if re.search(r'\b(?:schedules?|sets? date|to (?:announce|report|release))\b.{0,160}'
                 r'\b(?:earnings|financial results)\b|실적.{0,20}(?:발표 일정|발표 예정)',
                 event.title, re.I):
        return '실적발표 일정'
    family = source_family(event)
    official = event.source == 'SEC EDGAR' or event.source.startswith('Official/')
    if re.search(r'earnings call|어닝콜', event.title, re.I):
        return '어닝콜 요약' if official else '어닝콜 관련 보도'
    if family == '실적 발표':
        return '실적발표 요약' if official else '실적 관련 보도'
    if family == '실적 전망' and not official:
        return '실적 전망 관련 보도'
    return family


def format_compact_event(event, *, test=False):
    title, summary = RuleSummarizer().summarize(event)
    record = event.metadata.get('ollama_summary')
    if record:
        title = record['result']['title_ko']
    family = notification_category(event)
    issuer = company_label(event, {})
    if issuer:
        title = issuer + ' | ' + family
    else:
        title = title[:250] + ' | ' + family
    groups = {}
    categories = {'핵심 발표': '핵심내용', '주요 내용': '핵심내용',
                  '향후 계획': '향후 전망', '실적 전망': '향후 전망',
                  '기술·제품': '사업 진행 상황', '참여·도입': '사업 진행 상황',
                  '계약·거래': '주요 계약·투자'}
    for line in summary.splitlines():
        line = line.strip().lstrip('•· ')
        if not line or line.startswith(('공시 유형:', '공시 항목:', '※')):
            continue
        label, sep, value = line.partition(': ')
        if sep and label in categories:
            category, text = categories[label], value
        elif sep and label in {'주요 실적', '성능·수치'}:
            category, text = label, value
        elif sep and re.search(r'매출|이익|EPS|현금흐름|마진', label, re.I):
            category, text = '주요 실적', line
        else:
            category, text = '핵심내용', line
        if family == '실적발표 일정':
            category = '발표 일정'
        # Split separately labelled metrics only; never split a numeric comparison.
        for item in re.split(r'\s+·\s+(?=[^:·]{1,25}:)', text):
            groups.setdefault(category, []).append(nominal(item))
    if record:
        from us_market_monitor.processors.ollama import card_sections, fingerprint
        from us_market_monitor.processors.ollama_preview import validate_result

        validate_result(record['result'], record['source'])
        if record['fingerprint'] != fingerprint(event, record['model']):
            raise ValueError('stale_ollama_summary')
        groups = card_sections(record)
    lines = [heading(('[테스트] ' if test else '') + '🇺🇸' + title + '🇺🇸')]
    for category, items in groups.items():
        lines += ['', heading('📍' + category), *bullet_lines(items)]
    lines += ['', heading('📍원문 링크') + ' : ' + escape(event.source_url), '',
              heading('🇺🇸그로쓰리서치 U.S 채널[실시간 U.S 주요 알림]'),
              'https://t.me/growth_us']
    return '\n'.join(lines)
