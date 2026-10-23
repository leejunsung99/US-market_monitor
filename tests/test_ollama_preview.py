import copy

import pytest

from us_market_monitor.processors.ollama_preview import (
    OllamaPreview,
    protect_numbers,
    restore_numbers,
    validate_result,
)

SOURCE = "Meta reported revenue growth of 26%. Net income was $9.9 billion."


def valid_result():
    return {
        "title_ko": "메타 실적 발표",
        "summary": {"text_ko": "메타 매출이 26% 증가했습니다.", "evidence": SOURCE},
        "points": [{"text_ko": "순이익은 $9.9 billion입니다.", "evidence": SOURCE}],
        "limitations_ko": "언론 보도 기준입니다.",
    }


def test_accepts_source_backed_korean_summary():
    assert validate_result(valid_result(), SOURCE)["title_ko"] == "메타 실적 발표"


def test_double_digit_wording_is_equivalent_to_two_digit_korean():
    source = 'The company expects double-digit revenue growth.'
    result = valid_result()
    result['title_ko'] = '매출 성장 전망'
    result['summary'] = {'text_ko': '2자리 매출 성장 예상', 'evidence': source}
    result['points'] = [result['summary']]
    result['limitations_ko'] = ''
    validate_result(result, source)


@pytest.mark.parametrize("text", ["매출이 99% 증가했습니다.", "순이익은 $9.9 billion, 전년 $8.1 billion입니다."])
def test_rejects_invented_or_converted_numbers(text):
    result = valid_result()
    result["summary"]["text_ko"] = text
    with pytest.raises(ValueError, match="unsupported_claim_numbers"):
        validate_result(result, SOURCE)


def test_rejects_fabricated_evidence():
    result = valid_result()
    result["summary"]["evidence"] = "Meta reported revenue growth of 99%."
    with pytest.raises(ValueError, match="evidence_not_in_source"):
        validate_result(result, SOURCE)


def test_rejects_missing_schema_and_non_korean_output():
    result = valid_result()
    bad = copy.deepcopy(result)
    del bad["points"]
    with pytest.raises(ValueError, match="invalid_summary_schema"):
        validate_result(bad, SOURCE)
    result["summary"]["text_ko"] = "Meta revenue grew 26%."
    with pytest.raises(ValueError, match="not_korean"):
        validate_result(result, SOURCE)


def test_local_model_cannot_be_redirected_to_remote_host():
    with pytest.raises(ValueError, match="ollama_must_be_local"):
        OllamaPreview(base_url="https://external.example")


def test_numeric_placeholders_roundtrip_currency_ranges_and_negative_change():
    source = "Revenue $73 billion to $76 billion; change -5.2%, EPS NT$29.43."
    protected, values = protect_numbers(source)
    assert "$73" not in protected and "-5.2" not in protected
    assert restore_numbers(protected, values) == source
    assert restore_numbers({"text": [protected]}, values) == {"text": [source]}


def test_unknown_placeholder_fails_closed():
    with pytest.raises(ValueError, match="unknown_numeric_placeholder"):
        restore_numbers("__VALUE_UNKNOWN__", {})
