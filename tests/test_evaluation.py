from datetime import date

from leasedesk import demo_data, evaluation


def demo_abstract():
    return demo_data.build_demo_portfolio(date(2026, 11, 10))[5].abstract  # Golden Dragon: has % rent


def test_identical_abstract_scores_perfectly():
    a = demo_abstract()
    assert evaluation.accuracy(evaluation.compare(a, a.model_copy(deep=True))) == 1.0


def test_detects_wrong_values_and_tolerates_formatting():
    expected = demo_abstract()
    actual = expected.model_copy(deep=True)
    actual.tenant_name = expected.tenant_name.upper().replace(",", "")  # formatting only: still correct
    actual.square_feet = expected.square_feet + 100                     # wrong
    actual.base_rent_schedule[2].monthly_rent += 50                     # wrong
    actual.options = []                                                  # missed

    wrong = {r.field for r in evaluation.compare(expected, actual) if not r.correct}
    assert "tenant_name" not in wrong
    assert "square_feet" in wrong
    assert f"base_rent_schedule[{expected.base_rent_schedule[2].start_date}]" in wrong
    assert "options.count" in wrong


def test_names_and_suites_match_without_entity_descriptions_or_prefixes():
    from leasedesk.models import LeaseAbstract

    expected = LeaseAbstract(tenant_name="Kettle & Crumb Bakery, Inc.", suite="104", guarantor="Margaret Ellison")
    actual = LeaseAbstract(tenant_name="Kettle & Crumb Bakery, Inc., a Colorado corporation", suite="Suite 104",
                           guarantor="Margaret Ellison, an individual")
    results = {r.field: r.correct for r in evaluation.compare(expected, actual)}
    assert results["tenant_name"] and results["suite"] and results["guarantor"]
    wrong = LeaseAbstract(tenant_name="Kettle and Crumb LLC", suite="105", guarantor="Mark Ellison")
    results = {r.field: r.correct for r in evaluation.compare(expected, wrong)}
    assert not (results["tenant_name"] or results["suite"] or results["guarantor"])
