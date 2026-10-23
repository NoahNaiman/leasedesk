from datetime import date

import pandas as pd
import streamlit as st

from app import lease_summary, ui
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
properties = list(dict.fromkeys(table["Property"]))  # already in display order
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
    column_order=["Property", "Tenant", "Document", "Suite", "SF", "Monthly rent", "Rent $/SF/yr", "Expiration",
                  "Months left",
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
st.divider()
lease_summary.render(record, today)
