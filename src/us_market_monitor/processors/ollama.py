"""Production local translation and evidence-backed Korean outline summaries."""

import copy
import hashlib
import json
import logging
import os
import re
import subprocess

import requests

from us_market_monitor.notifications.compact import nominal, notification_category
from us_market_monitor.processors.briefing import polish_korean, select_points
from us_market_monitor.processors.ollama_preview import OllamaPreview, validate_result

log = logging.getLogger(__name__)
VERSION = 'ollama-production-v1'
STYLE = '''
운영 알림용 한국어 개조체로 작성합니다. 모든 항목은 짧은 명사형 또는
"~했음", "~예상", "~계획", "~유지", "~전망"으로 끝냅니다.
"~했습니다", "~습니다", "~입니다" 같은 기사체와 존댓말은 쓰지 않습니다.
한 항목에 하나의 사실/수치만 작성하고 중요한 내용은 개수 제한 없이 포함합니다.
경영진/외부 인사의 발언이면 발언 주체를 보존합니다. 전망을 확정 사실로 쓰지 마세요.
공식 발표, 기사 보도, 실적 발표 일정은 metadata.category를 따라 구분합니다.
수치가 없는 짧은 속보는 제목의 의미만 한국어로 전달합니다. 내용을 만들어 늘리지 마세요.
금액·비율은 원문 토큰 그대로 유지합니다. 서로 다른 기간/기준의 수치를 합치지 마세요.
광고·회사 소개·기자 소개·관련 기사 추천은 요약에서 제외합니다.
연간 가이던스 미제시·미확정 사항도 중요하면 포함하세요. 한 항목의 근거에 없는
연도·분기·수치를 다른 문장에서 끌어와 합치지 마세요. 모든 숫자는 해당 항목 근거에 있어야 합니다.
'''


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
        self.client = OllamaPreview(config.ollama_model, config.ollama_base_url,
                                    timeout=config.ollama_timeout)
        self.client.system_prompt += STYLE

    def _generate(self, source, metadata):
        try:
            return self.client.summarize(source, metadata)
        except ValueError as error:
            candidate = getattr(error, 'result', None)
            if not candidate:
                raise
            candidate = copy.deepcopy(candidate)
            stats = dict(getattr(error, 'stats', {}) or {})
            repairs = []
            for claim in [candidate['summary'], *candidate['points']]:
                check = {**candidate, 'summary': claim, 'points': [claim]}
                try:
                    validate_result(check, source)
                except ValueError:
                    quote = claim.get('evidence', '')
                    if quote not in source:
                        prefix = quote.rstrip('."\'”’ ')
                        if len(prefix) >= 30 and prefix in source:
                            start = source.index(prefix)
                            end = start + len(prefix)
                            while end < len(source) and source[end] in '."\'”’':
                                end += 1
                            claim['evidence'] = source[start:end]
                        # Only trailing punctuation can be restored. Different
                        # words/numbers never qualify as an exact source quote.
                        try:
                            validate_result({**candidate, 'summary': claim, 'points': [claim]}, source)
                            continue
                        except ValueError:
                            pass
                    if len(repairs) >= 2 or claim.get('evidence', '') not in source:
                        raise error from None
                    # Re-generate only the failed claim from its exact evidence.
                    # No unsupported facts are removed or silently approved.
                    fixed, timing = self.client.summarize(claim['evidence'], {
                        'category': 'claim_repair', 'style': '한국어 개조체',
                        'instruction': 'Translate ONLY this evidence. No other years or numbers.',
                    })
                    claim.update(fixed['summary'])
                    repairs.append(timing)
            validate_result(candidate, source)
            stats['repairs'] = repairs
            stats['wall_seconds'] = round(stats.get('wall_seconds', 0)
                                          + sum(item.get('wall_seconds', 0) for item in repairs), 2)
            return candidate, stats

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
        try:
            category = notification_category(event)
            result, stats = self._generate(source, {
                'category': 'earnings' if category in {'실적발표 요약', '실적 관련 보도'} else category,
                'source': event.source, 'source_url': event.source_url,
                'style': '한국어 개조체', 'issuer_tickers': list(event.related_tickers),
            })
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
            log.warning('Ollama validation failed (%s); delivery held', code)
            return False
        except Exception as error:
            # Transport exception messages can contain URLs. Do not log bodies.
            log.warning('Ollama request failed (%s); delivery held', type(error).__name__)
            return False
