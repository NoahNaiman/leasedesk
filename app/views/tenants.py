from datetime import date

import streamlit as st

from app import lease_summary, ui
from leasedesk import db, portfolio

today = date.today()
approved = ui.records(db.STATUS_APPROVED)

st.title("Tenants")
if not approved:
    st.write("No approved leases yet. Tenants appear here once their leases are approved.")
    st.page_link("views/review.py", label="Review abstracts", icon=":material/fact_check:")
    st.stop()

groups = portfolio.tenant_groups(approved)
table = portfolio.tenant_table(groups, today)

c1, c2 = st.columns([3, 2])
query = c1.text_input("Search", placeholder="Tenant, legal name, suite, or guarantor",
                      label_visibility="collapsed")
chosen_props = c2.multiselect("Property", list(dict.fromkeys(table["Property"])),
                              placeholder="All properties", label_visibility="collapsed")
view = portfolio.search(table, query)
if chosen_props:
    view = view[view["Property"].isin(chosen_props)]

event = st.dataframe(
    view, hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row",
    column_order=["Property", "Tenant", "Suite", "SF", "Monthly rent", "Expiration", "Months left",
                  "Guarantor", "Documents"],
    column_config={"SF": st.column_config.NumberColumn(format="%,d"),
                   "Monthly rent": st.column_config.NumberColumn(format="dollar"),
                   "Expiration": st.column_config.DateColumn(format="MMM D, YYYY")},
)
st.caption(f"{len(view)} of {len(table)} tenants, sorted by property. Click a tenant to see its documents and terms.")

# A tenant opened from the Properties page stays open until another row is picked or it's closed.
if event.selection.rows:
    st.session_state.pop("tenant_key", None)
    key = view.iloc[event.selection.rows[0]]["key"]
else:
    key = st.session_state.get("tenant_key")
group = next((g for g in groups if g.key == key), None)
if group is None:
    st.stop()

st.divider()
h1, h2 = st.columns([5, 1])
h1.subheader(group.name)
h1.caption(f"{group.primary.abstract.tenant_name or ''} · {group.property}")
if "tenant_key" in st.session_state and h2.button("Close", icon=":material/close:", width="stretch"):
    st.session_state.pop("tenant_key")
    st.rerun()

with st.container(border=True):
    st.markdown(f"**Documents on file ({len(group.records)})**")
    ordered = sorted(group.records, key=lambda r: r.abstract.lease_date or date.min)
    for r in ordered:
        a = r.abstract
        d1, d2, d3 = st.columns([2, 5, 1], vertical_alignment="center")
        d1.markdown(f"{a.document_type.value.title()}  \n:gray[{ui.fmt_date(a.lease_date)}]")
        d2.write(r.file_name)
        if d3.button("Edit", key=f"doc-edit-{r.id}", icon=":material/edit:", width="stretch"):
            ui.open_in_review(r.id)
    if len(group.records) > 1:
        st.caption(f"The tenant list shows the newest value stated in any of these documents, such as an "
                   f"amendment's new expiration date. The full terms below come from the "
                   f"{group.primary.abstract.document_type.value} dated "
                   f"{ui.fmt_date(group.primary.abstract.lease_date)}; amendments are not yet merged into them, "
                   "so check each amendment's abstract for what it changed.")

lease_summary.render(group.primary, today)
