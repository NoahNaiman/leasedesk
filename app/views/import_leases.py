from pathlib import Path

import pandas as pd
import streamlit as st

from app import ui
from leasedesk import config, db, documents, extraction, library, settings

st.title("Import leases")
st.write("LeaseDesk reads your property folders where they are. Nothing is copied or moved. "
         "Every AI abstract goes to **Review** before it reaches your library.")

api_key = settings.get_api_key()


def need_api_key() -> bool:
    if api_key:
        return False
    st.warning("Add your Anthropic API key on the Settings page to use the AI steps.", icon=":material/key:")
    st.page_link("views/settings.py", label="Open Settings", icon=":material/settings:")
    return True


# --------------------------------------------------------------------------- 1. Library folder
st.subheader("1. Property folders")
saved_root = settings.get_library_folder()
with st.container(border=True):
    st.write("The folder that holds **one subfolder per property**. Inside each property, put each "
             "tenant's documents in its own subfolder. Folders named Old, Former, Expired, Insurance, "
             "CAM, Tax, Partnership, Photos and similar are skipped automatically.")
    st.caption("Tip: in Finder, right-click the folder while holding Option and choose **Copy as Pathname**.")
    c1, c2 = st.columns([3, 1], vertical_alignment="bottom")
    root_text = c1.text_input("Folder location", value=saved_root or "", placeholder="/Users/you/Properties")
    root = Path(root_text.strip().strip("'\"")).expanduser() if root_text.strip() else None
    if c2.button("Scan folders", type="primary", width="stretch", disabled=root is None,
                 icon=":material/folder_open:"):
        if not root.is_dir():
            st.error("That folder could not be found. Check the location and try again.")
        else:
            settings.save_library_folder(str(root))
            bar = st.progress(0.0, text="Scanning…")
            with db.connect() as conn:
                summary = library.scan(conn, root, on_progress=lambda n, total, name: bar.progress(
                    n / total, text=f"Scanning {name}…"))
            st.session_state["scan_summary"] = summary
            st.rerun()
    if summary := st.session_state.pop("scan_summary", None):
        st.success(f"Scanned {summary.properties} property folders and {summary.files:,} files "
                   f"({summary.new_or_changed:,} new or changed). Scanning is free: nothing was sent anywhere.")

library_root = Path(saved_root) if saved_root else None
with db.connect() as conn:
    counts = library.property_file_counts(conn)

