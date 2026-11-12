from datetime import date

import pandas as pd
import streamlit as st
from dateutil.relativedelta import relativedelta

from app import ui
from leasedesk import db, portfolio
from leasedesk import distributions as dist

today = date.today()
st.title("Partner distributions")
st.write("Split each distribution among a partnership's partners: by ownership, or through a waterfall of "
         "preferred return, return of capital and promote. History is replayed in date order, so unpaid "
         "preferred return and unreturned capital carry forward.")

STRUCTURES = {dist.STRUCTURE_WATERFALL: "Waterfall (pref, capital, promote)", dist.STRUCTURE_PRO_RATA: "Pro rata"}
TIER_COLUMNS = {t: st.column_config.NumberColumn(format="dollar") for t in dist.TIERS}
MONEY = {c: st.column_config.NumberColumn(format="dollar") for c in (
    "Distribution", "Total", "Capital contributed", "Total received", "Unreturned capital",
    "Unpaid preferred return", "capital")} | TIER_COLUMNS

with db.connect() as conn:
    partnerships = dist.list_partnerships(conn)


def partnership_form(p: dist.Partnership | None, key: str) -> None:
    """Create or edit a partnership's terms and partners."""
    with st.form(f"terms-{key}"):
        c1, c2 = st.columns(2)
        name = c1.text_input("Partnership name", p.name if p else "")
        prop = c2.text_input("Property", (p.property_name or "") if p else "")
        c3, c4, c5, c6 = st.columns(4)
        structure = c3.selectbox("Structure", list(STRUCTURES), format_func=STRUCTURES.get,
                                 index=list(STRUCTURES).index(p.structure) if p else 0)
        start = c4.date_input("Capital contributed on", p.start_date if p else today, format="MM/DD/YYYY")
        pref = c5.number_input("Preferred return (%/yr)", 0.0, 50.0, p.pref_rate_pct if p else 8.0, 0.25)
        promote = c6.number_input("Promote to sponsor (%)", 0.0, 100.0, p.promote_pct if p else 20.0, 1.0)
        st.caption("Partners. Ownership % is used for pro rata splits; capital for the waterfall. "
                   "Tick the sponsor (GP) who earns the promote.")
        partners = pd.DataFrame([{"Partner": x.name, "Capital": x.capital, "Ownership %": x.ownership_pct,
                                  "Sponsor": x.is_sponsor} for x in (p.partners if p else [])]
                                or [{"Partner": "", "Capital": 0.0, "Ownership %": 0.0, "Sponsor": False}])
        edited = st.data_editor(partners, num_rows="dynamic", hide_index=True, width="stretch", key=f"pt-{key}",
                                column_config={"Capital": st.column_config.NumberColumn(format="dollar")})
        if st.form_submit_button("Save", type="primary"):
            new = dist.Partnership(
                name.strip(), structure, start,
                [dist.Partner(str(r["Partner"]).strip(), float(r["Capital"] or 0), float(r["Ownership %"] or 0),
                              bool(r["Sponsor"])) for _, r in edited.iterrows() if str(r["Partner"] or "").strip()],
                pref if structure == dist.STRUCTURE_WATERFALL else 0.0,
                promote if structure == dist.STRUCTURE_WATERFALL else 0.0, prop.strip() or None, p.id if p else None)
            if not new.name:
                st.error("Give the partnership a name.")
            elif problems := new.problems():
                st.error(" ".join(problems))
            else:
                with db.connect() as conn:
                    dist.save_partnership(conn, new)
                st.session_state["partnership"] = new.name
                st.rerun()


if not partnerships:
    st.info("No partnerships yet. Add one to start recording distributions.")
    partnership_form(None, "new")
    st.stop()

names = [p.name for p in partnerships]
current = st.session_state.get("partnership")
c1, c2 = st.columns([4, 1], vertical_alignment="bottom")
chosen = c1.selectbox("Partnership", names, index=names.index(current) if current in names else 0)
st.session_state["partnership"] = chosen
p = next(x for x in partnerships if x.name == chosen)
with st.expander("New partnership"):
    partnership_form(None, "new")

