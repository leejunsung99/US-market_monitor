from us_market_monitor.notifications.preview_layout import (
    build_card,
    candidate_sentences,
    economic_points,
    render_card,
    safe_translation,
    source_family,
)


def test_sec_results_metadata_overrides_product_classification(event_factory):
    event = event_factory(
        "TSLA 8-K 공시", source="SEC EDGAR", related_tickers=("TSLA",),
        event_type="core_product", metadata={"items": ["2.02"], "official": True},
    )
    card = build_card(event, 1, {})
    assert card.family == "실적 발표"
    assert "테슬라(TSLA)" in card.title


def test_ad_and_flattened_table_are_not_displayed(event_factory):
    event = event_factory(
        article_summary="Revenue 2026 2025 10 20 30 40 50 60.\n"
        "Our AI portfolio is outperforming the market by 118%.\n"
        "Revenue was $10 billion."
    )
    sentences, omitted = candidate_sentences(event)
    assert sentences == ["Revenue was $10 billion."]
    assert omitted == 2


def test_translation_cannot_swap_range_or_lose_minus_sign():
    assert not safe_translation("Revenue $18.5 to $19.5 billion", "매출 $19.5 ~ $18.5 billion")
    assert not safe_translation("Change -5.2%", "변화 5.2%")
    assert safe_translation("Claims 199,000", "청구 199000")


def test_fed_speech_is_not_a_rate_decision(event_factory):
    event = event_factory(source="Federal Reserve", metadata={"feed": "speeches"})
    assert source_family(event) == "연준 연설·증언"


def test_multi_company_article_does_not_claim_single_issuer(event_factory):
    event = event_factory(related_tickers=("NVDA", "MU"))
    card = build_card(event, 1, {})
    assert card.title == card.family
    assert "엔비디아" not in card.title


def test_actual_and_forecast_separate_and_html_escaped(event_factory):
    event = event_factory(
        event_type="earnings", article_summary="Revenue was $10 billion.\n"
        "Management expects revenue of $12 billion.\nCompany announced <new> products."
    )
    card = build_card(event, 1, {})
    assert card.sections["발표 실적"] == ["Revenue was $10 billion."]
    assert card.sections["전망·계획 · 확정 실적과 구분"] == [
        "Management expects revenue of $12 billion."
    ]
    assert "&lt;new&gt;" in render_card(card)


def test_census_retains_annualization_revision_and_error_band(event_factory):
    event = event_factory(
        metadata={"indicator": "census_housing_starts"},
        summary="Privately-owned housing starts in September 2026 were at a seasonally "
        "adjusted annual rate of 1,221,000. This is 5.2 percent (+/- 11.8%)* below the "
        "revised August 2026 estimate of 1,288,000.",
    )
    points = economic_points(event)
    assert "계절조정 연율" in points[0]
    assert "-5.2%" in points[1] and "1,288,000" in points[1]
    assert "±11.8%" in points[2]


def test_date_only_publication_does_not_invent_midnight(event_factory):
    event = event_factory(metadata={"publication_precision": "date", "publication_date": "2026-10-15"})
    assert build_card(event, 1, {}).publication == "2026-10-15 (시각 미제공)"
