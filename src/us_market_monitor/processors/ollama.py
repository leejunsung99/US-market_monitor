"""Production local translation and evidence-backed Korean outline summaries."""

import hashlib
import json
import logging
import os
import re
import subprocess
from datetime import UTC, datetime

import requests

from us_market_monitor.notifications.compact import nominal, notification_category
from us_market_monitor.processors.briefing import polish_korean, select_points
from us_market_monitor.processors.ollama_grounded import GroundedOllama
from us_market_monitor.processors.ollama_preview import validate_result

log = logging.getLogger(__name__)
VERSION = 'ollama-production-v2-grounded'


def ensure_gpu_server(config):
    """Start the existing project GPU helper if its local Windows endpoint is down."""
    if os.name != 'nt' or config.ollama_base_url != 'http://127.0.0.1:11435':
        return
    session = requests.Session()
    session.trust_env = False
    try:
        session.get(config.ollama_base_url + '/api/version', timeout=3).raise_for_status()
        return
    except requests.RequestException:
        helper = config.root / 'scripts/start_ollama_gpu.ps1'
        if not helper.exists():
            return
        subprocess.run(['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(helper)],
                       cwd=config.root, capture_output=True, timeout=30,
                       creationflags=subprocess.CREATE_NO_WINDOW, check=False)


def source_text(event):
    raw = event.body or event.article_summary or event.summary
    points = select_points(raw, event.title)
    points = [point for point in points if not point.rstrip().endswith(('...', '…'))]
    if (event.event_type in {'contract_investment', 'business_development'}
            and ('/PRNewswire/' in raw or event.source.startswith('Official/'))):
        # Commercial releases often append descriptions of existing partner
        # programs. Retain the announcement, allocation, timing and constraints,
        # rather than spending GPU time translating generic corporate benefits.
        material = re.compile(
            r'[$%\d]|\b(?:one|two|three|four|five|six|seven|eight|nine|ten)[- ]years?\b|'
            r'\b(?:announces?|announced|commits?|committed|commitment|will|plans?|planned|'
            r'expanded|expands|subject to|approval|conditions?|risks?|costs?|capacity)\b', re.I,
        )
        points = [point for point in points if material.search(point)]
    return event.title + ('\n' + '\n'.join(points) if points else '')


def fingerprint(event, model):
    return hashlib.sha256((VERSION + model + source_text(event)).encode()).hexdigest()


def category_for(claim):
    text = claim['evidence']
    if re.search(r'guidance|outlook|forecast|expects?|plans?|will\b|would\b|could\b', text, re.I):
        return '향후 전망'
    if re.search(r'cash flow|capital expenditures|capex', text, re.I):
        return '투자·현금흐름'
    if re.search(r'revenue|income|profit|EPS|per share|margin', text, re.I):
        return '주요 실적'
    if re.search(r'CEO|CFO|said|Zuckerberg|Neumann', text, re.I):
        return '경영진·주요 발언'
    if re.search(r'acqui|agreement|contract|merger', text, re.I):
        return '주요 계약·투자'
    return '주요 내용'


def card_sections(record):
    def render(claim):
        text = nominal(polish_korean(claim['text_ko'], ''))
        basis = claim['evidence']
        if (re.search(r'gross margins?', basis, re.I)
                or (re.search(r'gross margins?', record['source'], re.I)
                    and not re.search(r'net margins?|operating margins?', record['source'], re.I))):
            text = text.replace('순이익률', '매출총이익률').replace('영업이익률', '매출총이익률')
        if re.search(r'warranty true downs?', record['source'], re.I):
            text = text.replace('보증 비용 감소', '보증충당금 하향 조정')
        return text.replace('관세 환불', '관세 환급')

    groups = {'핵심내용': [render(record['result']['summary'])]}
    seen = {record['result']['summary']['text_ko']}
    evidence_seen = {record['result']['summary']['evidence']}
    for point in record['result']['points']:
        if point['text_ko'] in seen:
            continue
        if len(record['source']) < 500 and point['evidence'] in evidence_seen:
            continue
        seen.add(point['text_ko'])
        evidence_seen.add(point['evidence'])
        text = render(point)
        text = text.replace('자유 현금 흐름', '잉여현금흐름').replace('자유현금흐름', '잉여현금흐름')
        if 'Spencer Neumann' in record['source']:
            text = text.replace('네이먼', '노이만').replace('뉴먼', '노이만')
            text = text.replace('잠재된 구독', '휴면 구독')
        groups.setdefault(category_for(point), []).append(text)
    return groups


class OllamaProcessor:
    background = True

    def __init__(self, config):
        self.client = GroundedOllama(config.ollama_model, config.ollama_base_url,
                                    timeout=config.ollama_timeout)

    def translate(self, event):
        if event.event_type == 'monitor_alert':
            return True
        key = fingerprint(event, self.client.model)
        record = event.metadata.get('ollama_summary', {})
        if record.get('fingerprint') == key:
            try:
                validate_result(record['result'], record['source'])
                return True
            except (ValueError, KeyError, TypeError):
                event.metadata.pop('ollama_summary', None)
        source = source_text(event)
        work = event.metadata.get('ollama_work', {})
        if work.get('fingerprint') != key:
            work = {'fingerprint': key}
        event.metadata['ollama_work'] = work
        try:
            category = notification_category(event)
            result, stats = self.client.summarize_resumable(source, {
                'category': 'earnings' if category in {'실적발표 요약', '실적 관련 보도'} else category,
                'source': event.source, 'source_url': event.source_url,
                'style': '한국어 개조체', 'issuer_tickers': list(event.related_tickers),
            }, work)
            undisclosed = re.search(r'[^.\n]*has not yet released full-year (20\d{2}) guidance[^.\n]*', source)
            if undisclosed and not any('미발표' in p['text_ko'] or '미제시' in p['text_ko']
                                       for p in result['points']):
                result['points'].append({'text_ko': undisclosed[1] + '년 연간 가이던스: 아직 미발표',
                                         'evidence': undisclosed[0]})
            validate_result(result, source)
            for claim in [result['summary'], *result['points']]:
                if re.search(r'습니다|입니다|했습니다', claim['text_ko']):
                    raise ValueError('not_outline_style')
            record = {'fingerprint': key, 'source': source, 'result': result,
                      'model': self.client.model, 'version': VERSION, 'stats': stats}
            event.metadata['ollama_summary'] = record
            event.metadata.pop('ollama_work', None)
            event.metadata.pop('processing_failure', None)
            event.title_ko = result['title_ko']
            event.summary_ko = '\n'.join(claim['text_ko'] for claim in [result['summary'], *result['points']])
            event.translation_hash = 'ollama:' + key
            log.info('Ollama Korean summary ready source=%s seconds=%s points=%d',
                     event.source, stats.get('wall_seconds'), len(result['points']))
            return True
        except (ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
            code = str(error) if re.fullmatch('[a-z_]{1,70}', str(error)) else type(error).__name__
            self.last_failure = {'code': code, 'result': getattr(error, 'result', None),
                                 'stats': getattr(error, 'stats', None)}
            event.metadata['processing_failure'] = {
                'code': code, 'at': datetime.now(UTC).isoformat(), 'version': VERSION,
            }
            log.warning('Ollama validation failed (%s); delivery held', code)
            return False
        except Exception as error:
            # Transport exception messages can contain URLs. Do not log bodies.
            log.warning('Ollama request failed (%s); delivery held', type(error).__name__)
            event.metadata['processing_failure'] = {
                'code': type(error).__name__, 'at': datetime.now(UTC).isoformat(), 'version': VERSION,
            }
            return False
