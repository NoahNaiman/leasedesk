from datetime import date, timedelta

import streamlit as st

from app import ui
from leasedesk import agenda, corrections, db, demo_data, insurance, portfolio

today = date.today()
all_records = ui.records()

if not all_records:
    st.title("Welcome to LeaseDesk")
    st.write(
        "LeaseDesk reads your lease PDFs, pulls out the key terms with AI, and keeps them in one "
        "searchable place, with a calendar of every expiration, option deadline, and rent increase."
    )
    left, right = st.columns(2)
    with left, st.container(border=True):
        st.subheader(":material/storefront: Try the demo")
        st.write("Load two fictional shopping centers, **Willow Creek Commons** and **Maple Ridge Plaza**, "
                 "with 12 tenants between them.")
        if st.button("Load demo property", type="primary"):
            with db.connect() as conn, st.spinner("Building demo leases…"):
                demo_data.load_demo_into_db(conn)
            st.rerun()
    with right, st.container(border=True):
        st.subheader(":material/upload_file: Use your own leases")
        st.write("Upload lease PDFs (digital or scanned) and let the AI abstract them for your review.")
        st.page_link("views/import_leases.py", label="Go to Import leases", icon=":material/arrow_forward:")
    st.stop()

approved = [r for r in all_records if r.status == db.STATUS_APPROVED]
counts = {s: sum(1 for r in all_records if r.status == s) for s in
          (db.STATUS_PENDING, db.STATUS_REVIEW, db.STATUS_FAILED)}

st.title("Portfolio overview")
if any(r.is_demo for r in all_records):
    st.caption("Showing demo data for a fictional shopping center. Remove it any time from Settings.")

groups = portfolio.tenant_groups(approved)
with db.connect() as conn:
    certificates = insurance.list_certificates(conn)
    requirements = insurance.get_requirements(conn)

ICONS = {agenda.KIND_OPTION_DEADLINE: ":material/gavel:", agenda.KIND_OPTION_REMINDER: ":material/notifications:",
         agenda.KIND_EXPIRATION: ":material/event_busy:", agenda.KIND_RENT_INCREASE: ":material/trending_up:",
         agenda.KIND_COMMENCEMENT: ":material/key:", agenda.KIND_RENT_COMMENCEMENT: ":material/payments:",
         agenda.KIND_COI_EXPIRES: ":material/gpp_bad:", agenda.KIND_COI_REMINDER: ":material/shield:"}

with st.container(border=True):
    head, pick = st.columns([3, 1], vertical_alignment="bottom")
    day = pick.date_input("Day", today, min_value=date(1950, 1, 1), max_value=date(2100, 12, 31), format="MM/DD/YYYY",
                      label_visibility="collapsed")
    head.subheader("Today" if day == today else "Agenda", anchor=False)
    head.caption(f"{day:%A, %B %-d, %Y}")
    todays = agenda.items(groups, certificates, requirements, day)
    if not todays:
        st.write("Nothing scheduled for this day.")
    for item in todays:
        st.markdown(f"{ICONS[item.kind]} **{item.kind}** · {item.tenant}"
                    + (f", {item.suite}" if item.suite else "") + f" · {item.property}  \n"
                    f":gray[{item.detail}]")
    week = agenda.items(groups, certificates, requirements, day + timedelta(days=1), day + timedelta(days=7))
    if week:
        with st.expander(f"Next 7 days ({len(week)})"):
            st.dataframe(agenda.frame(week), hide_index=True, width="stretch",
                         column_config={"Date": st.column_config.DateColumn(format="ddd, MMM D"),
                                        "Details": st.column_config.TextColumn(width="large")})

# Tenant-level figures use each tenant's merged current terms (lease plus amendments).
tenants = portfolio.tenant_table(groups, today)
m1, m2, m3, m4 = st.columns(4)
m1.metric("Tenants", len(tenants), help=f"{len(approved)} approved documents")
m2.metric("Leased square feet", ui.number(tenants["SF"].sum()) if not tenants.empty else "0")
m3.metric("Monthly base rent", ui.money_short(tenants["Monthly rent"].sum()) if not tenants.empty else "$0")
m4.metric("Awaiting review", counts[db.STATUS_REVIEW])

if counts[db.STATUS_REVIEW]:
    st.info(f"{counts[db.STATUS_REVIEW]} lease abstract(s) are waiting for your review before they "
            "appear in the library.", icon=":material/fact_check:")
    st.page_link("views/review.py", label="Review now", icon=":material/arrow_forward:")
if counts[db.STATUS_PENDING] or counts[db.STATUS_FAILED]:
    st.warning(f"{counts[db.STATUS_PENDING]} uploaded lease(s) not yet abstracted, "
               f"{counts[db.STATUS_FAILED]} failed.", icon=":material/pending:")
    st.page_link("views/import_leases.py", label="Go to Import leases", icon=":material/arrow_forward:")

coverage = insurance.tenant_insurance(groups, certificates, requirements, today)
uninsured = sum(r.status in (insurance.STATUS_MISSING, insurance.STATUS_EXPIRED) for r in coverage)
if uninsured:
    st.warning(f"{uninsured} tenant(s) have no current insurance certificate on file.", icon=":material/gpp_bad:")
    st.page_link("views/insurance.py", label="Review insurance", icon=":material/arrow_forward:")

st.subheader("Coming up in the next 90 days")
events = portfolio.critical_dates(approved, today, days_ahead=90)
if events:
    st.dataframe(
        portfolio.critical_dates_frame(events, today),
        hide_index=True,
        width="stretch",
        column_config={
            "Date": st.column_config.DateColumn(format="MMM D, YYYY"),
            "Days away": st.column_config.NumberColumn(format="%d days"),
            "Details": st.column_config.TextColumn(width="large"),
        },
    )
else:
    st.write("Nothing due in the next 90 days.")
st.page_link("views/critical_dates.py", label="See all critical dates", icon=":material/event:")

# How often reviewers changed what the AI drafted, on documents approved so far.
report = corrections.correction_report(approved)
if report.documents:
    st.subheader("How often the AI needed correcting")
    c1, c2, c3 = st.columns(3)
    c1.metric("Approved documents compared", report.documents)
    c2.metric("Fields corrected", f"{report.rate:.1%}", help=f"{report.fields_corrected} of {report.fields} fields")
    c3.metric("Documents with any correction", f"{report.document_rate:.0%}")
    if report.by_field:
        st.caption("Most corrected: " + ", ".join(f"{f.replace('_', ' ')} ({n} of {t})"
                                                   for f, n, t in report.most_corrected()))
