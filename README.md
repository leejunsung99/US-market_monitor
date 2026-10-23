# US Market Radar

업황 변화 필터(`industry_outlook`)는 기업 목록과 관계없이 이익·매출 성장, 마진,
메모리·원자재 가격, 수요·수주·재고 변화 중 수치나 급격한 변화 표현을 담은 기사를
일반 중요 알림으로 분류합니다. CXMT 성장 둔화 기사로 회귀 검증하며, 단순 주가
등락과 추측·과거 사례는 제외합니다. 구체적인 실적·정책 사건의 분류가 우선합니다.
BigGo 자체 수집은 추가하지 않았으므로 해당 출처의 모든 기사가 들어오지는 않습니다.

2026-10-19 알림 범위 확대 시험: `settings/filters.yaml`의 일반 알림 기준은 2점,
계약·투자 기준은 5억 달러입니다. `untracked_event_types`에 지정한 사건은 관심 기업
목록 밖에서도 알립니다. 보안 사고, 규제 조사·리콜, 신용 위험, 주요 제품, 인수합병,
대규모 계약, 생산 차질, 사업 확장·제휴가 대상입니다. 긴급 알림은 기존 7점 기준을
유지하며 광고·루머·중복 차단도 유지합니다. 수집 출처는 기존과 같으며, 전체 시장
피드에서 받은 기사에도 확대된 필터를 적용합니다. 이 설정이 알림 건수의 정확한
두 배를 보장하지는 않습니다. 이후 수신 결과에 따라 사건 유형과 기준을 조정합니다.

무료 공식 출처와 넓은 공식 발표 알림의 설정·적용 범위·접근 제한은 [공식 출처 운영 설명](docs/PUBLIC_SOURCES.md)에 정리했습니다.

미국 증시와 글로벌 금융시장에 영향을 줄 수 있는 새 정책·기업 사건을 수집하고, 규칙 기반으로 **HIGH / CRITICAL**만 Telegram에 전달하는 Python 서비스입니다. 주가 시세와 자동매매는 구현하지 않습니다.

SEC EDGAR JSON, 연준 공식 RSS, Finnhub 시장·기업 뉴스 수집기를 독립 모듈로 구성했습니다. SQLite WAL, 지속적인 중복 방지, 첫 실행 기준점, 전송 상태 관리, 호출 제한, 오류 백오프, 프로세스 잠금과 종료 처리를 포함합니다.

## Windows 11 시작하기

Python 3.11 이상을 설치하고 저장소 폴더에서 PowerShell로 실행합니다.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
if (-not (Test-Path -LiteralPath '.env')) { Copy-Item .env.example .env }
notepad .env
```

또는 `powershell -ExecutionPolicy Bypass -File .\scripts\setup.ps1`로 같은 준비를 수행할 수 있습니다. 기존 `.env`는 덮어쓰지 않습니다. 현재 작업 폴더를 프로젝트 폴더로 유지하거나 `--root "프로젝트 경로"`를 지정하세요.

### 키 발급과 입력 위치

실제 값은 프로젝트 루트의 **`.env`에만 입력**합니다. 환경변수가 이미 설정되어 있으면 `.env`보다 우선합니다.

| 설정 | 발급·입력 방법 | 없을 때 |
| --- | --- | --- |
| `TELEGRAM_BOT_TOKEN` | Telegram [@BotFather](https://t.me/BotFather)에서 `/newbot`으로 발급 | Telegram 발송 비활성화 |
| `TELEGRAM_CHAT_ID` | 채널의 `@채널이름` 또는 숫자 chat ID. 봇을 채널 관리자로 추가하고 메시지 게시 권한 부여 | 토큰과 함께 설정해야 함 |
| `FINNHUB_API_KEY` | [Finnhub](https://finnhub.io/) 계정 대시보드에서 API 키 발급 | Finnhub 전체 비활성화, 감시 범위 축소 로그 출력 |
| `SEC_USER_AGENT` | API 키가 아닌 `프로그램이름 실제연락이메일` 형태의 식별 헤더 | SEC 비활성화 |
| `OPENAI_API_KEY` | 초기 버전에서 사용하지 않는 예약 항목 | 영향 없음 |
| `AI_SUMMARY_ENABLED` | 초기 버전은 `false` 유지 | `true`이면 미구현 기능임을 표시하고 시작 거부 |

공개 채널은 `TELEGRAM_CHAT_ID=@채널이름`을 사용할 수 있습니다. 비공개 채널은 봇이 접근 가능한 채널에 테스트 메시지를 작성한 뒤 [getUpdates](https://core.telegram.org/bots/api#getupdates)의 `channel_post.chat.id`를 확인합니다. 토큰이 포함된 응답이나 URL을 Git·로그·채팅에 공유하지 마세요.

### 실행 명령

```powershell
# 실제 수집 / 발송 없음 / 운영 DB와 기준점 변경 없음
.\.venv\Scripts\python.exe main.py --dry-run --once