if library_root and counts:
    # ----------------------------------------------------------------------- 2. Choose properties
    st.subheader("2. Find the lease documents")
    st.write("Untick a property to leave it out of LeaseDesk entirely. Rename properties on the "
             "**Properties** page.")
    table = pd.DataFrame([{
        "id": c.property.id, "Include": not c.property.excluded, "Property": c.property.display_name,
        "Folder": c.property.folder_name, "Files": c.files, "Lease documents": c.lease_docs,
        "Unclear": c.unclear, "Skipped": c.skipped, "Added": c.added,
    } for c in counts])
    edited = st.data_editor(
        table, hide_index=True, width="stretch", key="property_table",
        disabled=[c for c in table.columns if c != "Include"],
        column_order=["Include", "Property", "Files", "Lease documents", "Unclear", "Skipped", "Added"],
        column_config={"Include": st.column_config.CheckboxColumn(width="small"),
                       "Lease documents": st.column_config.NumberColumn(help="Current lease documents found"),
                       "Unclear": st.column_config.NumberColumn(help="PDFs whose names don't say what they are"),
                       "Added": st.column_config.NumberColumn(help="Already added to LeaseDesk")},
    )
    changed = edited[edited["Include"] != table["Include"]]
    if not changed.empty:
        with db.connect() as conn:
            for _, row in changed.iterrows():
                db.set_property_excluded(conn, int(row["id"]), not row["Include"])
        st.rerun()

    included = [c for c in counts if not c.property.excluded]
    names = {c.property.display_name: c for c in included}
    chosen = st.multiselect("Work on these properties", list(names), key="chosen_properties",
                            placeholder="Choose one or more properties")
    selected = [names[n] for n in chosen]
    ids = [c.property.id for c in selected]

    if selected:
        unclear = sum(c.unclear for c in selected)
        with db.connect() as conn:
            to_add = conn.execute(
                f"SELECT COUNT(*) FROM file_index WHERE decision = 'lease_doc' AND document_id IS NULL "
                f"AND property_id IN ({','.join('?' * len(ids))})", ids).fetchone()[0]

        a, b = st.columns(2)
        with a.container(border=True):
            st.markdown(f"**Check unclear files** ({unclear})")
            st.caption("A fast, inexpensive AI model reads the first three pages of each unclear PDF "
                       f"and decides whether it's a lease document. Estimated cost: "
                       f"about ${library.estimate_triage_cost(unclear):,.2f}.")
            if st.button("Check with AI", disabled=unclear == 0 or not api_key, icon=":material/manage_search:"):
                bar = st.progress(0.0, text="Checking…")
                with db.connect() as conn:
                    stats = library.triage(conn, extraction.make_client(api_key), library_root, ids,
                                           on_progress=lambda n, total, name: bar.progress(
                                               n / total, text=f"{n} of {total}: {Path(name).name}"))
                st.session_state["triage_stats"] = stats
                st.rerun()
        with b.container(border=True):
            st.markdown(f"**Add lease documents** ({to_add})")
            st.caption("Registers the lease documents found so far, reading them in place. "
                       "Free: nothing is sent to the AI yet.")
            if st.button("Add to LeaseDesk", type="primary", disabled=to_add == 0, icon=":material/playlist_add:"):
                bar = st.progress(0.0, text="Adding…")
                with db.connect() as conn:
                    result = library.add_lease_documents(conn, library_root, ids, on_progress=lambda n, total, name:
                                                         bar.progress(n / total, text=Path(name).name))
                st.session_state["add_result"] = result
                st.rerun()
        if not api_key:
            need_api_key()
        if stats := st.session_state.pop("triage_stats", None):
            st.success(f"Checked {stats['lease_docs'] + stats['other']} files: {stats['lease_docs']} lease "
                       f"documents, {stats['other']} other. Cost: ${stats['cost']:.2f}."
                       + (f" {stats['errors']} could not be read and are still marked unclear."
                          if stats["errors"] else ""))
        if result := st.session_state.pop("add_result", None):
            st.success(f"Added {result.added} lease documents."
                       + (f" {result.already_present} were already in LeaseDesk." if result.already_present else ""))
            for path, error in result.errors:
                st.error(f"**{path}**: {error}")

        # Per-file view, so any wrong call can be fixed by hand.
        labels = {library.DECISION_LEASE: "Lease document", library.DECISION_UNCLEAR: "Unclear",
                  library.DECISION_SKIPPED: "Skip"}
        reverse = {v: k for k, v in labels.items()}
        for c in selected:
            with st.expander(f"Files in {c.property.display_name}"):
                with db.connect() as conn:
                    files = library.files_for_property(conn, c.property.id)
                show_skipped = st.toggle("Show skipped files", key=f"skipped_{c.property.id}")
                rows = [f for f in files if show_skipped or f["decision"] != library.DECISION_SKIPPED]
                prefix = c.property.folder_name + "/"
                frame = pd.DataFrame([{
                    "id": f["id"], "File": f["rel_path"].removeprefix(prefix), "Tenant folder": f["tenant_folder"],
                    "Decision": labels[f["decision"]], "Why": f["reason"],
                    "Added": f["document_id"] is not None,
                } for f in rows])
                if frame.empty:
                    st.write("No files to show.")
                    continue
                st.caption("Change a file's decision if it's wrong. Your choice is kept on future scans.")
                edited_files = st.data_editor(
                    frame, hide_index=True, width="stretch", key=f"files_{c.property.id}",
                    disabled=["File", "Tenant folder", "Why", "Added"], column_order=list(frame.columns)[1:],
                    column_config={"Decision": st.column_config.SelectboxColumn(options=list(labels.values()),
                                                                                required=True)},
                )
                diff = edited_files[edited_files["Decision"] != frame["Decision"]]
                if not diff.empty:
                    with db.connect() as conn:
                        for _, row in diff.iterrows():
                            library.set_decision(conn, int(row["id"]), reverse[row["Decision"]])
                    st.rerun()

