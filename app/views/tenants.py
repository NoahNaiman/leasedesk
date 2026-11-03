from datetime import date

import pandas as pd
import streamlit as st

from app import lease_summary, ui
from leasedesk import db, portfolio

today = date.today()


def ordinal(n: int) -> str:
    return {2: "second", 3: "third", 4: "fourth", 5: "fifth"}.get(n, f"#{n}")

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
    column_order=["Property", "Tenant", "Suite", "SF", "Monthly rent", "Expiration", "Months left", "Status",
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
h1.caption(f"{group.terms.tenant_name or ''} · {group.property}")
if "tenant_key" in st.session_state and h2.button("Close", icon=":material/close:", width="stretch"):
    st.session_state.pop("tenant_key")
    st.rerun()

merged = group.merged
with st.container(border=True):
    st.markdown(f"**Documents on file ({len(group.records)})**")
    ordered = merged.applied + sorted(merged.superseded, key=lambda r: r.abstract.lease_date or date.min)
    for step, r in enumerate(ordered, 1):
        a = r.abstract
        superseded = r in merged.superseded
        d1, d2, d3 = st.columns([2, 5, 1], vertical_alignment="center")
        d1.markdown(f"{a.document_type.value.title()}  \n:gray[{ui.fmt_date(a.lease_date)}]")
        d2.markdown(f"~~{r.file_name}~~  \n:gray[Superseded by a newer lease; not used]" if superseded
                    else f"{r.file_name}  \n:gray[{'Base document' if step == 1 else f'Applied {ordinal(step)}'}]")
        if d3.button("Edit", key=f"doc-edit-{r.id}", icon=":material/edit:", width="stretch"):
            ui.open_in_review(r.id)
    if len(group.records) > 1:
        st.caption("The current terms below combine these documents in date order: each later document "
                   "replaces only what it states. Values a later document changed are marked with their source.")

with st.expander("Split or regroup these documents"):
    st.write("If one folder holds documents for more than one tenant (two billboards, for example), "
             "type the tenant each document belongs to. Using the same name for documents groups them; "
             "the name of another tenant at this property moves a document there. Leave it blank to "
             "use the folder's tenant. Your files are not changed.")
    frame = pd.DataFrame([{"id": r.id, "File": r.file_name, "Tenant": r.tenant_override or ""}
                          for r in ordered])
    edited = st.data_editor(frame, hide_index=True, width="stretch", key=f"regroup-{group.key}",
                            disabled=["File"], column_order=["File", "Tenant"],
                            column_config={"Tenant": st.column_config.TextColumn(
                                help="Blank = the tenant of the folder the file is in")})
    changes = edited[edited["Tenant"].fillna("") != frame["Tenant"]]
    if not changes.empty and st.button("Apply", type="primary", key=f"regroup-apply-{group.key}"):
        with db.connect() as conn:
            for _, row in changes.iterrows():
                db.set_tenant_override(conn, int(row["id"]), row["Tenant"])
        st.session_state.pop("tenant_key", None)
        st.toast("Documents regrouped.", icon=":material/check:")
        st.rerun()

st.markdown("#### Current terms")
lease_summary.render_terms(group.terms, today, merged)
