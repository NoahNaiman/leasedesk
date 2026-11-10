from pathlib import Path

import streamlit as st

from app import ui
from leasedesk import config, db, documents, extraction, settings

st.title("Import leases")
st.write("Add lease PDFs, then let the AI abstract them. Scanned leases work too. "
         "Every abstract goes to **Review** before it reaches your library.")

# --------------------------------------------------------------------------- Step 1: add files
st.subheader("1. Add lease files")
tab_upload, tab_folder = st.tabs([":material/upload: Upload files", ":material/folder_open: Import a whole folder"])

with tab_upload:
    files = st.file_uploader("Drag lease PDFs here, or click to choose them", type=["pdf"],
                             accept_multiple_files=True, key=f"uploader_{st.session_state.get('upload_round', 0)}")
    if files and st.button(f"Add {len(files)} file(s)", type="primary"):
        with db.connect() as conn:
            results = [documents.import_pdf_bytes(conn, f.name, f.getvalue()) for f in files]
        st.session_state["last_import"] = results
        st.session_state["upload_round"] = st.session_state.get("upload_round", 0) + 1
        st.rerun()

with tab_folder:
    st.write("Have a lot of files? Paste the location of a folder and every PDF inside it "
             "(including subfolders) will be added.")
    st.caption("Tip: in Finder, right-click the folder while holding Option and choose "
               "**Copy as Pathname**, then paste it below.")
    folder = st.text_input("Folder location", placeholder="/Users/you/Documents/Leases")
    if st.button("Import folder", disabled=not folder):
        path = Path(folder.strip().strip("'\"")).expanduser()
        if not path.is_dir():
            st.error("That folder could not be found. Check the location and try again.")
        else:
            with db.connect() as conn, st.spinner("Adding files…"):
                st.session_state["last_import"] = documents.import_folder(conn, path)
            st.rerun()

if results := st.session_state.pop("last_import", None):
    added = [r for r in results if r.doc_id]
    dupes = [r for r in results if r.duplicate]
    errors = [r for r in results if r.error]
    if added:
        st.success(f"Added {len(added)} file(s).")
    if dupes:
        st.info(f"Skipped {len(dupes)} file(s) that were already imported: "
                + ", ".join(r.file_name for r in dupes[:10]) + ("…" if len(dupes) > 10 else ""))
    for r in errors:
        st.error(f"**{r.file_name}**: {r.error}")
    if not results:
        st.warning("No PDF files were found.")

# --------------------------------------------------------------------------- Step 2: abstract
st.subheader("2. Abstract with AI")
pending = ui.records(db.STATUS_PENDING)
failed = ui.records(db.STATUS_FAILED)

if not pending:
    st.write("No files are waiting to be abstracted.")
else:
    pages = sum(r.page_count or 0 for r in pending)
    scanned = sum(1 for r in pending if r.is_scanned)
    # Rough estimate: ~2,500 input tokens per page plus ~4,000 output tokens per lease.
    est = extraction.estimate_cost(pages * 2500, len(pending) * 4000)
    st.write(f"**{len(pending)}** file(s) ready ({pages:,} pages, {scanned} scanned). "
             f"Estimated AI cost: **about ${est:,.2f}**.")
    with st.expander("Files waiting"):
        st.dataframe([{"File": r.file_name, "Pages": r.page_count, "Scanned": "Yes" if r.is_scanned else "No"}
                      for r in pending], hide_index=True, width="stretch")
        if st.button("Remove these files from the list", icon=":material/delete:"):
            with db.connect() as conn:
                for r in pending:
                    db.delete_document(conn, r.id)
                    documents.remove_stored_file(r.stored_path)
            st.rerun()

    api_key = settings.get_api_key()
    if not api_key:
        st.warning("Add your Anthropic API key on the Settings page to turn on AI abstraction.",
                   icon=":material/key:")
        st.page_link("views/settings.py", label="Open Settings", icon=":material/settings:")
    elif st.button(f"Abstract {len(pending)} lease(s)", type="primary", icon=":material/auto_awesome:"):
        bar = st.progress(0.0, text="Starting…")
        log = st.container()

        def on_progress(done, total, name, error):
            bar.progress(done / total, text=f"{done} of {total} done: {name}")
            if error:
                log.error(f"**{name}**: {error}")

        with db.connect() as conn:
            ok, bad = extraction.process_documents(
                conn, extraction.make_client(api_key), [r.id for r in pending], on_progress=on_progress)
        bar.progress(1.0, text="Finished")
        st.success(f"Abstracted {ok} lease(s)." + (f" {bad} failed. See below." if bad else ""))
        if ok:
            st.page_link("views/review.py", label="Review the results", icon=":material/arrow_forward:")

# --------------------------------------------------------------------------- Failures
if failed:
    st.subheader("Needs attention")
    for r in failed:
        with st.container(border=True):
            st.markdown(f"**{r.file_name}**")
            st.caption(r.error or "Unknown error")
            c1, c2, _ = st.columns([1, 1, 4])
            if c1.button("Try again", key=f"retry_{r.id}"):
                with db.connect() as conn:
                    db.reset_to_pending(conn, r.id)
                st.rerun()
            if c2.button("Remove", key=f"remove_{r.id}"):
                with db.connect() as conn:
                    db.delete_document(conn, r.id)
                documents.remove_stored_file(r.stored_path)
                st.rerun()

st.divider()
st.caption(f"Files are stored on this computer in {config.DOCUMENTS_DIR}. "
           "Lease contents are sent to Anthropic's API only when you click Abstract.")
