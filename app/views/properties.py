from datetime import date

import streamlit as st

from app import ui
from leasedesk import db, portfolio

today = date.today()
approved = ui.records(db.STATUS_APPROVED)
with db.connect() as conn:
    folder_properties = db.list_properties(conn)
    waiting = db.list_documents(conn, db.STATUS_REVIEW) + db.list_documents(conn, db.STATUS_PENDING)

st.title("Properties")
if not approved and not folder_properties:
    st.write("No properties yet. Point LeaseDesk at your property folders on the Import page, "
             "or approve some lease abstracts.")
    st.page_link("views/import_leases.py", label="Import leases", icon=":material/upload_file:")
    st.stop()

groups = portfolio.tenant_groups(approved)
props = portfolio.property_table(groups, today, [p.display_name for p in folder_properties])
waiting_counts = {}
for r in waiting:
    name = r.property_name or (r.abstract and portfolio.property_label(r.abstract))
    if name:
        waiting_counts[name] = waiting_counts.get(name, 0) + 1
props["Waiting"] = props["Property"].map(lambda p: waiting_counts.get(p, 0))

k = st.columns(4)
k[0].metric("Properties", len(props))
k[1].metric("Tenants", int(props["Tenants"].sum()))
k[2].metric("Leased SF", ui.number(props["Leased SF"].sum()))
k[3].metric("Monthly base rent", ui.money_short(props["Monthly rent"].sum()))

money_cols = {
    "Leased SF": st.column_config.NumberColumn(format="%,d"),
    "Monthly rent": st.column_config.NumberColumn(format="dollar"),
    "Annual rent": st.column_config.NumberColumn(format="dollar"),
    "Avg rent $/SF/yr": st.column_config.NumberColumn(format="$%.2f"),
    "Next expiration": st.column_config.DateColumn(format="MMM D, YYYY"),
    "Expiring within 12 months": st.column_config.NumberColumn("Expiring ≤ 12 mo"),
    "Waiting": st.column_config.NumberColumn(help="Documents not yet abstracted or approved"),
}
event = st.dataframe(props, hide_index=True, width="stretch", on_select="rerun",
                     selection_mode="single-row", column_config=money_cols,
                     column_order=["Property", "Tenants", "Leased SF", "Monthly rent",
                                   "Avg rent $/SF/yr", "Next expiration", "Expiring within 12 months",
                                   "Waiting"])
st.caption("Click a property to see its tenants.")

if not event.selection.rows:
    st.stop()

row = props.iloc[event.selection.rows[0]]
prop = row["Property"]
members = [g for g in groups if g.property == prop]

st.divider()
st.subheader(prop)
folders = [p.folder_name for p in folder_properties if p.display_name == prop]
details = [f"Folder: {', '.join(folders)}"] if folders else []
if not ui.missing(row["Address"]):
    details.append(row["Address"])
if details:
    st.caption(" · ".join(details))

m = st.columns(4)
m[0].metric("Tenants", int(row["Tenants"]))
m[1].metric("Leased SF", ui.number(row["Leased SF"]))
m[2].metric("Annual base rent", ui.money_short(row["Annual rent"]))
m[3].metric("Avg rent $/SF/yr", ui.money(row["Avg rent $/SF/yr"], cents=True))

tenants = portfolio.tenant_table(members, today)
pick = st.dataframe(
    tenants, hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row",
    key=f"tenants-{prop}",
    column_order=["Tenant", "Suite", "SF", "Monthly rent", "Expiration", "Months left", "Status", "Documents"],
    column_config={"SF": st.column_config.NumberColumn(format="%,d"),
                   "Monthly rent": st.column_config.NumberColumn(format="dollar"),
                   "Expiration": st.column_config.DateColumn(format="MMM D, YYYY")},
)
st.caption("Click a tenant to open it on the Tenants page.")
if pick.selection.rows:
    st.session_state["tenant_key"] = tenants.iloc[pick.selection.rows[0]]["key"]
    st.switch_page("views/tenants.py")

with st.expander("Rename or merge this property"):
    st.write("Give the property a new name, or merge it into another property by choosing that property's "
             "name. Your folders are not renamed; only the name shown in LeaseDesk changes.")
    others = [p for p in props["Property"] if p != prop]
    choice = st.selectbox("Rename to, or merge into", ["A new name…", *others], key=f"merge-{prop}")
    new_name = st.text_input("New property name", key=f"new-{prop}") if choice == "A new name…" else choice
    if st.button("Apply", type="primary", disabled=not (new_name or "").strip(), key=f"apply-{prop}"):
        with db.connect() as conn:
            for p in folder_properties:
                if p.display_name == prop:
                    db.rename_property(conn, p.id, new_name)
            portfolio.rename_property(conn, approved, prop, new_name)
        st.toast(f"{prop} is now {new_name}.", icon=":material/check:")
        st.rerun()
