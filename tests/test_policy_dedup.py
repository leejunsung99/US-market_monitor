def test_distinct_tariff_jurisdictions_not_suppressed(repo, classifier, filters, event_factory):
    china = classifier.classify(event_factory("US government imposes new tariffs on China"))
    canada = classifier.classify(
        event_factory(
            "US government imposes new tariffs on Canada",
            source_event_id="200",
            source_url="https://example.com/200",
        )
    )
    assert repo.ingest(china, filters)
    assert repo.ingest(canada, filters)


def test_same_country_policy_paraphrase_is_suppressed(repo, classifier, filters, event_factory):
    first = classifier.classify(event_factory("US government imposes new tariffs on China"))
    second = classifier.classify(
        event_factory(
            "President announces tariffs on China",
            source_event_id="200",
            source_url="https://example.com/200",
        )
    )
    assert repo.ingest(first, filters)
    assert repo.ingest(second, filters) is None


def test_acquisition_paraphrase_dedup(repo, classifier, filters, event_factory):
    first = classifier.classify(event_factory("Microsoft agrees to acquire Acme for $2 billion"))
    second = classifier.classify(
        event_factory(
            "Microsoft acquires Acme in $2 billion deal",
            source_event_id="200",
            source_url="https://example.com/200",
        )
    )
    assert repo.ingest(first, filters)
    assert repo.ingest(second, filters) is None
