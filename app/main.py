"""LeaseDesk entry point. Run with:  streamlit run app/main.py"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st  # noqa: E402

st.set_page_config(page_title="LeaseDesk", page_icon=":material/apartment:", layout="wide")

pages = {
    "": [
        st.Page("views/dashboard.py", title="Dashboard", icon=":material/space_dashboard:", default=True),
    ],
    "Portfolio": [
        st.Page("views/properties.py", title="Properties", icon=":material/location_city:"),
        st.Page("views/tenants.py", title="Tenants", icon=":material/storefront:"),
        st.Page("views/library.py", title="Lease library", icon=":material/menu_book:"),
        st.Page("views/critical_dates.py", title="Critical dates", icon=":material/event:"),
    ],
    "Accounting": [
        st.Page("views/rent_roll.py", title="Rent roll check", icon=":material/receipt_long:"),
        st.Page("views/distributions.py", title="Partner distributions", icon=":material/account_balance:"),
    ],
    "Leases": [
        st.Page("views/import_leases.py", title="Import leases", icon=":material/upload_file:"),
        st.Page("views/review.py", title="Review abstracts", icon=":material/fact_check:"),
    ],
    "App": [
        st.Page("views/settings.py", title="Settings", icon=":material/settings:"),
    ],
}

with st.sidebar:
    st.markdown("### :material/apartment: LeaseDesk")
    st.caption("Retail lease administration")

st.navigation(pages).run()
