"""Compact photo captions with verified fact spans and a hard display budget."""

import re
from html import escape
from html.parser import HTMLParser

from us_market_monitor.notifications.compact import heading, nominal
from us_market_monitor.processors.briefing import polish_korean

CAPTION_LIMIT = 1024
SECTIONS = ('핵심내용', '주요 실적', '향후 전망', '주요 위험')


class _Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)


def visible_text(html):
    parser = _Text()
    parser.feed(html)
    parser.close()
    return ''.join(parser.parts)


def caption_units(html):
    """Count displayed UTF-16 units, excluding HTML tags and link destinations."""
    return len(visible_text(html).encode('utf-16-le')) // 2


def emphasize(text, focus):
    """Model supplies plain-text spans; only the application can create HTML."""
    if not focus:
        return escape(text)
    start = text.find(focus)
    if start < 0:
        raise ValueError('caption_focus_not_in_text')
    return escape(text[:start]) + '<b>' + escape(focus) + '</b>' + escape(text[start + len(focus):])


def render_caption(event, record, *, test=False, limit=CAPTION_LIMIT):
    from us_market_monitor.processors.caption import caption_fingerprint, validate_caption_result

    if record.get('fingerprint') != caption_fingerprint(event, record['model']):
        raise ValueError('stale_caption_summary')
    result = validate_caption_result(record['result'], record['units'], record['source'])
    title = record.get('issuer', '')
    title += (' | ' if title else '') + result['title_ko']
    lead = heading(('[테스트] ' if test else '') + '🇺🇸' + title + '🇺🇸')
    url = escape(event.source_url, quote=True)
    footer = '\n\n' + heading('📍원문 링크') + ' : <a href="' + url + '">' + url + '</a>'
    footer += '\n\n' + heading('🇺🇸그로쓰리서치 U.S 채널[실시간 U.S 주요 알림]')
    footer += '\nhttps://t.me/growth_us'

    def render(items):
        groups = {}
        for item in items:
            text = nominal(polish_korean(item['text_ko'], ''))
            focus = polish_korean(item['focus_ko'], '')
            line = '· ' + emphasize(text, focus)
            groups.setdefault(item['section'], []).append(line)
        lines = [lead]
        for section in SECTIONS:
            if section in groups:
                lines += ['', heading('📍' + section), '\n'.join(groups[section])]
        return '\n'.join(lines) + footer

    # Retain whole facts by materiality; never cut numbers, qualifiers or HTML.
    selected, seen = [], set()
    for item in sorted(result['items'], key=lambda item: item['priority']):
        key = re.sub(r'\s+', '', item['text_ko'])
        if key in seen:
            continue
        if caption_units(render([*selected, item])) <= limit:
            selected.append(item)
            seen.add(key)
    if not selected:
        raise ValueError('caption_budget_too_small')
    text = render(selected)
    if caption_units(text) > limit:
        raise ValueError('caption_too_long')
    return text
