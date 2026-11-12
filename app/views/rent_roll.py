from datetime import date

import streamlit as st

from app import ui
from leasedesk import config, db, portfolio, rentroll, sandbox_exports

st.title("Rent roll check")
st.write("Compare what your accounting system bills each tenant with what their lease says they owe "
         "today, including every amendment. Export a rent roll (CSV or Excel) and upload it here.")

sample = config.DATA_DIR.parent / "exports" / sandbox_exports.RENT_ROLL_FILE
c1, c2 = st.columns([3, 1], vertical_alignment="bottom")
upload = c1.file_uploader("Rent roll export", type=["csv", "xlsx", "xls"])
as_of = c2.date_input("Check rent as of", value=date.today(), format="MM/DD/YYYY")
if sample.exists() and upload is None:
    if st.button("Use the sandbox's sample rent roll", icon=":material/science:"):
        st.session_state["rent_roll_sample"] = True
    st.caption("A fictional export with a few problems planted in it, to show what the check finds.")

if upload is not None:
    data, name = upload.getvalue(), upload.name
    st.session_state.pop("rent_roll_sample", None)
elif st.session_state.get("rent_roll_sample") and sample.exists():
    data, name = sample.read_bytes(), sample.name
else:
    st.stop()

try:
    table = rentroll.read_table(data, name)
except Exception as exc:  # unreadable or unexpected file
    st.error(f"That file couldn't be read as a rent roll: {exc}")
    st.stop()

columns = list(table.columns)
guess = rentroll.guess_mapping(columns)
missing = [f for f in rentroll.REQUIRED if not guess.get(f)]
with st.expander("Columns", expanded=bool(missing)):
    st.caption("LeaseDesk matched these columns by name. Change any that are wrong.")
    mapping = {}
    cols = st.columns(4)
    for i, f in enumerate(rentroll.FIELDS):
        options = ["(not in this file)", *columns]
        index = options.index(guess[f]) if guess.get(f) in options else 0
        choice = cols[i % 4].selectbox(rentroll.FIELD_LABELS[f], options, index=index, key=f"map-{f}")
        mapping[f] = None if choice == options[0] else choice
if any(not mapping.get(f) for f in rentroll.REQUIRED):
    st.warning("Choose which columns hold the tenant name and the monthly rent.")
    st.stop()

rows = rentroll.parse_rows(table, mapping)
with db.connect() as conn:
    approved = db.list_documents(conn, db.STATUS_APPROVED)
    in_review = db.list_documents(conn, db.STATUS_REVIEW)
report = rentroll.check(rows, approved, as_of, in_review)
frame = report.frame()

m = st.columns(4)
m[0].metric("Rent roll lines", report.rows)
m[1].metric("Matched to a lease", report.matched)
m[2].metric("Clean", report.clean)
m[3].metric("Under-billed / month", ui.money(report.monthly_underbilling),
            help="Rent the leases call for that isn't being billed (missed increases and short billing).")

if frame.empty:
    st.success("Every line matches its lease.", icon=":material/task_alt:")
    st.stop()

high = int((frame["Severity"] == "High").sum())
if high:
    st.error(f"{high} high-priority issue(s): billing that differs from the lease, or a lease that isn't billed.",
             icon=":material/priority_high:")
kinds = st.multiselect("Show", sorted(frame["Issue"].unique()), placeholder="All issues")
view = frame[frame["Issue"].isin(kinds)] if kinds else frame
st.dataframe(
    view, hide_index=True, width="stretch",
    column_config={"Billed": st.column_config.NumberColumn(format="dollar"),
                   "Per lease": st.column_config.NumberColumn(format="dollar"),
                   "Monthly difference": st.column_config.NumberColumn(format="dollar"),
                   "Annual impact": st.column_config.NumberColumn(format="dollar"),
                   "Details": st.column_config.TextColumn(width="large")},
)
st.download_button("Download findings (Excel)", portfolio.to_excel({"Rent roll check": frame}),
                   file_name=f"rent_roll_check_{as_of:%Y-%m-%d}.xlsx", icon=":material/download:")
