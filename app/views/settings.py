import streamlit as st

from leasedesk import config, db, demo_data, settings

st.title("Settings")

st.subheader("AI abstraction")
st.write("LeaseDesk uses Anthropic's Claude to read leases. You need an API key from your own "
         "Anthropic account. Usage is billed by Anthropic, typically well under a dollar per lease.")
masked = settings.masked_api_key()
if masked:
    st.success(f"API key saved ({masked}).", icon=":material/key:")
    if st.button("Remove key"):
        settings.clear_api_key()
        st.rerun()
else:
    st.markdown("1. Sign in at [console.anthropic.com](https://console.anthropic.com) and add billing.\n"
                "2. Go to **API Keys** and create a key.\n"
                "3. Paste it below.")
    with st.form("api_key"):
        key = st.text_input("Anthropic API key", type="password", placeholder="sk-ant-…")
        if st.form_submit_button("Save key", type="primary"):
            if not key.strip().startswith("sk-ant-"):
                st.error("That doesn't look like an Anthropic API key. It should start with sk-ant-.")
            else:
                settings.save_api_key(key)
                st.rerun()
st.caption("The key is stored only on this computer, in a file only your user account can read.")

st.subheader("Demo property")
with db.connect() as conn:
    has_demo = any(r.is_demo for r in db.list_documents(conn))
if has_demo:
    st.write("Demo data for **Willow Creek Commons** and **Maple Ridge Plaza** is loaded. Remove it before working with real leases, "
             "or keep it alongside them. Demo leases are tagged, so removing them never touches your own.")
    if st.button("Remove demo data", icon=":material/delete:"):
        with db.connect() as conn:
            demo_data.remove_demo_from_db(conn)
        st.rerun()
else:
    st.write("Load a fictional shopping center to explore LeaseDesk without using real leases.")
    if st.button("Load demo property", icon=":material/storefront:"):
        with db.connect() as conn, st.spinner("Building demo leases…"):
            demo_data.load_demo_into_db(conn)
        st.rerun()

st.subheader("Your data")
st.write(f"Everything is stored on this computer in `{config.DATA_DIR}`. "
         "Back up this folder to keep a copy of your lease library.")
st.caption("Lease PDFs are sent to Anthropic only when you click Abstract. Under Anthropic's commercial "
           "terms, API data is not used to train models.")
