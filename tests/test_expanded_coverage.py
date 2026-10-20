import pytest

@pytest.mark.parametrize('title,kind', [
 ('Tesla unveils new Roadster sports car','core_product'),
 ('Tesla revealed its Cybercab robotaxi','core_product'),
 ('CoreWeave confirms security breach affecting customer data','cybersecurity_incident'),
 ('Hackers stole CoreWeave customer data','cybersecurity_incident'),
 ('NHTSA opens investigation into Tesla FSD crashes','legal_regulatory'),
 ('Tesla recalls 120,000 vehicles over brake defect','legal_regulatory'),
 ('CoreWeave defaults on bond payment','financial_distress'),
 ("Fitch downgrades CoreWeave credit rating",'financial_distress'),
])
def test_expanded_material_events(classifier,event_factory,title,kind):
    event=classifier.classify(event_factory(title))
    assert event is not None
    assert event.event_type==kind

@pytest.mark.parametrize('title,summary', [
 ('CoreWeave could suffer a security breach',''),
 ('CoreWeave explains why AI security matters',''),
 ('Tesla Roadster weekly recap','Tesla unveils new Roadster'),
 ('Tesla could unveil new Roadster',''),
 ('CoreWeave improves security monitoring',''),
 ('Unknown startup confirms security breach','CoreWeave provides GPU services.'),
 ('Tesla shares rise','Unknown startup confirms security breach.'),
])
def test_expanded_rules_keep_noise_out(classifier,event_factory,title,summary):
    assert classifier.classify(event_factory(title,summary=summary)) is None

def test_coreweave_in_watchlist(watchlist):
    assert 'CoreWeave' in watchlist['companies']['CRWV']['aliases']
