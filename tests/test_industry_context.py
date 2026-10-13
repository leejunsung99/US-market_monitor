def test_roundup_ticker_not_mislabeled_as_related_to_extracted_event(classifier, event_factory):
    event = classifier.classify(
        event_factory(
            "Micron, Salesforce and more stocks in today's market",
            summary="Micron stock falls after Chinese rival CXMT reports progress on a denser DRAM chip design.",
        )
    )
    assert event.related_tickers == ("MU",)
    assert event.impact_tickers == ("MU",)


def test_planned_production_not_mass_production_complete(classifier, event_factory):
    event = classifier.classify(
        event_factory("CXMT confirms plans to begin mass production of DDR5 memory")
    )
    assert event.event_stage == "announced_plan"


def test_unattributed_plan_without_to_is_a_review_candidate(classifier, event_factory):
    event = event_factory(
        "Why Micron stock dropped", summary="CXMT plans a new DRAM product for 2026."
    )
    assert classifier.classify(event) is None
    assert event.metadata["classification_decision"]["status"] == "review"