with db.connect() as conn:
    history = dist.list_distributions(conn, p.id)
result = dist.allocate(p, history, as_of=today)
terms = STRUCTURES[p.structure] + (f" · {p.pref_rate_pct:g}% preferred return · {p.promote_pct:g}% promote"
                                   if p.structure == dist.STRUCTURE_WATERFALL else "")
st.caption(f"{p.property_name or 'No property linked'} · {terms} · capital contributed {p.start_date:%b %-d, %Y}")
for problem in p.problems():
    st.warning(problem)

m = st.columns(4)
m[0].metric("Capital contributed", ui.money_short(p.total_capital))
m[1].metric("Distributed", ui.money_short(sum(d.amount for d in history)), help=f"{len(history)} distributions")
if p.structure == dist.STRUCTURE_WATERFALL:
    m[2].metric("Unreturned capital", ui.money_short(sum(b.unreturned_capital for b in result.balances.values())))
    m[3].metric("Unpaid preferred return", ui.money_short(sum(b.unpaid_pref for b in result.balances.values())),
                help=f"Accrued to {today:%b %-d, %Y}")

tab_new, tab_history, tab_statement, tab_terms = st.tabs(
    ["Plan a distribution", "History", "Partner statement", "Terms and partners"])

with tab_new:
    c1, c2 = st.columns(2)
    paid_on = c1.date_input("Distribution date", today, format="MM/DD/YYYY")
    amount = c2.number_input("Amount to distribute", min_value=0.0, value=0.0, step=1000.0, format="%.2f")
    if p.property_name:
        with db.connect() as conn:
            groups = portfolio.tenant_groups(db.list_documents(conn, db.STATUS_APPROVED))
        quarter_start = (paid_on.replace(day=1) - relativedelta(months=3))
        rent = dist.scheduled_rent(groups, p.property_name, quarter_start,
                                   paid_on.replace(day=1) - relativedelta(days=1))
        if rent:
            st.caption(f"For reference: the leases at {p.property_name} call for {ui.money(rent)} of base rent "
                       f"over the three months before this date (gross, before expenses, debt service and reserves).")
    if amount > 0:
        preview = dist.allocate(p, [*history, dist.Distribution(paid_on, amount)], as_of=paid_on)
        planned = preview.frame()
        planned = planned[planned["Date"].eq(paid_on) & planned["Distribution"].eq(amount)].tail(len(p.partners))
        st.dataframe(planned.drop(columns=["Date", "Distribution"]), hide_index=True, width="stretch",
                     column_config=MONEY)
        note = st.text_input("Note (optional)", placeholder="e.g. Q3 operating cash flow")
        if st.button("Record this distribution", type="primary", icon=":material/payments:"):
            with db.connect() as conn:
                dist.record_distribution(conn, p.id, dist.Distribution(paid_on, amount, note or None))
            st.toast("Distribution recorded.", icon=":material/check:")
            st.rerun()

with tab_history:
    frame = result.frame()
    if frame.empty:
        st.write("No distributions yet.")
    else:
        notes = {d.paid_on: d.note for d in history}
        frame.insert(1, "Note", frame["Date"].map(notes))
        st.dataframe(frame.sort_values(["Date", "Partner"], ascending=[False, True]), hide_index=True,
                     width="stretch", column_config=MONEY | {"Date": st.column_config.DateColumn(format="MM/DD/YYYY")})
        last = history[-1]
        if st.button(f"Delete the latest distribution ({last.paid_on:%b %-d, %Y}, {ui.money(last.amount)})",
                     icon=":material/delete:"):
            with db.connect() as conn:
                dist.delete_distribution(conn, last.id)
            st.rerun()

with tab_statement:
    statement = result.statement()
    st.dataframe(statement, hide_index=True, width="stretch", column_config=MONEY)
    st.download_button("Download statement (Excel)",
                       portfolio.to_excel({"Partner statement": statement, "Distributions": result.frame()}),
                       file_name=f"{p.name} distributions {today:%Y-%m-%d}.xlsx", icon=":material/download:")

with tab_terms:
    partnership_form(p, str(p.id))