# Telegram 연결 확인: 실제 뉴스와 구별되는 테스트 메시지 1개 발송
.\.venv\Scripts\python.exe main.py --test-telegram

# 최초 운영 기준점 생성 및 각 활성 소스 1회 수집
.\.venv\Scripts\python.exe main.py --once

# 지속 실행 (Ctrl+C로 안전하게 종료)
.\.venv\Scripts\python.exe main.py

# 실행 중에도 조회 가능한 읽기 전용 상태 출력
.\.venv\Scripts\python.exe main.py --status

# 중요도 검토 후보(알람 미발송)와 제외 이유 집계
.\.venv\Scripts\python.exe main.py --review

# 뉴스 저장·발송 없이 공식 소스 실제 연결 및 파싱 점검
.\.venv\Scripts\python.exe scripts/check_sources.py
```

`python -m us_market_monitor` 및 설치된 `us-market-monitor` 명령도 같은 옵션을 지원합니다. `--once`는 모든 소스의 첫 수집이 끝나면 종료하고, 소스 오류가 있으면 종료 코드 `1`을 반환합니다. 설정 오류·중복 프로세스는 `2`입니다. 키가 없는 선택 소스는 오류를 가장하지 않고 명시적으로 건너뜁니다. 수집 간격은 실행 시간과 호출 제한으로 인해 길어질 수 있습니다.

`--dry-run`은 인메모리 DB를 사용하며 Telegram을 호출하지 않습니다. 새 임시 기준점을 만들기 때문에 정상적인 첫 실행에서는 미리 있던 기사 대신 **시작 이후 발표된 사건**만 출력합니다. 기존 기사를 표시하는 점검 명령은 `scripts/check_sources.py`이며 Telegram 메시지를 보내지 않습니다. 운영 기준점은 `--once` 또는 지속 실행을 시작할 때 저장합니다.

## 수집과 중요도 판정

### 특정 날짜 뉴스 재생 테스트

상시 감시를 잠시 중단한 상태에서 아래 명령으로 별도 테스트 DB에 해당 KST 날짜의 중요 사건을 먼저 수집·저장할 수 있습니다. 수집 종료 시각 이후 사건은 포함하지 않습니다. 일반·추측 기사는 제외하며, 남은 중요 사건이 5개보다 적으면 그 건수만 전송합니다.

```powershell
.\.venv\Scripts\python.exe scripts/replay_day.py --date 2026-10-13 --database data/replay-20261013.sqlite3
.\.venv\Scripts\python.exe scripts/replay_day.py --database data/replay-20261013.sqlite3 --send --limit 5 --spacing 10
```

수집은 API 요청 간격을 두고 수행하고, 발송은 저장된 사건의 발표 시간순으로 최대 5개만 보냅니다. 메시지에는 과거 실제 뉴스 재생 테스트임을 표시합니다. 동일 테스트 DB로 명령을 다시 실행해도 이미 발송되거나 결과가 불명확한 전송을 포함해 5개 한도를 유지합니다. 수집 명령을 다시 실행하려면 다른 테스트 DB 이름을 사용하세요. 테스트 종료 후 상시 감시를 다시 실행해야 합니다.

| 소스 | 구현 | 제한·검증 |
| --- | --- | --- |
| SEC EDGAR | 공식 티커→CIK 매핑, 회사별 submissions JSON, 8-K/6-K 및 수정 공시, 중요한 8-K item 선별, 해당 공시의 99.x HTML 첨부 최대 2개 | SEC 공유 요청 간격 0.25초. 공시가 존재한다는 사실만으로 승인하지 않음 |
| Federal Reserve | 통화정책, 은행·소비자 규제, 연설·증언 RSS 및 링크된 공식 본문 | 최근 사건의 공식 본문만 가져오며 사이트 메뉴·일반 페이지는 분류하지 않음 |
| Finnhub | `/news?category=general`, 종목별 `/company-news` | 기본 전체 30회/분으로 제한. 계정 플랜에 맞춰 더 낮출 수 있음. 403은 접근/플랜 확인 필요 |

SEC는 `data.sec.gov/submissions/CIK##########.json`을 사용합니다. [SEC API 문서](https://www.sec.gov/search-filings/edgar-application-programming-interfaces), [접근 정책](https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data), [연준 RSS 목록](https://www.federalreserve.gov/feeds/feeds.htm), [Finnhub 시장 뉴스](https://finnhub.io/docs/api/market-news), [기업 뉴스](https://finnhub.io/docs/api/company-news), [호출 제한](https://finnhub.io/docs/api/rate-limit), [Telegram Bot API](https://core.telegram.org/bots/api#sendmessage)를 기준으로 구현했습니다.

