def test_speculation_in_event_sentence_cannot_pass_via_summary(classifier, event_factory):
    event = event_factory(
        "Amazon expands its AI ambitions",
        summary="Amazon is in negotiations to acquire Decart AI in a deal that could be valued at $7 billion.",
    )
    assert classifier.classify(event) is None


def test_confirmed_event_not_negated_by_forward_looking_summary(classifier, event_factory):
    event = event_factory(
        "NVIDIA reports quarterly results",
        summary="Revenue was $10 billion. The company may see higher demand next year.",
    )
    assert classifier.classify(event).event_type == "earnings"


def test_stock_movers_roundup_not_a_confirmed_acquisition(classifier, event_factory):
    event = event_factory(
        "T-Mobile Rises on Upgrade; IonQ to Buy Elisity | Stock Movers",
        summary="Amazon is in negotiations to acquire Decart AI for around $7 billion.",
    )
    assert classifier.classify(event) is None
