def test_acquisition_discussions_are_not_confirmed_deal(classifier, event_factory):
    event = event_factory(
        "Nvidia in discussions to acquire or expand stake in Reflection AI",
        summary="Nvidia is in discussions about a major investment in Reflection AI.",
    )
    assert classifier.classify(event) is None


def test_question_about_an_old_deal_is_not_a_new_announcement(classifier, event_factory):
    event = event_factory(
        "Is Advanced Micro Devices Inc. (AMD) Right to Bet $8.2 Billion on the Future of Physical AI?",
        summary="On September 28, the company inked a deal to acquire World Labs.",
    )
    assert classifier.classify(event) is None