`settings/watchlist.yaml`에서 회사명 별칭·종목·산업을 바꿀 수 있습니다. `settings/filters.yaml`에서 사건별 정규식, 제외·추측 표현, 중요도 임계값, 대규모 거래 기준과 중복 판정 창을 조절할 수 있습니다. 설정 변경은 프로세스를 재시작하면 적용됩니다.

### 산업 사건·경쟁관계와 검토함

`settings/industry_relations.yaml`은 CXMT·YMTC·삼성전자·SK하이닉스·SMIC와 관심 종목의 **감시용 경쟁관계**를 관리합니다. 관계 목록은 수집 대상 티커를 늘리는 설정이 아니라, 수집된 기사에서 관심 목록 밖 경쟁사를 인식하는 설정입니다. 현재 수집 API에 없는 기사를 새로 확보하는 기능은 아닙니다.

반도체 규칙에는 DRAM·DDR5·LPDDR·HBM·NAND·4F² 메모리 셀 구조 및 밀도, GPU·AI 가속기 등을 포함합니다. 기술 진전 발표, 출시 계획 발표, 실제 출시, 양산 시작을 각각 구분하며, 회사의 발표·발언을 인용한 근거가 있는 계획만 중요 후보로 승인합니다. 기사에 등장하는 계획·예상 문구만으로 출시나 양산 완료를 판단하지 않습니다. 단순 하락 설명 제목이어도 같은 문장 안에 회사 주체·기술·새 발표 근거가 있으면 해당 사건을 추출할 수 있습니다. 광고·매수 추천·목표주가·회고·루머는 계속 제외합니다.

중요도는 현재 사건 근거, 관련 주체, 기술 중요성, 발표 인용, 공식 출처와 긴급성 점수를 합산합니다. 점수 가중치·HIGH/CRITICAL 임계값은 `filters.yaml`에 있습니다. 기술 발표·출시 계획은 기본 HIGH이며 주가 하락 표현만으로 CRITICAL로 올리지 않습니다. 알람에는 사건 단계와 확인 근거를 표시하고, 원문에서 언급된 종목과 설정된 경쟁관계의 관찰 대상을 구분합니다. 언론 보도의 회사 발표 인용은 공식 원문을 독립 검증한 것으로 표시하지 않습니다.

