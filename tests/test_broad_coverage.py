import pytest


@pytest.mark.parametrize(
    "title,kind",
    [
        ("Nebius confirms data breach affecting customers", "cybersecurity_incident"),
        ("Fitch downgrades Acme credit rating", "financial_distress"),
        ("Acme bond yields surge to record high", "financial_distress"),
        ("NHTSA opens investigation into Rivian vehicles", "legal_regulatory"),
        ("Rivian recalls 60,000 vehicles over safety defect", "legal_regulatory"),
        ("Acme signs $500 million contract for data center", "contract_investment"),
        ("Microsoft acquires startup for $500 million", "acquisition"),
        ("Acme launches new cloud platform", "core_product"),
        ("Acme announces strategic partnership with Beta", "business_development"),
        ("Acme opens data center in Texas", "business_development"),
    ],
)
def test_broad_coverage_includes_unlisted_companies(classifier, event_factory, title, kind):
    event = classifier.classify(event_factory(title))
    assert event is not None
    assert event.event_type == kind
    assert event.priority == "HIGH"
    if "Microsoft" not in title:
        assert event.related_tickers == ()
        assert event.entity_key.startswith("market:")


@pytest.mark.parametrize(
    "title",
    [
        "Acme could confirm data breach",
        "Acme reportedly signs $500 million contract for data center",
        "Acme explains why cloud security matters",
        "Acme signs $249 million contract for data center",
        "Acme reports quarterly results",
        "Acme sponsored: launches new cloud platform",
        "Why Acme shares rise after data breach",
    ],
)
def test_broad_coverage_keeps_basic_exclusions(classifier, event_factory, title):
    assert classifier.classify(event_factory(title)) is None


def test_unlisted_coverage_can_be_switched_off(classifier, event_factory):
    classifier.filters["untracked_event_types"] = []
    assert classifier.classify(event_factory("Nebius confirms data breach")) is None


def test_unlisted_companies_do_not_share_a_dedup_entity(classifier, event_factory, repo, filters):
    first = classifier.classify(event_factory("Acme confirms data breach"))
    second = classifier.classify(
        event_factory(
            "Beta confirms data breach", source_event_id="200", source_url="https://example.com/200"
        )
    )
    assert first.entity_key != second.entity_key
    assert repo.ingest(first, filters)
    assert repo.ingest(second, filters)


def test_trial_thresholds(filters):
    assert filters["high_threshold"] == 2
    assert filters["critical_threshold"] == 7
    assert filters["minimum_deal_usd"] == 250_000_000
