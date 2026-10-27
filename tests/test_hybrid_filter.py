import copy

import pytest

from us_market_monitor.processors.hybrid_filter import route_event, validate_judgment


def test_clear_earnings_passes_without_llm(classifier, event_factory):
    event = event_factory()
    before = copy.deepcopy(event)
    assert route_event(event, classifier).route == "pass"
    assert event == before


def test_review_candidate_routes_to_llm(classifier, event_factory):
    event = event_factory("NVIDIA earnings update", summary="The company beat expectations.")
    assert route_event(event, classifier).route == "llm_review"


def test_advertisement_remains_rejected(classifier, event_factory):
    event = event_factory("Sponsored NVIDIA stock picks to buy", summary="Revenue could grow.")
    assert route_event(event, classifier).route == "reject"


def test_future_earnings_does_not_direct_pass(classifier, event_factory):
    event = event_factory("Tesla Likely To Report Lower Q3 Earnings", summary="Revenue was $10 billion.")
    assert route_event(event, classifier).route == "llm_review"


def test_broader_relevance_can_rescue_old_rejection(classifier, event_factory):
    event = event_factory("NVIDIA capacity update", summary="Capacity expansion and chip demand remain in focus.")
    assert route_event(event, classifier).route == "llm_review"


def test_unrelated_story_does_not_use_llm(classifier, event_factory):
    event = event_factory("Local community holds annual picnic", summary="Families gathered in the park.")
    assert route_event(event, classifier).route == "reject"


@pytest.mark.parametrize("ids", [[], [9], [True]])
def test_alert_requires_real_evidence_ids(ids):
    with pytest.raises(ValueError):
        validate_judgment({"decision": "alert", "reason_ko": "중요한 실적 변화", "evidence_ids": ids}, 2)


def test_uncertain_is_distinct_from_alert():
    result = validate_judgment({"decision": "uncertain", "reason_ko": "본문 근거가 부족합니다", "evidence_ids": []}, 2)
    assert result["decision"] == "uncertain"
