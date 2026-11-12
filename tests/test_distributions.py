"""Partner distributions. Every expected number here is worked out by hand in the comments."""

from datetime import date

import pytest

from leasedesk import distributions as dist
from leasedesk.distributions import Distribution, Partner, Partnership


def waterfall() -> Partnership:
    return Partnership("Elm JV", dist.STRUCTURE_WATERFALL, date(2025, 1, 1),
                       [Partner("Fund LP", capital=900_000), Partner("Sponsor GP", capital=100_000, is_sponsor=True)],
                       pref_rate_pct=8.0, promote_pct=20.0)


def test_pro_rata_split_and_rounding():
    p = Partnership("Trio", dist.STRUCTURE_PRO_RATA, date(2025, 1, 1),
                    [Partner("A", ownership_pct=100 / 3), Partner("B", ownership_pct=100 / 3),
                     Partner("C", ownership_pct=100 / 3)])
    r = dist.allocate(p, [Distribution(date(2025, 3, 31), 100.00)])
    amounts = sorted(a.amount for a in r.allocations)
    assert amounts == [33.33, 33.33, 33.34] and sum(amounts) == 100.00  # no cent lost


def test_waterfall_pref_then_capital_then_promote():
    p = waterfall()
    r = dist.allocate(p, [Distribution(date(2026, 1, 1), 50_000), Distribution(date(2027, 1, 1), 1_200_000)])
    lp, gp = r.balances["Fund LP"], r.balances["Sponsor GP"]
    # Year 1: 8% on $1M accrues $80,000 (LP 72,000 / GP 8,000). Only $50,000 is paid, pro rata: 45,000 / 5,000.
    first = [a for a in r.allocations if a.distribution.amount == 50_000]
    assert {(a.partner, a.tier, a.amount) for a in first} == {
        ("Fund LP", dist.TIER_PREF, 45_000), ("Sponsor GP", dist.TIER_PREF, 5_000)}
    # Year 2: unpaid pref 27,000 + 72,000 = 99,000 (LP) and 3,000 + 8,000 = 11,000 (GP), then $1M of capital,
    # leaving 90,000: a 20% promote (18,000) to the sponsor and 72,000 by capital (64,800 / 7,200).
    assert lp.by_tier == {dist.TIER_PREF: 144_000, dist.TIER_CAPITAL: 900_000, dist.TIER_RESIDUAL: 64_800}
    assert gp.by_tier == {dist.TIER_PREF: 16_000, dist.TIER_CAPITAL: 100_000, dist.TIER_PROMOTE: 18_000,
                          dist.TIER_RESIDUAL: 7_200}
    assert round(lp.received + gp.received, 2) == 1_250_000
    assert (lp.unreturned_capital, gp.unreturned_capital) == (0, 0)
    assert round(lp.unpaid_pref, 2) == 0


def test_unpaid_pref_keeps_accruing_to_the_as_of_date():
    p = waterfall()
    r = dist.allocate(p, [Distribution(date(2025, 7, 2), 10_000)], as_of=date(2026, 1, 1))
    # 182 days to the distribution: 1,000,000 x 8% x 182/365 = 39,890.41 accrued; 10,000 paid.
    # Then 183 more days on the same capital: 40,109.59. Unpaid: 29,890.41 + 40,109.59 = 70,000.00.
    total_unpaid = sum(b.unpaid_pref for b in r.balances.values())
    assert total_unpaid == pytest.approx(70_000.00, abs=0.02)


def test_tables_have_one_row_per_partner_per_distribution():
    r = dist.allocate(waterfall(), [Distribution(date(2026, 1, 1), 50_000), Distribution(date(2026, 1, 1), 50_000)])
    frame = r.frame()
    assert len(frame) == 4 and frame["Total"].sum() == 100_000
    statement = r.statement().set_index("Partner")
    assert statement.loc["Fund LP", "Capital contributed"] == 900_000


def test_problems_with_the_terms():
    p = Partnership("Bad", dist.STRUCTURE_PRO_RATA, date(2025, 1, 1),
                    [Partner("A", ownership_pct=60), Partner("B", ownership_pct=30)])
    assert p.problems() == ["Ownership adds up to 90%, not 100%."]
    w = waterfall()
    w.partners[1].is_sponsor = False
    assert w.problems() == ["A promote needs a sponsor (GP) partner."]


def test_partnerships_and_distributions_are_stored(conn):
    p = waterfall()
    pid = dist.save_partnership(conn, p)
    dist.record_distribution(conn, pid, Distribution(date(2026, 1, 1), 50_000, "Q4 2025"))
    loaded = dist.list_partnerships(conn)[0]
    assert (loaded.name, loaded.pref_rate_pct, [x.name for x in loaded.partners]) == (
        "Elm JV", 8.0, ["Fund LP", "Sponsor GP"])
    assert loaded.partners[1].is_sponsor
    history = dist.list_distributions(conn, pid)
    assert [(d.paid_on, d.amount, d.note) for d in history] == [(date(2026, 1, 1), 50_000, "Q4 2025")]
    dist.delete_distribution(conn, history[0].id)
    assert dist.list_distributions(conn, pid) == []