with st.expander("Or upload individual PDFs"):
    files = st.file_uploader("Drag lease PDFs here, or click to choose them", type=["pdf"],
                             accept_multiple_files=True, key=f"uploader_{st.session_state.get('upload_round', 0)}")
    if files and st.button(f"Add {len(files)} file(s)"):
        with db.connect() as conn:
            results = [documents.import_pdf_bytes(conn, f.name, f.getvalue()) for f in files]
        st.session_state["last_import"] = results
        st.session_state["upload_round"] = st.session_state.get("upload_round", 0) + 1
        st.rerun()
    if results := st.session_state.pop("last_import", None):
        added = [r for r in results if r.doc_id]
        dupes = [r for r in results if r.duplicate]
        if added:
            st.success(f"Added {len(added)} file(s).")
        if dupes:
            st.info(f"Skipped {len(dupes)} file(s) that were already imported.")
        for r in results:
            if r.error:
                st.error(f"**{r.file_name}**: {r.error}")

# --------------------------------------------------------------------------- 3. Abstract
st.subheader("3. Abstract with AI")
pending = ui.records(db.STATUS_PENDING)
failed = ui.records(db.STATUS_FAILED)

if not pending:
    st.write("No documents are waiting to be abstracted.")
else:
    by_property = sorted({r.property_name or "Uploaded files" for r in pending})
    pick = st.multiselect("Properties to abstract", by_property, default=by_property, key="abstract_props")
    batch = [r for r in pending if (r.property_name or "Uploaded files") in pick]
    pages = sum(r.page_count or 0 for r in batch)
    scanned = sum(1 for r in batch if r.is_scanned)
    # Rough estimate: ~2,500 input tokens per page plus ~4,000 output tokens per document.
    est = extraction.estimate_cost(pages * 2500, len(batch) * 4000)
    st.write(f"**{len(batch)}** of {len(pending)} waiting documents selected ({pages:,} pages, {scanned} scanned). "
             f"Estimated AI cost: **about ${est:,.2f}**.")
    with st.expander("Documents waiting"):
        st.dataframe([{"Property": r.property_name or "Uploaded", "Tenant folder": r.tenant_folder,
                       "File": r.file_name, "Pages": r.page_count, "Scanned": "Yes" if r.is_scanned else "No"}
                      for r in batch], hide_index=True, width="stretch")
        if st.button("Remove these from LeaseDesk", icon=":material/delete:",
                     help="Your original files are never touched."):
            with db.connect() as conn:
                for r in batch:
                    db.delete_document(conn, r.id)
                    documents.remove_stored_file(r.stored_path)
            st.rerun()

    if not need_api_key() and st.button(f"Abstract {len(batch)} document(s)", type="primary",
                                        icon=":material/auto_awesome:", disabled=not batch):
        bar = st.progress(0.0, text="Starting…")
        log = st.container()

        def on_progress(done, total, name, error):
            bar.progress(done / total, text=f"{done} of {total} done: {name}")
            if error:
                log.error(f"**{name}**: {error}")

        with db.connect() as conn:
            ok, bad = extraction.process_documents(
                conn, extraction.make_client(api_key), [r.id for r in batch], on_progress=on_progress)
        bar.progress(1.0, text="Finished")
        st.success(f"Abstracted {ok} document(s)." + (f" {bad} failed. See below." if bad else ""))
        if ok:
            st.page_link("views/review.py", label="Review the results", icon=":material/arrow_forward:")

# --------------------------------------------------------------------------- Failures
if failed:
    st.subheader("Needs attention")
    for r in failed:
        with st.container(border=True):
            st.markdown(f"**{r.file_name}**" + (f" · {r.property_name}" if r.property_name else ""))
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
st.caption("Property folders are read in place and never changed. Uploaded files are kept in "
           f"{config.DOCUMENTS_DIR}. Document contents are sent to Anthropic's API only for the AI steps.")
