from datetime import timedelta

import pytest

from us_market_monitor.processors.earnings import earnings_facts


def test_actual_unh_video_is_excluded(classifier, event_factory):
    event = event_factory(
        "UNH beats Q3 expectations, raises guidance as AI helps cut costs",
        summary="Hosts discuss UnitedHealth reporting third quarter earnings results and raised its full-year profit outlook.",
        source_url="https://finance.yahoo.com/video/unh-beats-q3-expectations-raises-guidance.html",
    )
    assert classifier.classify(event) is None
    assert event.metadata["classification_decision"]["reason"] == "commentary_or_recap"


def test_numbers_do_not_make_commentary_a_release(classifier, event_factory):
    event = event_factory(
        "NVIDIA reports quarterly results", summary="The hosts discuss revenue of $10 billion."
    )
    assert classifier.classify(event) is None


@pytest.mark.parametrize(
    "summary",
    [
        "The company beat expectations and raised guidance.",
        "Shares gained 5% and market capitalization was $10 billion.",
    ],
)
def test_nonnumeric_earnings_go_to_review(classifier, event_factory, summary):
    event = event_factory("NVIDIA reports quarterly results", summary=summary)
    assert classifier.classify(event) is None
    assert (
        event.metadata["classification_decision"]["reason"] == "earnings_missing_reported_figures"
    )


def test_official_release_without_numbers_still_passes(classifier, event_factory):
    event = event_factory(
        "NVIDIA reports quarterly results", summary="Results released.", metadata={"official": True}
    )
    assert classifier.classify(event).event_type == "earnings"


def test_guidance_withdrawal_is_material_without_a_new_number(
    repo, classifier, filters, event_factory
):
    first = classifier.classify(event_factory("NVIDIA reports quarterly results"))
    assert repo.ingest(first, filters)
    event = event_factory(
        "NVIDIA withdraws revenue outlook",
        summary="NVIDIA withdraws revenue outlook.",
        source_event_id="200",
        source_url="https://example.com/200",
        published_at=first.published_at + timedelta(minutes=5),
    )
    assert repo.ingest(classifier.classify(event), filters)


def test_official_source_first_within_same_priority(repo, classifier, filters, event_factory):
    news = classifier.classify(event_factory("NVIDIA reports quarterly results"))
    news_id = repo.ingest(news, filters)
    release = classifier.classify(
        event_factory(
            "Microsoft reports quarterly results",
            metadata={"official": True},
            source_event_id="200",
            source_url="https://example.com/200",
            published_at=news.published_at + timedelta(seconds=1),
        )
    )
    release_id = repo.ingest(release, filters)
    assert [row["id"] for row in repo.pending()] == [release_id, news_id]


def test_cross_type_release_then_guidance_recap_suppressed(
    repo, classifier, filters, event_factory
):
    first = classifier.classify(
        event_factory(
            "NVIDIA reports Q3 2026 quarterly results",
            summary="Revenue was $10 billion. Revenue outlook was $15 billion.",
        )
    )
    assert repo.ingest(first, filters)
    second = classifier.classify(
        event_factory(
            "NVIDIA raises Q3 2026 revenue outlook to $15 billion",
            summary="Revenue was $10 billion.",
            source_event_id="200",
            source_url="https://example.com/200",
            published_at=first.published_at + timedelta(minutes=5),
        )
    )
    assert second.event_type == "earnings_guidance"
    assert repo.ingest(second, filters) is None


def test_new_guidance_number_is_a_real_update(repo, classifier, filters, event_factory):
    first = classifier.classify(
        event_factory(
            "NVIDIA reports quarterly results",
            summary="Revenue was $10 billion. Revenue outlook was $15 billion.",
        )
    )
    assert repo.ingest(first, filters)
    second = classifier.classify(
        event_factory(
            "NVIDIA revises revenue outlook to $16 billion",
            summary="Revenue was $10 billion.",
            source_event_id="200",
            source_url="https://example.com/200",
            published_at=first.published_at + timedelta(minutes=5),
        )
    )
    assert repo.ingest(second, filters)


def test_normalized_units_do_not_create_updates(event_factory):
    first = event_factory("NVIDIA reports quarterly results", summary="Revenue was $10 billion.")
    second = event_factory(
        "NVIDIA reports quarterly results", summary="Revenue was $10,000 million."
    )
    assert earnings_facts(first) == earnings_facts(second)


def test_shorter_recap_does_not_count_as_changed_figures(event_factory):
    from us_market_monitor.processors.earnings import earnings_update

    first = event_factory(
        "NVIDIA reports quarterly results", summary="Revenue outlook is $10 billion to $12 billion."
    )
    recap = event_factory(
        "NVIDIA reports quarterly results", summary="Revenue outlook is $12 billion."
    )
    assert not earnings_update(earnings_facts(recap), earnings_facts(first))


def test_unrelated_market_cap_is_not_a_reported_financial_result(event_factory):
    event = event_factory(
        "NVIDIA reports quarterly results",
        summary="Revenue grew and market capitalization was $10 billion.",
    )
    assert earnings_facts(event)["metrics"] == {}


def test_different_quarters_are_separate(repo, classifier, filters, event_factory):
    first = classifier.classify(event_factory("NVIDIA reports Q3 2026 quarterly results"))
    assert repo.ingest(first, filters)
    second = classifier.classify(
        event_factory(
            "NVIDIA reports Q4 2026 quarterly results",
            source_event_id="200",
            source_url="https://example.com/200",
        )
    )
    assert repo.ingest(second, filters)