확신이 부족한 산업 후보는 별도 `review_candidates` 테이블에 제목 350자·근거 발췌 700자·출처·판정 이유를 **기본 7일, 최대 500건**만 보관합니다. 검토 후보는 `events`나 Telegram 발송 큐에 들어가지 않습니다. 일반 기사는 여전히 본문 없이 해시만 남기고, 제외 이유의 건수만 30일 보관합니다. `--review`로 유효한 후보와 제외 이유를 조회할 수 있으며, 이 조회는 실행 중에도 가능합니다.

규칙·별칭·경쟁관계의 내용으로 버전 해시를 만들므로 설정 변경 후 재시작하면 기존 관측 해시를 새 규칙으로 다시 평가할 수 있습니다. 발송 사건의 지속적인 중복 검사는 유지하며 최초 시작 기준점 이전 기사까지 자동으로 소급 전송하지 않습니다. 검토 후보나 새 키워드가 기준을 자동 변경하지는 않습니다. 후보 검토 → 규칙/관계 수정 → 회귀 테스트 → 재시작 순서로 최신화합니다. 실제 주가 반응에 대한 수치 검증, 매체 간 독립적인 사실 확인, 규칙의 자동 학습은 구현하지 않았습니다.

일반 주가 설명·목표주가·홍보·회고·루머를 먼저 제외한 뒤, **실제 발표·결정·수정·계약·중단을 나타내는 사건 근거**와 관련 기업·정책 주체를 함께 확인합니다. 기업 뉴스 API의 조회 종목만으로 관련 기업을 표시하지 않습니다. 인수·투자는 기본 10억 달러 이상 또는 원문에 명시된 대규모·전략적 거래 근거를 요구합니다. 핵심 GPU/반도체 기술 출시도 구체적인 차세대 기술 발표 근거가 필요합니다.

이 규칙은 의미를 완전히 이해하는 분류 모델이 아닙니다. 복잡한 문맥·새 표현은 놓칠 수 있고 명확하지 않은 사건은 제외하는 방향으로 작동합니다. 관심 목록 밖 기업은 원문에서 세계 최대·시스템 중요성 등 명시적인 근거가 있는 경우에 한해 후보로 다룹니다.

**CPI·PPI·고용·GDP·개인소득/소비(PCE) 공식 발표를 별도로 감시합니다.** `.env`의 네 RSS 주소를 사용하며 `ECONOMIC_ENABLED=true`, `ECONOMIC_INTERVAL_SECONDS=120`으로 제어합니다. BEA 피드에서는 전국 GDP와 Personal Income and Outlays 발표만 선택합니다. BLS는 피드 등록 시각이 발표 전일 수 있어 최소 오전 8:30(뉴욕시간)까지 기다립니다. 처음 추가되는 네 소스만 `ECONOMIC_INITIAL_LOOKBACK_HOURS=24`시간을 소급하며, 기존 소스의 기준점과 전송 이력은 유지합니다. `python main.py --economic-only --once`로 경제지표만 한 번 수집·발송할 수 있습니다(상시 실행과 동일한 프로세스 잠금 사용).

**예상 대비 차이 알람은 아직 비활성입니다.** 공식 발표값을 보내며 컨센서스나 시장 반응을 만들어내지 않습니다. 공식 RSS의 갱신 지연·접근 제한에 따라 알림이 늦어질 수 있습니다. 다른 경제지표와 정부 정책·지정학 뉴스를 전부 감시하는 구성은 아니며, 일반 뉴스 범위는 Finnhub 제공 범위에도 의존합니다.

경제지표는 이미지 없이 텍스트 목록으로 전송합니다. 확인된 CPI·PPI·고용·GDP 수치 형식은 한국어 항목으로 직접 표시하며, 인식하지 못하는 형식이나 PCE 세부 설명은 공식 원문 발췌로 표시합니다. 계절조정 여부와 지표 종류가 기계 번역으로 바뀌지 않도록 경제지표에는 번역 API를 호출하지 않습니다.

## 메시지 언어와 근거

