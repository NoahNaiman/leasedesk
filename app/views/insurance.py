import hashlib
from datetime import date
from pathlib import Path

import streamlit as st

from app import ui
from leasedesk import config, db, extraction, insurance, portfolio, settings

today = date.today()
st.title("Insurance")
st.write("Each tenant's certificate of insurance, checked against your requirements: missing or expired "
         "certificates, limits that are too low, and certificates that don't name the landlord.")

with db.connect() as conn:
    req = insurance.get_requirements(conn)
    certificates = insurance.list_certificates(conn)
    groups = portfolio.tenant_groups(db.list_documents(conn, db.STATUS_APPROVED))
rows = insurance.tenant_insurance(groups, certificates, req, today)

with st.expander("Requirements"):
    with st.form("requirements"):
        c1, c2, c3, c4 = st.columns(4)
        occ = c1.number_input("GL each occurrence", 0.0, value=float(req.gl_each_occurrence), step=100_000.0,
                              format="%.0f")
        agg = c2.number_input("GL aggregate", 0.0, value=float(req.gl_aggregate), step=100_000.0, format="%.0f")
        soon = c3.number_input("Warn days before expiry", 0, 365, req.expiring_soon_days)
        addl = c4.checkbox("Landlord must be additional insured", req.additional_insured)
        st.caption("Umbrella or excess coverage counts toward both limits.")
        if st.form_submit_button("Save requirements"):
            with db.connect() as conn:
                insurance.save_requirements(conn, insurance.Requirements(occ, agg, addl, int(soon)))
            st.rerun()

if not rows:
    st.write("No active tenants yet. Tenants appear here once their leases are approved.")
    st.stop()

counts = {s: sum(r.status == s for r in rows) for s in insurance.STATUS_ORDER}
m = st.columns(4)
m[0].metric("Tenants", len(rows))
m[1].metric("Current", counts[insurance.STATUS_CURRENT])
m[2].metric("Missing or expired", counts[insurance.STATUS_MISSING] + counts[insurance.STATUS_EXPIRED])
m[3].metric("Other problems", len(rows) - counts[insurance.STATUS_CURRENT] - counts[insurance.STATUS_MISSING]
            - counts[insurance.STATUS_EXPIRED])
urgent = counts[insurance.STATUS_MISSING] + counts[insurance.STATUS_EXPIRED]
if urgent:
    st.error(f"{urgent} tenant(s) have no current certificate on file.", icon=":material/gpp_bad:")

table = insurance.frame(rows)
statuses = st.multiselect("Show", [s for s in insurance.STATUS_ORDER if counts[s]], placeholder="All statuses")
view = table[table["Status"].isin(statuses)] if statuses else table
event = st.dataframe(
    view, hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row",
    column_config={"Expires": st.column_config.DateColumn(format="MMM D, YYYY"),
                   "GL each occurrence": st.column_config.NumberColumn(format="dollar"),
                   "GL aggregate": st.column_config.NumberColumn(format="dollar"),
                   "Umbrella": st.column_config.NumberColumn(format="dollar"),
                   "Problems": st.column_config.TextColumn(width="large")})
st.download_button("Download (Excel)", portfolio.to_excel({"Insurance": table}),
                   file_name=f"insurance_{today:%Y-%m-%d}.xlsx", icon=":material/download:")
st.caption("Click a tenant to see its certificates or add a new one.")
if not event.selection.rows:
    st.stop()

pick = view.iloc[event.selection.rows[0]]
tenant = next(r for r in rows if r.tenant == pick["Tenant"] and r.property == pick["Property"])
st.divider()
st.subheader(tenant.tenant)
st.caption(f"{tenant.property} · {tenant.status}" + (f": {' '.join(tenant.problems)}" if tenant.problems else ""))

on_file = sorted([c for c in certificates if (c.property, c.tenant) == (tenant.property, tenant.tenant)],
                 key=lambda c: c.certificate.expiration_date or date.min, reverse=True)
