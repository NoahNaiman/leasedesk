from datetime import date

from leasedesk import demo_data, wire_format


def _unions(node) -> int:
    if isinstance(node, dict):
        return ("anyOf" in node) + sum(_unions(v) for v in node.values())
    if isinstance(node, list):
        return sum(_unions(v) for v in node)
    return 0


def test_wire_schema_stays_within_api_limits():
    # The API rejects schemas with many optional unions, or whose grammar grows too large.
    # Only the optional percentage_rent object should remain a union.
    assert _unions(wire_format.WIRE_SCHEMA) <= 1


def test_every_object_requires_all_its_keys():
    defs = wire_format.WIRE_SCHEMA["$defs"].values()
    for obj in [wire_format.WIRE_SCHEMA, *defs]:
        if obj.get("type") == "object":
            assert set(obj["required"]) == set(obj["properties"])


def test_demo_abstracts_round_trip():
    for tenant in demo_data.build_demo_portfolio(date(2026, 11, 10)):
        assert wire_format.from_wire(wire_format.to_wire_json(tenant.abstract)) == tenant.abstract


def test_reply_values_are_cleaned_up():
    reply = wire_format.to_wire_json(demo_data.build_demo_portfolio(date(2026, 11, 10))[0].abstract)
    reply["square_feet"] = "2,450 SF"   # not a clean number
    reply["security_deposit"] = "$8,500.00"
    reply["guarantor"] = "N/A"
    reply["recoveries"]["pays_cam"] = "no"
    reply["recoveries"]["base_year"] = ""
    a = wire_format.from_wire(reply)
    assert a.square_feet is None          # unreadable: left blank for review
    assert a.security_deposit == 8500.0
    assert a.guarantor is None
    assert a.recoveries.pays_cam is False
    assert a.recoveries.base_year is None