한국어 제목과 항목별 요약을 제공합니다. 원문에서 완결된 핵심 문장을 최대 4개 골라 핵심 발표·실적·성능 수치·참여 기업·향후 계획 등으로 표시합니다. 전망과 테스트 조건은 해당 문장에 유지하며, 원문에 없는 시장 영향이나 사실을 생성하지 않습니다. 잘린 소개 문구, 반복 문장, SEC 표지의 주소·등록 정보·법적 안내는 요약에서 제외합니다.

Telegram 뉴스는 미국 국기가 있는 제목 → 카테고리별 핵심 내용 → 원문 링크·채널 안내로 표시합니다. 제목과 카테고리는 굵게·밑줄을 적용하고, 공식 실적 발표와 기사 보도를 제목에서 구분합니다. 짧은 목록은 붙여 쓰고 긴 항목 전후에는 빈 줄을 넣습니다. 문장 수와 요약 전체 글자 수를 고정하지 않고 중복·홍보성 내용을 제외한 중요한 사실을 발췌합니다. SEC 본문과 최대 2개 99.x 첨부를 읽으며, 일반 기사는 기사 본문을 소개 문구보다 우선합니다. 중요 사건은 확보한 본문과 발췌를 함께 DB에 보관합니다. 이미지가 없거나 로고로 식별되면 생략하며, 긴 알림은 이미지와 본문을 분리하고 본문도 메시지 길이에 맞춰 나눕니다. Azure 번역은 필요 시 여러 요청으로 나누며 숫자·범위·단위를 보호합니다. 운영 뉴스는 번역 실패 또는 한국어 검증 실패 시 영문을 발송하지 않고 보류 후 재시도합니다. 한국어 수치 템플릿을 확보하지 못한 경제지표도 보류합니다. 재시도 횟수·알림 유효시간 제한은 유지합니다. 운영은 규칙 기반 발췌·Azure 번역을 사용하며 로컬 LLM은 별도 테스트 경로입니다. DB·로그 시각은 UTC입니다.

2026-10-14 규칙부터 해설 영상·팟캐스트·진행자 토론·회고 기사는 제외합니다. 비공식 실적 기사는 제목·요약에 매출, EPS, 순이익 또는 전망의 구체적인 달러 수치가 있어야 하며, 수치 없는 기사는 검토 대상으로 남깁니다. 공식 발표는 수치 없이도 사건 근거가 확인되면 통과할 수 있고, 같은 중요도에서는 공식 출처부터 전송합니다. 실적 발표와 전망 변경은 같은 기업의 같은 실적 사건으로 묶습니다. 후속 기사는 기존에 없던 수치·변경된 수치 또는 전망 철회가 확인될 때만 전송하며, 단위 차이·일부 수치 생략·상향이라는 표현의 반복은 새 사실로 보지 않습니다. 규칙은 원문 표현만 비교하므로 표에만 있는 수치나 복잡한 문맥을 놓칠 수 있습니다. 검토 후보는 자동 발송하지 않습니다.

## 기준점·중복·전송 복구