for c in on_file:
    cc = c.certificate
    with st.container(border=True):
        a, b, d = st.columns([5, 1, 1], vertical_alignment="center")
        a.markdown(f"**{cc.carrier or 'Carrier not shown'}** · policy {cc.policy_number or '—'}  \n"
                   f"{ui.fmt_date(cc.effective_date)} to {ui.fmt_date(cc.expiration_date)} · GL "
                   f"{ui.money(cc.gl_each_occurrence)} / {ui.money(cc.gl_aggregate)}"
                   + (f" · umbrella {ui.money(cc.umbrella_limit)}" if cc.umbrella_limit else "")
                   + f" · additional insured: {'yes' if cc.additional_insured else 'no'}")
        if c.stored_path and Path(c.stored_path).exists():
            b.download_button("PDF", Path(c.stored_path).read_bytes(), file_name=c.file_name or "certificate.pdf",
                              key=f"coi-pdf-{c.id}", width="stretch")
        if d.button("Delete", key=f"coi-del-{c.id}", width="stretch"):
            with db.connect() as conn:
                insurance.delete_certificate(conn, c.id)
            st.rerun()

st.markdown("**Add a certificate**")
upload = st.file_uploader("Certificate PDF (optional)", type=["pdf"], key=f"coi-upload-{tenant.tenant}")
draft_key = f"coi-draft-{tenant.property}-{tenant.tenant}"
api_key = settings.get_api_key()
if upload is not None and api_key and st.button("Read it with AI", icon=":material/auto_awesome:"):
    path = config.DATA_DIR / "certificates" / f"{hashlib.sha256(upload.getvalue()).hexdigest()}.pdf"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(upload.getvalue())
    with st.spinner("Reading the certificate…"):
        try:
            cert, _, _ = insurance.extract_certificate(extraction.make_client(api_key), path)
            st.session_state[draft_key] = cert
        except Exception as exc:
            st.error(f"The certificate couldn't be read: {exc}. Enter it by hand below.")
elif upload is not None and not api_key:
    st.caption("Add an API key in Settings to fill this in automatically; you can also type it below.")

draft: insurance.Certificate = st.session_state.get(draft_key) or insurance.Certificate()
with st.form(f"coi-form-{tenant.tenant}"):
    c1, c2 = st.columns(2)
    carrier = c1.text_input("Carrier", draft.carrier or "")
    policy = c2.text_input("Policy number", draft.policy_number or "")
    c3, c4 = st.columns(2)
    effective = c3.date_input("Effective", draft.effective_date or today, format="MM/DD/YYYY")
    expires = c4.date_input("Expires", draft.expiration_date or today.replace(year=today.year + 1),
                            format="MM/DD/YYYY")
    c5, c6, c7 = st.columns(3)
    occ = c5.number_input("GL each occurrence", 0.0, value=float(draft.gl_each_occurrence or 0), step=100_000.0,
                          format="%.0f")
    agg = c6.number_input("GL aggregate", 0.0, value=float(draft.gl_aggregate or 0), step=100_000.0, format="%.0f")
    umb = c7.number_input("Umbrella", 0.0, value=float(draft.umbrella_limit or 0), step=1_000_000.0, format="%.0f")
    addl = st.checkbox("Landlord is an additional insured", bool(draft.additional_insured))
    if st.form_submit_button("Save certificate", type="primary"):
        cert = insurance.Certificate(insured_name=draft.insured_name or tenant.tenant, carrier=carrier or None,
                                     policy_number=policy or None, effective_date=effective, expiration_date=expires,
                                     gl_each_occurrence=occ or None, gl_aggregate=agg or None,
                                     umbrella_limit=umb or None, additional_insured=addl,
                                     certificate_holder=draft.certificate_holder)
        stored = None
        if upload is not None:
            stored = config.DATA_DIR / "certificates" / f"{hashlib.sha256(upload.getvalue()).hexdigest()}.pdf"
            stored.parent.mkdir(parents=True, exist_ok=True)
            stored.write_bytes(upload.getvalue())
        with db.connect() as conn:
            insurance.save_certificate(conn, tenant.property, tenant.tenant, cert,
                                       source="ai" if st.session_state.get(draft_key) else "manual",
                                       file_name=upload.name if upload else None,
                                       stored_path=str(stored) if stored else None)
        st.session_state.pop(draft_key, None)
        st.toast("Certificate saved.", icon=":material/check:")
        st.rerun()
