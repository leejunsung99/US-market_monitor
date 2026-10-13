# 한국어 번역 API 선택

확인일: 2026-10-13. 중요도 판정은 Python 규칙으로 유지하고, 중복을 제거한 중요 사건의 **제목과 짧은 발췌만 번역**하는 구성을 권장합니다. 이번 변경에는 번역 호출을 추가하지 않았습니다.

| API | 한국어·과금 조건 | 이 프로젝트에서의 용도 |
| --- | --- | --- |
| Google Cloud Translation NMT | 한국어 `ko` 지원. NMT 텍스트 번역 월 최초 50만 자 무료 크레딧, 기본 종량제 초과분 100만 자당 US$20 | 번역 모델을 NMT로 명시하고 LLM 번역 옵션을 사용하지 않으려는 경우 |
| Azure Translator Standard | 한국어 지원. F0 플랜 월 200만 자 무료. 유료 요금은 지역·플랜의 현재 견적 확인 | 무료 범위를 우선하는 소규모 알람 서비스 |
| NAVER Cloud Papago Text Translation | 한국어·영어 지원. 공식 요금표 100만 자당 20,000원. 월 50만 자 사용 예시도 20,000원으로 제시되어 있어 최소 과금·단위 및 세금은 계정에서 확인 | 한국어 금융 용어와 용어집을 실제 기사 샘플로 비교하려는 경우 |
| DeepL API | 기존 API Free/Pro는 신규 가입 중단. 신규 API Developer는 무료 총 100만 자, 월별 재설정 없음. 이후 Growth 가입 조건 확인 | 소량 번역 품질을 비교할 때. 월 50만 자 무료 조건을 신규 가입 기준으로 안내하지 않음 |

추천은 **Google NMT 또는 Azure Standard**부터 시작하는 것입니다. DeepL에는 차세대 언어 모델 옵션도 있으므로 제공사 내부 모델까지 LLM을 배제해야 한다면 모델 조건을 따로 확인해야 합니다. 번역 API도 내부적으로 기계학습 모델을 사용하며, 외부 번역 API를 쓴다는 것과 규칙 기반 중요도 판정은 별개의 사항입니다.

번역 품질의 우열은 이 작업에서 직접 측정하지 않았습니다. 같은 금융 기사 20~30개로 숫자·티커·부정문·출시 계획/완료·기술 용어 보존을 비교한 뒤 선택하세요. 도입 시에는 번역 결과 캐시, 실패 시 원문 표시, 원문 링크 유지, 월 사용량 제한과 키 비출력을 함께 구현해야 합니다.

## 공식 자료

- [Google NMT 요금](https://cloud.google.com/products/translate/pricing), [지원 언어](https://docs.cloud.google.com/translate/docs/languages)
- [Azure Translator 요금](https://azure.microsoft.com/en-us/pricing/details/translator/), [지원 언어](https://learn.microsoft.com/en-us/azure/ai-services/translator/language-support)
- [Papago Translation 서비스](https://www.ncloud.com/product/aiService/papagoTranslation), [사용 조건](https://guide.ncloud-docs.com/docs/en/papagotranslation-spec), [NAVER Cloud 요금표](https://www.ncloud.com/charge/region/ko)
- [DeepL API 플랜](https://support.deepl.com/hc/en-us/articles/360021200939-DeepL-API-plans)