- 소스별 최초 시작 시각을 네트워크 요청 **이전**에 커밋합니다. 공급자의 초 단위 시간 정밀도를 고려해 시작 시각을 초 단위로 내림하므로 경계의 1초 이내 사건을 포함할 수 있습니다. 그 이전 사건은 버리고, 최초 요청 중에 발표된 사건은 처리합니다. 첫 요청이 실패해도 기준점을 새로 만들지 않습니다.
- 재시작할 때 기존 기준점을 유지하고 마지막 성공 시각에서 최대 2일의 겹치는 범위를 조회합니다. 공급자의 RSS·뉴스 보관 범위를 넘어선 장시간 장애는 복구 가능한 범위를 제한합니다. Finnhub 일반 뉴스는 API가 반환하는 최근 기사 범위에 의존합니다.
- 원본 ID·정리된 URL·정규화 제목 해시 및 기업/정책·사건유형·발표 시각을 비교합니다. 유사 제목과 짧은 시간창의 동일 주체 사건으로 매체 간 중복을 줄입니다. 국가가 다른 관세 정책은 분리합니다.
- 정책 확정·규제 확대·전망 상하향·수정 등 중요한 변화와 수치 변경 근거가 있으면 후속 알림을 허용합니다. 규칙 기반 유사 사건 판정에는 오탐·누락 가능성이 있으므로 `dedup_window_hours`와 `dedup_similarity`를 운영 피드백에 맞춰 조절하세요.
- 일반 뉴스는 `events`에 저장하지 않습니다. 일반 뉴스의 최소 관측 해시만 `seen`에 보관하고 30일 뒤 정리합니다. `events`에는 중요 사건의 제목·발췌와 수집한 본문을 보관해 재요약 시 앞선 축약으로 정보가 손실되지 않도록 합니다. 과거에 저장하지 않은 본문은 자동 복원되지 않습니다.
- 중요 사건 저장과 `pending` 전송 큐 생성은 같은 SQLite 트랜잭션입니다. `sent`는 다시 보내지 않습니다. 전송 상태는 `pending / sending / sent / failed / unknown / expired`입니다.
- 확실한 미전송 연결 타임아웃·429는 지수 백오프와 `retry_after`에 따라 최대 8회 시도합니다. 동일 채널은 기본 3.1초 이상 간격으로 보냅니다. 잘못된 토큰·권한·대상 등 확정적인 거부는 `failed`로 남깁니다.
- 읽기 타임아웃·불명확한 5xx 응답·전송 중 종료는 `unknown`으로 보관하여 자동 중복을 막습니다. Telegram Bot API에는 이 서비스를 위한 전송 멱등성 키가 없으므로 완전한 exactly-once 보장은 불가능합니다. 해당 채널에서 실제 게시 여부를 확인해야 합니다.
- `MAX_NOTIFICATION_AGE_HOURS`보다 오래된 미발송 사건은 `expired`로 전환합니다. 기본 24시간입니다. Telegram 설정이 없으면 중요 사건은 이 제한 안에서 대기합니다.

`unknown` 수동 처리는 서비스를 정지하고 DB를 백업한 뒤 실제 채널의 게시 여부를 확인하여 수행합니다. 재발송이 필요하면 아래처럼 특정 이벤트만 `pending`으로 되돌릴 수 있습니다. 이미 게시된 사건에 이 작업을 하면 중복 메시지가 생깁니다.

```sql
SELECT event_id, status, error_message FROM notifications WHERE status IN ('unknown', 'failed');
-- 확인된 특정 ID만 선택; 아래 ?는 SQLite 클라이언트에서 실제 ID로 대체
UPDATE notifications SET status='pending', retry_count=0,
  error_message=NULL, next_attempt_at='1970-01-01T00:00:00+00:00'
WHERE event_id=? AND status IN ('unknown','failed');
```

프로세스 잠금은 같은 `DATA_DIR`의 중복 실행을 막습니다. 별도 DB·서버에서 동일 채널로 실행하는 구성은 이 잠금의 범위 밖입니다. Windows OneDrive 폴더에서는 실행 중 SQLite 파일의 동기화 충돌을 피하도록 실제 운영 `DATA_DIR`을 동기화되지 않는 로컬 경로로 지정하는 편이 좋습니다.

## 상시 실행

### Windows 작업 스케줄러

키·소스 점검 후, 현재 로그인한 사용자의 로그온 시 실행하려면 아래 작업을 등록할 수 있습니다. 관리자 권한이 필요하면 해당 사용자 권한으로 실행합니다.

```powershell
$projectPath = (Get-Location).Path
$action = New-ScheduledTaskAction -Execute (Join-Path $projectPath '.venv\Scripts\python.exe') -Argument 'main.py' -WorkingDirectory $projectPath
$trigger = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName 'US Market Radar' -Action $action -Trigger $trigger -Settings $settings -Description 'Important US market news to Telegram'
```

이 예시는 로그온 이후 실행입니다. 로그인 전부터 운영하려면 작업 스케줄러에서 시작 시 트리거와 해당 계정의 비로그온 실행을 별도로 설정하세요. PC 절전·종료 중에는 수집할 수 없습니다. 서비스는 실제 네트워크 장애 시 내부적으로 재시도하고, 작업 스케줄러는 프로세스 종료 시 다시 시작합니다.

