"""Partner distributions: who gets what when a partnership distributes cash.

A partnership has partners (capital contributed and ownership share) and one of two
structures:

- **Pro rata:** every distribution is split by ownership percentage.
- **Waterfall:** each distribution pays, in order,
  1. the **preferred return** that has accrued and not yet been paid (simple annual interest
     on each partner's unreturned capital, accruing day by day),
  2. **return of capital**, pro rata to unreturned capital,
  3. the **residual**: the sponsor (GP) takes the promote percentage, and the rest is shared
     by all partners in proportion to the capital they contributed.

Distributions are replayed in date order, so unreturned capital and unpaid preferred return
carry forward from one distribution to the next. ``allocate`` is a pure function: given the
terms and the history it returns every dollar, with the tier it was paid under.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import pandas as pd

STRUCTURE_PRO_RATA = "pro_rata"
STRUCTURE_WATERFALL = "waterfall"
TIER_PRO_RATA = "Pro rata"
TIER_PREF = "Preferred return"
TIER_CAPITAL = "Return of capital"
TIER_RESIDUAL = "Residual split"
TIER_PROMOTE = "Promote"
TIERS = [TIER_PREF, TIER_CAPITAL, TIER_RESIDUAL, TIER_PROMOTE, TIER_PRO_RATA]

SCHEMA = """
CREATE TABLE IF NOT EXISTS partnerships (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    name              TEXT NOT NULL UNIQUE,
    property_name     TEXT,
    structure         TEXT NOT NULL,
    start_date        TEXT NOT NULL,
    pref_rate_pct     REAL NOT NULL DEFAULT 0,
    promote_pct       REAL NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS partners (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    partnership_id    INTEGER NOT NULL REFERENCES partnerships(id),
    name              TEXT NOT NULL,
    is_sponsor        INTEGER NOT NULL DEFAULT 0,
    capital           REAL NOT NULL DEFAULT 0,
    ownership_pct     REAL NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS distributions (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    partnership_id    INTEGER NOT NULL REFERENCES partnerships(id),
    paid_on           TEXT NOT NULL,
    amount            REAL NOT NULL,
    note              TEXT
);
"""


@dataclass
class Partner:
    name: str
    capital: float = 0.0
    ownership_pct: float = 0.0
    is_sponsor: bool = False
    id: int | None = None


@dataclass
class Partnership:
    name: str
    structure: str
    start_date: date
    partners: list[Partner]
    pref_rate_pct: float = 0.0
    promote_pct: float = 0.0
    property_name: str | None = None
    id: int | None = None

    @property
    def total_capital(self) -> float:
        return sum(p.capital for p in self.partners)

    def problems(self) -> list[str]:
        """Terms that would make the math meaningless."""
        out = []
        if not self.partners:
            out.append("Add at least one partner.")
        if self.structure == STRUCTURE_PRO_RATA and abs(sum(p.ownership_pct for p in self.partners) - 100) > 0.01:
            out.append(f"Ownership adds up to {sum(p.ownership_pct for p in self.partners):g}%, not 100%.")
        if self.structure == STRUCTURE_WATERFALL:
            if self.total_capital <= 0:
                out.append("A waterfall needs contributed capital.")
            if self.promote_pct and not any(p.is_sponsor for p in self.partners):
                out.append("A promote needs a sponsor (GP) partner.")
        return out


@dataclass
class Distribution:
    paid_on: date
    amount: float
    note: str | None = None
    id: int | None = None


@dataclass
class Allocation:
    distribution: Distribution
    partner: str
    tier: str
    amount: float


@dataclass
class PartnerBalance:
    """Where a partner stands after a set of distributions."""
    name: str
    capital: float
    received: float = 0.0
    by_tier: dict[str, float] = field(default_factory=dict)
    unreturned_capital: float = 0.0
    unpaid_pref: float = 0.0


@dataclass
class Result:
    allocations: list[Allocation]
    balances: dict[str, PartnerBalance]
    structure: str = STRUCTURE_WATERFALL

    def frame(self) -> pd.DataFrame:
        """One row per distribution and partner, with each tier as a column."""
        rows: dict[tuple, dict] = {}
        for a in self.allocations:
            key = (id(a.distribution), a.partner)
            row = rows.setdefault(key, {"Date": a.distribution.paid_on, "Distribution": a.distribution.amount,
                                        "Partner": a.partner})
            row[a.tier] = row.get(a.tier, 0.0) + a.amount
        df = pd.DataFrame(rows.values())
        if df.empty:
            return df
        tiers = [t for t in TIERS if t in df.columns]
        df[tiers] = df[tiers].fillna(0.0)
        df["Total"] = df[tiers].sum(axis=1).round(2)
        return df[["Date", "Distribution", "Partner", *tiers, "Total"]]

    def statement(self) -> pd.DataFrame:
        """Each partner's totals: contributed, received by tier, and what is still owed."""
        rows = []
        for b in self.balances.values():
            row = {"Partner": b.name, "Capital contributed": b.capital, "Total received": round(b.received, 2)}
            row.update({t: round(v, 2) for t, v in b.by_tier.items()})
            if self.structure == STRUCTURE_WATERFALL:
                row["Unreturned capital"] = round(b.unreturned_capital, 2)
                row["Unpaid preferred return"] = round(b.unpaid_pref, 2)
            rows.append(row)
        df = pd.DataFrame(rows)
        tiers = [t for t in TIERS if t in df.columns]
        order = ["Partner", "Capital contributed", "Total received", *tiers,
                 *[c for c in ("Unreturned capital", "Unpaid preferred return") if c in df.columns]]
        return df[order].fillna(0.0) if not df.empty else df


def _split(amount: float, weights: dict[str, float]) -> dict[str, float]:
    """Split ``amount`` in proportion to ``weights``, rounding to cents with no cent lost."""
    total = sum(w for w in weights.values() if w > 0)
    if total <= 0 or amount <= 0:
        return {k: 0.0 for k in weights}
    shares = {k: round(amount * max(w, 0) / total, 2) for k, w in weights.items()}
    drift = round(amount - sum(shares.values()), 2)
    if drift:
        largest = max(weights, key=lambda k: weights[k])
        shares[largest] = round(shares[largest] + drift, 2)
    return shares


def allocate(p: Partnership, distributions: list[Distribution], as_of: date | None = None) -> Result:
    """Every dollar of every distribution, by partner and tier, with balances afterwards.

    ``as_of`` (default: the last distribution) is the date unpaid preferred return is accrued to.
    """
    balances = {x.name: PartnerBalance(x.name, x.capital, unreturned_capital=x.capital) for x in p.partners}
    allocations: list[Allocation] = []
    last = p.start_date

    def accrue(to: date) -> None:
        nonlocal last
        days = max(0, (to - last).days)
        for b in balances.values():
            b.unpaid_pref += b.unreturned_capital * p.pref_rate_pct / 100 * days / 365
        last = max(last, to)

    def pay(d: Distribution, shares: dict[str, float], tier: str) -> float:
        paid = 0.0
        for name, amount in shares.items():
            if amount:
                allocations.append(Allocation(d, name, tier, amount))
                b = balances[name]
                b.received += amount
                b.by_tier[tier] = b.by_tier.get(tier, 0.0) + amount
                paid += amount
        return round(paid, 2)

    for d in sorted(distributions, key=lambda d: (d.paid_on, d.id or 0)):
        remaining = round(d.amount, 2)
        if p.structure == STRUCTURE_PRO_RATA:
            pay(d, _split(remaining, {x.name: x.ownership_pct for x in p.partners}), TIER_PRO_RATA)
            continue
        accrue(d.paid_on)
        # 1. Preferred return owed, pro rata to what each partner is owed.
        owed = {n: round(b.unpaid_pref, 2) for n, b in balances.items()}
        pref = _split(min(remaining, sum(owed.values())), owed)
        remaining = round(remaining - pay(d, pref, TIER_PREF), 2)
        for n, amount in pref.items():
            balances[n].unpaid_pref = max(0.0, balances[n].unpaid_pref - amount)
        # 2. Return of capital, pro rata to unreturned capital.
        unreturned = {n: round(b.unreturned_capital, 2) for n, b in balances.items()}
        capital = _split(min(remaining, sum(unreturned.values())), unreturned)
        remaining = round(remaining - pay(d, capital, TIER_CAPITAL), 2)
        for n, amount in capital.items():
            balances[n].unreturned_capital = max(0.0, balances[n].unreturned_capital - amount)
        # 3. Residual: promote to the sponsor, the rest by contributed capital.
        if remaining > 0:
            sponsors = {x.name: x.capital or 1.0 for x in p.partners if x.is_sponsor}
            promote_total = round(remaining * p.promote_pct / 100, 2) if sponsors else 0.0
            pay(d, _split(promote_total, sponsors), TIER_PROMOTE)
            pay(d, _split(round(remaining - promote_total, 2), {x.name: x.capital for x in p.partners}),
                TIER_RESIDUAL)
    if p.structure == STRUCTURE_WATERFALL:
        accrue(as_of or last)
    return Result(allocations, balances, p.structure)


# --------------------------------------------------------------------------- storage

def ensure_schema(conn) -> None:
    conn.executescript(SCHEMA)


def save_partnership(conn, p: Partnership) -> int:
    """Insert or update a partnership and replace its partner list."""
    ensure_schema(conn)
    values = (p.name, p.property_name, p.structure, p.start_date.isoformat(), p.pref_rate_pct, p.promote_pct)
    if p.id is None:
        p.id = conn.execute("""INSERT INTO partnerships (name, property_name, structure, start_date, pref_rate_pct,
                               promote_pct) VALUES (?, ?, ?, ?, ?, ?)""", values).lastrowid
    else:
        conn.execute("""UPDATE partnerships SET name = ?, property_name = ?, structure = ?, start_date = ?,
                        pref_rate_pct = ?, promote_pct = ? WHERE id = ?""", (*values, p.id))
    conn.execute("DELETE FROM partners WHERE partnership_id = ?", (p.id,))
    for x in p.partners:
        conn.execute("""INSERT INTO partners (partnership_id, name, is_sponsor, capital, ownership_pct)
                        VALUES (?, ?, ?, ?, ?)""", (p.id, x.name, int(x.is_sponsor), x.capital, x.ownership_pct))
    return p.id


def list_partnerships(conn) -> list[Partnership]:
    ensure_schema(conn)
    out = []
    for r in conn.execute("SELECT * FROM partnerships ORDER BY name COLLATE NOCASE").fetchall():
        partners = [Partner(x["name"], x["capital"], x["ownership_pct"], bool(x["is_sponsor"]), x["id"])
                    for x in conn.execute("SELECT * FROM partners WHERE partnership_id = ? ORDER BY id", (r["id"],))]
        out.append(Partnership(r["name"], r["structure"], date.fromisoformat(r["start_date"]), partners,
                               r["pref_rate_pct"], r["promote_pct"], r["property_name"], r["id"]))
    return out


def record_distribution(conn, partnership_id: int, d: Distribution) -> int:
    ensure_schema(conn)
    d.id = conn.execute("INSERT INTO distributions (partnership_id, paid_on, amount, note) VALUES (?, ?, ?, ?)",
                        (partnership_id, d.paid_on.isoformat(), d.amount, d.note)).lastrowid
    return d.id


def delete_distribution(conn, distribution_id: int) -> None:
    conn.execute("DELETE FROM distributions WHERE id = ?", (distribution_id,))


def list_distributions(conn, partnership_id: int) -> list[Distribution]:
    ensure_schema(conn)
    rows = conn.execute("SELECT * FROM distributions WHERE partnership_id = ? ORDER BY paid_on, id",
                        (partnership_id,)).fetchall()
    return [Distribution(date.fromisoformat(r["paid_on"]), r["amount"], r["note"], r["id"]) for r in rows]


def scheduled_rent(groups, property_name: str, start: date, end: date) -> float:
    """Base rent the leases at a property call for between two dates (inclusive), by month.

    A starting point for sizing a distribution: gross scheduled rent, before vacancy,
    expenses, debt service and reserves.
    """
    from dateutil.relativedelta import relativedelta

    total, month = 0.0, start.replace(day=1)
    while month <= end:
        for g in groups:
            if g.property == property_name:
                total += g.terms.current_monthly_rent(month) or 0.0
        month += relativedelta(months=1)
    return round(total, 2)
