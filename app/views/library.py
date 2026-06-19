from datetime import date

import pandas as pd
import streamlit as st

from app import ui
from leasedesk import db, portfolio

today = date.today()
approved = ui.records(db.STATUS_APPROVED)

st.title("Lease library")
if not approved:
    st.write("No approved leases yet. Import leases and approve their abstracts to see them here.")
    st.page_link("views/import_leases.py", label="Import leases", icon=":material/upload_file:")
    st.stop()

table = portfolio.lease_table(approved, today)

c1, c2, c3 = st.columns([3, 2, 1])
query = c1.text_input("Search", placeholder="Tenant, suite, guarantor, or file name",
                      label_visibility="collapsed")
properties = sorted(p for p in table["Property"].dropna().unique())
chosen_props = c2.multiselect("Property", properties, placeholder="All properties",
                              label_visibility="collapsed")


def export_workbook() -> bytes:
    rent_rows, option_rows = [], []
    for r in approved:
        a = r.abstract
        for s in a.base_rent_schedule:
            rent_rows.append({"Tenant": a.trade_name or a.tenant_name, "Suite": a.suite, "Start": s.start_date,
                              "End": s.end_date, "Monthly rent": s.monthly_rent, "Annual $/SF": s.annual_rent_psf})
        for o in a.options:
            option_rows.append({"Tenant": a.trade_name or a.tenant_name, "Suite": a.suite,
                                "Type": o.option_type.value.replace("_", " "),
                                "Notice deadline": o.notice_deadline, "Term (months)": o.term_months,
                                "Description": o.description})
    return portfolio.to_excel({"Leases": table.drop(columns=["id"]), "Rent schedules": pd.DataFrame(rent_rows),
                               "Options": pd.DataFrame(option_rows)})


c3.download_button("Excel", export_workbook(), file_name=f"lease_abstracts_{today:%Y-%m-%d}.xlsx",
                   icon=":material/download:", width="stretch")

view = portfolio.search(table, query)
if chosen_props:
    view = view[view["Property"].isin(chosen_props)]

event = st.dataframe(
    view,
    hide_index=True,
    width="stretch",
    on_select="rerun",
    selection_mode="single-row",
    column_order=["Tenant", "Suite", "SF", "Monthly rent", "Rent $/SF/yr", "Expiration", "Months left",
                  "Recovery", "Pro-rata %", "Next option deadline"],
    column_config={
        "SF": st.column_config.NumberColumn(format="%,d"),
        "Monthly rent": st.column_config.NumberColumn(format="dollar"),
        "Rent $/SF/yr": st.column_config.NumberColumn(format="$%.2f"),
        "Expiration": st.column_config.DateColumn(format="MMM D, YYYY"),
        "Next option deadline": st.column_config.DateColumn(format="MMM D, YYYY"),
        "Pro-rata %": st.column_config.NumberColumn(format="%.2f%%"),
    },
)
st.caption(f"{len(view)} of {len(table)} leases. Click a row to see the full abstract.")

if not event.selection.rows:
    st.stop()

doc_id = int(view.iloc[event.selection.rows[0]]["id"])
record = next(r for r in approved if r.id == doc_id)
a = record.abstract

st.divider()
h1, h2, h3 = st.columns([4, 1, 1])
h1.subheader(a.display_name())
h1.caption(f"{a.tenant_name or ''} · {a.property_name or ''} · {record.file_name}")
if h2.button("Edit", icon=":material/edit:", width="stretch"):
    ui.open_in_review(record.id)
with open(record.stored_path, "rb") as fh:
    h3.download_button("PDF", fh.read(), file_name=record.file_name, mime="application/pdf",
                       icon=":material/picture_as_pdf:", width="stretch")

k = st.columns(4)
k[0].metric("Square feet", f"{a.square_feet:,.0f}" if a.square_feet else "—")
k[1].metric("Current monthly rent", ui.money(a.current_monthly_rent(today), cents=True))
k[2].metric("Expires", ui.fmt_date(a.expiration_date))
k[3].metric("Pro-rata share", ui.pct(a.recoveries.pro_rata_share_pct))

left, right = st.columns(2, gap="large")
with left:
    with st.container(border=True):
        st.markdown("**Term**")
        st.markdown(f"Lease date: {ui.fmt_date(a.lease_date)}  \n"
                    f"Commencement: {ui.fmt_date(a.commencement_date)}  \n"
                    f"Rent commencement: {ui.fmt_date(a.rent_commencement_date)}  \n"
                    f"Expiration: {ui.fmt_date(a.expiration_date)}  \n"
                    f"Security deposit: {ui.money(a.security_deposit, cents=True)}  \n"
                    f"Guarantor: {a.guarantor or '—'}")
    with st.container(border=True):
        st.markdown("**Base rent schedule**")
        st.dataframe(
            pd.DataFrame([{"Start": s.start_date, "End": s.end_date, "Monthly": s.monthly_rent,
                           "$/SF/yr": s.annual_rent_psf,
                           "Current": "◀" if (s.start_date or date.min) <= today <= (s.end_date or date.max) else ""}
                          for s in a.base_rent_schedule]),
            hide_index=True, width="stretch",
            column_config={"Start": st.column_config.DateColumn(format="MM/DD/YYYY"),
                           "End": st.column_config.DateColumn(format="MM/DD/YYYY"),
                           "Monthly": st.column_config.NumberColumn(format="dollar"),
                           "$/SF/yr": st.column_config.NumberColumn(format="$%.2f")})
        if a.percentage_rent:
            pr = a.percentage_rent
            st.markdown(f"**Percentage rent:** {ui.pct(pr.rate_pct)} of sales over "
                        f"{ui.money(pr.breakpoint_amount)} ({pr.breakpoint_type or 'breakpoint'})")
with right:
    r = a.recoveries
    with st.container(border=True):
        st.markdown("**Recoveries**")
        passes = [n for n, v in (("CAM", r.pays_cam), ("taxes", r.pays_taxes), ("insurance", r.pays_insurance)) if v]
        st.markdown(f"Structure: {r.structure.value.replace('_', ' ').upper() if r.structure.value == 'nnn' else r.structure.value.replace('_', ' ').title()}  \n"
                    f"Pays: {', '.join(passes) or '—'}  \n"
                    f"Pro-rata share: {ui.pct(r.pro_rata_share_pct)}  \n"
                    f"CAM cap: {ui.pct(r.cam_cap_pct)}"
                    f"{' (' + r.cam_cap_type.value.replace('_', '-') + ')' if r.cam_cap_pct else ''}  \n"
                    f"Base year: {r.base_year or '—'}  \n"
                    f"Admin fee: {ui.pct(r.admin_fee_pct)} · Gross-up: {ui.pct(r.gross_up_pct)}")
        if r.excluded_expenses:
            st.caption("Excluded: " + "; ".join(r.excluded_expenses))
    with st.container(border=True):
        st.markdown("**Options**")
        if a.options:
            for o in a.options:
                st.markdown(f"- **{o.option_type.value.replace('_', ' ').title()}**, notice by "
                            f"{ui.fmt_date(o.notice_deadline)}: {o.description}")
        else:
            st.write("None")
    with st.container(border=True):
        st.markdown("**Use and restrictions**")
        for label, value in (("Permitted use", a.permitted_use), ("Exclusive", a.exclusive_use),
                             ("Co-tenancy", a.co_tenancy), ("Radius", a.radius_restriction),
                             ("Notes", a.notes)):
            if value:
                st.markdown(f"**{label}:** {value}")