### Ubuntu / Oracle Cloud systemd

```bash
sudo apt-get update
sudo apt-get install -y python3 python3-venv git
sudo useradd --system --home /opt/us-market-monitor --shell /usr/sbin/nologin radar
sudo git clone https://github.com/leejunsung99/US-market_monitor.git /opt/us-market-monitor
sudo chown -R radar:radar /opt/us-market-monitor
cd /opt/us-market-monitor
sudo -u radar python3 -m venv .venv
sudo -u radar .venv/bin/python -m pip install .
sudo -u radar cp .env.example .env
sudo chmod 600 .env
sudo nano .env
sudo -u radar mkdir -p data logs
sudo -u radar .venv/bin/python main.py --dry-run --once
sudo -u radar .venv/bin/python main.py --test-telegram
sudo cp deploy/us-market-radar.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now us-market-radar
sudo systemctl status us-market-radar
journalctl -u us-market-radar -f
```

Python은 3.11 이상이어야 합니다. 다른 설치 경로·사용자·데이터 경로를 사용하면 서비스 파일의 `User`, `Group`, `WorkingDirectory`, `ExecStart`, `ReadWritePaths`를 수정하세요. 수집기는 개별 실패를 격리하므로 네트워크 장애만으로 전체 프로세스를 재시작하지 않습니다. 활성 수집기의 성공 시각과 `error_count`를 `--status`로 확인하세요. 이번 작업에서는 systemd 서비스나 Windows 예약 작업을 실제 등록하지 않았습니다.

## 파일 구성

```text
main.py                         # 실행 진입점
pyproject.toml / requirements.txt
.env.example / .gitignore
settings/watchlist.yaml          # 37개 기본 관심 기업과 별칭·산업
settings/filters.yaml            # 사건 규칙·임계값·중복 설정
settings/industry_relations.yaml # 관심 종목 밖 경쟁사와 산업 관계
src/us_market_monitor/
  config.py / http.py / main.py / __main__.py
  collectors/sec.py / fed.py / finnhub.py
  processors/normalizer.py / classifier.py / deduplicator.py / summarizer.py
  database/models.py / repository.py
  notifications/telegram.py / formatter.py
  scheduler/jobs.py
tests/                          # 모의 응답, 분류, 저장, 중복, 복구, 전송 테스트
scripts/setup.ps1 / check_sources.py / replay_day.py
deploy/us-market-radar.service
.github/workflows/test.yml       # Windows/Ubuntu, Python 3.11–3.13 CI
docs/VALIDATION.md               # 실연동 결과와 남은 검증
data/ / logs/                   # 로컬 런타임 파일, Git 제외
```

## 테스트와 검증

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m pip check
```

로컬 Windows / Python 3.12에서 **119개 테스트 통과**, Ruff 정적 검사와 `pip check` 통과. 초기 테스트는 GitHub CI의 Windows/Ubuntu × Python 3.11/3.12/3.13 조합도 모두 통과했습니다. 상세 결과와 실제 API 검증 범위는 [docs/VALIDATION.md](docs/VALIDATION.md)에 기록했습니다. 테스트는 외부 네트워크나 실제 Telegram 전송을 사용하지 않습니다. 외부 수집 성공은 별도의 실연동 점검 결과로만 보고합니다.

## 공개 저장소와 비밀 설정

`.env`, `.env.*`(비밀값 없는 `.env.example` 제외), 인증키, 가상환경, 로그, SQLite·런타임 데이터는 Git에서 제외합니다. 인증 헤더와 토큰을 포함한 URL·응답 본문을 로그에 기록하지 않으며, 추가로 설정한 비밀값을 로그 포맷터에서 가립니다. 키를 소스·테스트·README에 넣지 마세요. CI는 모의 응답 테스트만 수행하므로 개인 API 키가 필요하지 않습니다.
