from datetime import date

import streamlit as st

from app import ui
from leasedesk import db, portfolio

today = date.today()
approved = ui.records(db.STATUS_APPROVED)

st.title("Critical dates")
st.write("Expirations, option notice deadlines, and rent increases across your approved leases.")

c1, c2, c3 = st.columns([2, 3, 2])
windows = {"Next 30 days": 30, "Next 90 days": 90, "Next 6 months": 182, "Next 12 months": 365,
           "Next 3 years": 365 * 3}
window = c1.selectbox("Show", list(windows), index=1)
types = c2.multiselect("Event types", portfolio.EVENT_TYPES, default=portfolio.EVENT_TYPES)
show_past = c3.checkbox("Include the last 30 days", help="Catch anything that may have been missed.")

events = [e for e in portfolio.critical_dates(approved, today, windows[window],
                                               include_past_days=30 if show_past else 0)
          if e.event in types]

if not events:
    st.info("No critical dates in this window.")
    st.stop()

urgent = [e for e in events if e.event == portfolio.EVENT_OPTION and 0 <= (e.when - today).days <= 60]
if urgent:
    st.error(f"{len(urgent)} option notice deadline(s) in the next 60 days: "
             + "; ".join(f"{e.tenant} by {ui.fmt_date(e.when)}" for e in urgent), icon=":material/alarm:")

frame = portfolio.critical_dates_frame(events, today)
st.dataframe(
    frame, hide_index=True, width="stretch",
    column_config={
        "Date": st.column_config.DateColumn(format="MMM D, YYYY"),
        "Days away": st.column_config.NumberColumn(format="%d days"),
        "Details": st.column_config.TextColumn(width="large"),
    },
)

d1, d2, _ = st.columns([1, 1, 3])
d1.download_button("Excel", portfolio.to_excel({"Critical dates": frame}),
                   file_name=f"critical_dates_{today:%Y-%m-%d}.xlsx", icon=":material/download:",
                   width="stretch")
d2.download_button("Add to calendar", portfolio.to_ics(events), file_name="lease_critical_dates.ics",
                   mime="text/calendar", icon=":material/calendar_add_on:", width="stretch",
                   help="Opens in Apple Calendar, Outlook, or Google Calendar. "
                        "Option deadlines include a reminder two weeks ahead.")
