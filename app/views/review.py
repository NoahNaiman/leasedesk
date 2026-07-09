from datetime import date

import pandas as pd
import streamlit as st

from app import ui
from leasedesk import db
from leasedesk.models import (
    CapType,
    Confidence,
    DocumentType,
    LeaseAbstract,
    LeaseOption,
    OptionType,
    PercentageRent,
    Recoveries,
    RecoveryStructure,
    RentStep,
)

st.title("Review abstracts")

queue = ui.records(db.STATUS_REVIEW)
# A lease opened from the library stays pinned here until it is approved or closed.
requested = st.session_state.get("review_doc_id")
record = None
if requested:
    with db.connect() as conn:
        record = db.get_document(conn, requested)

if record is None and not queue:
    st.success("All caught up. There's nothing waiting for review.", icon=":material/task_alt:")
    st.page_link("views/library.py", label="Go to the lease library", icon=":material/menu_book:")
    st.stop()

if record is None:
    st.write(f"**{len(queue)}** abstract(s) waiting. Check each value against the lease, fix anything "
             "wrong, then approve it.")
    queue = sorted(queue, key=lambda r: ((r.property_name or "~").lower(), (r.tenant_folder or "").lower(),
                                         r.file_name.lower()))
    labels = {r.id: " › ".join(x for x in (r.property_name, r.tenant_folder, r.file_name) if x) for r in queue}
    current = st.session_state.get("review_current")
    ids = list(labels)
    chosen = st.selectbox("Lease", ids, index=ids.index(current) if current in ids else 0,
                          format_func=labels.get)
    st.session_state["review_current"] = chosen
    record = next(r for r in queue if r.id == chosen)
else:
    st.session_state["review_current"] = record.id
    c1, c2 = st.columns([5, 1])
    c1.info(f"Editing **{record.file_name}** (currently {record.status.replace('_', ' ')}).",
            icon=":material/edit:")
    if c2.button("Close", width="stretch"):
        st.session_state.pop("review_doc_id", None)
        st.rerun()

a: LeaseAbstract = record.abstract or LeaseAbstract()
page_key = f"page_{record.id}"
st.session_state.setdefault(page_key, 1)

left, right = st.columns([6, 5], gap="large")

# --------------------------------------------------------------------------- Left: the document
with left:
    total = record.page_count or 1
    nav1, nav2, nav3 = st.columns([1, 2, 1])
    if nav1.button("◀ Prev", disabled=st.session_state[page_key] <= 1, width="stretch"):
        st.session_state[page_key] -= 1
        st.rerun()
    nav2.markdown(f"<div style='text-align:center;padding-top:6px'>Page {st.session_state[page_key]} "
                  f"of {total}</div>", unsafe_allow_html=True)
    if nav3.button("Next ▶", disabled=st.session_state[page_key] >= total, width="stretch"):
        st.session_state[page_key] += 1
        st.rerun()
    st.image(ui.page_image(record.stored_path, st.session_state[page_key]), width="stretch")
    if record.is_scanned:
        st.caption(":material/document_scanner: Scanned document. Double-check numbers carefully.")

    if a.evidence:
        st.markdown("**Where the AI found each value**")
        for i, ev in enumerate(a.evidence):
            badge = {"high": ":green-badge[high]", "medium": ":orange-badge[medium]",
                     "low": ":red-badge[low]"}[ev.confidence.value]
            c1, c2 = st.columns([5, 1])
            c1.markdown(f"{badge} **{ev.field}**: _“{ev.quote}”_")
            if ev.page and c2.button(f"p. {ev.page}", key=f"ev_{record.id}_{i}", width="stretch"):
                st.session_state[page_key] = min(max(1, ev.page), total)
                st.rerun()

# --------------------------------------------------------------------------- Right: the abstract form
low = [e.field for e in a.evidence if e.confidence == Confidence.LOW]
with right:
    if low:
        st.warning("The AI was unsure about: " + ", ".join(sorted(set(low))), icon=":material/help:")
    if a.notes:
        st.info(a.notes, icon=":material/sticky_note_2:")

    def enum_index(options, value):
        return list(options).index(value) if value in list(options) else 0

    with st.form(f"review_{record.id}"):
        t_basic, t_rent, t_rec, t_opt, t_use = st.tabs(
            ["Basics", "Rent", "Recoveries", "Options", "Use & clauses"])

        with t_basic:
            c1, c2 = st.columns(2)
            doc_type = c1.selectbox("Document type", list(DocumentType), format_func=lambda d: d.value.title(),
                                    index=enum_index(DocumentType, a.document_type))
            property_name = c2.text_input("Property", a.property_name or "")
            tenant_name = c1.text_input("Tenant (legal name)", a.tenant_name or "")
            trade_name = c2.text_input("Trade name (d/b/a)", a.trade_name or "")
            suite = c1.text_input("Suite", a.suite or "")
            square_feet = c2.number_input("Square feet", value=a.square_feet, min_value=0.0, step=100.0)
            landlord = c1.text_input("Landlord", a.landlord or "")
            guarantor = c2.text_input("Guarantor", a.guarantor or "")
            property_address = st.text_input("Property address", a.property_address or "")
            d1, d2 = st.columns(2)
            lease_date = d1.date_input("Lease date", a.lease_date, format="MM/DD/YYYY")
            commencement = d2.date_input("Commencement date", a.commencement_date, format="MM/DD/YYYY")
            rent_commencement = d1.date_input("Rent commencement date", a.rent_commencement_date,
                                              format="MM/DD/YYYY")
            expiration = d2.date_input("Expiration date", a.expiration_date, format="MM/DD/YYYY")
            security_deposit = d1.number_input("Security deposit ($)", value=a.security_deposit, min_value=0.0)

        with t_rent:
            st.caption("One row per rent period. Add or delete rows as needed.")
            rent_df = st.data_editor(
                pd.DataFrame([s.model_dump() for s in a.base_rent_schedule],
                             columns=["start_date", "end_date", "monthly_rent", "annual_rent_psf"]),
                num_rows="dynamic", width="stretch", key=f"rent_{record.id}",
                column_config={
                    "start_date": st.column_config.DateColumn("Start", format="MM/DD/YYYY"),
                    "end_date": st.column_config.DateColumn("End", format="MM/DD/YYYY"),
                    "monthly_rent": st.column_config.NumberColumn("Monthly rent", format="dollar"),
                    "annual_rent_psf": st.column_config.NumberColumn("Annual $/SF", format="$%.2f"),
                })
            pr = a.percentage_rent or PercentageRent()
            st.markdown("**Percentage rent**")
            p1, p2, p3 = st.columns(3)
            pr_rate = p1.number_input("Rate (%)", value=pr.rate_pct, min_value=0.0, step=0.5)
            pr_type = p2.selectbox("Breakpoint", ["", "natural", "artificial"],
                                   index=["", "natural", "artificial"].index(pr.breakpoint_type or ""))
            pr_amount = p3.number_input("Breakpoint ($)", value=pr.breakpoint_amount, min_value=0.0)
            pr_reporting = st.text_input("Sales reporting", pr.sales_reporting or "")

        with t_rec:
            r = a.recoveries
            c1, c2 = st.columns(2)
            structure = c1.selectbox("Structure", list(RecoveryStructure),
                                     format_func=lambda s: s.value.replace("_", " ").upper()
                                     if s.value == "nnn" else s.value.replace("_", " ").title(),
                                     index=enum_index(RecoveryStructure, r.structure))
            share = c2.number_input("Pro-rata share (%)", value=r.pro_rata_share_pct, min_value=0.0,
                                    max_value=100.0, format="%.4f")
            denominator = st.text_input("How the share is calculated", r.pro_rata_denominator_notes or "")
            k1, k2, k3 = st.columns(3)
            pays_cam = k1.checkbox("Pays CAM", value=bool(r.pays_cam))
            pays_taxes = k2.checkbox("Pays taxes", value=bool(r.pays_taxes))
            pays_ins = k3.checkbox("Pays insurance", value=bool(r.pays_insurance))
            c1, c2 = st.columns(2)
            cap_pct = c1.number_input("CAM cap (%)", value=r.cam_cap_pct, min_value=0.0, step=0.5)
            cap_type = c2.selectbox("Cap type", list(CapType),
                                    format_func=lambda c: c.value.replace("_", "-").title(),
                                    index=enum_index(CapType, r.cam_cap_type))
            base_year = c1.number_input("Base year", value=r.base_year, min_value=1950, max_value=2100, step=1)
            admin_fee = c2.number_input("Admin fee (%)", value=r.admin_fee_pct, min_value=0.0, step=0.5)
            gross_up = c1.number_input("Gross-up to occupancy (%)", value=r.gross_up_pct, min_value=0.0,
                                       max_value=100.0)
            exclusions = st.text_area("Excluded expenses (one per line)", "\n".join(r.excluded_expenses))

        with t_opt:
            st.caption("Renewal, termination, and other options. Add or delete rows as needed.")
            opt_df = st.data_editor(
                pd.DataFrame([o.model_dump() for o in a.options],
                             columns=["option_type", "notice_deadline", "term_months", "description"]),
                num_rows="dynamic", width="stretch", key=f"opts_{record.id}",
                column_config={
                    "option_type": st.column_config.SelectboxColumn(
                        "Type", options=[o.value for o in OptionType], required=True),
                    "notice_deadline": st.column_config.DateColumn("Notice deadline", format="MM/DD/YYYY"),
                    "term_months": st.column_config.NumberColumn("Term (months)", step=1),
                    "description": st.column_config.TextColumn("Description", width="large"),
                })

        with t_use:
            permitted_use = st.text_area("Permitted use", a.permitted_use or "")
            exclusive_use = st.text_area("Exclusive use", a.exclusive_use or "")
            co_tenancy = st.text_area("Co-tenancy", a.co_tenancy or "")
            radius = st.text_area("Radius restriction", a.radius_restriction or "")
            notes = st.text_area("Notes", a.notes or "")

        s1, s2, _ = st.columns([1, 1, 2])
        if record.status == db.STATUS_APPROVED:
            # Already in the library: saving keeps it approved.
            approve = s1.form_submit_button("Save changes", type="primary", icon=":material/save:",
                                            width="stretch")
            save = False
        else:
            approve = s1.form_submit_button("Approve", type="primary", icon=":material/check:", width="stretch")
            save = s2.form_submit_button("Save draft", width="stretch")


def _clean(v):
    """Turn blanks and NaN/NaT from the editors into None."""
    if v is None or (isinstance(v, float) and pd.isna(v)) or v is pd.NaT:
        return None
    if isinstance(v, str):
        return v.strip() or None
    if isinstance(v, pd.Timestamp):
        return v.date()
    return v


def _rows(df: pd.DataFrame) -> list[dict]:
    return [{k: _clean(v) for k, v in row.items()} for row in df.to_dict("records")]


if approve or save:
    rent_steps = [RentStep(**row) for row in _rows(rent_df) if any(row.values())]
    options = [LeaseOption(option_type=row["option_type"], notice_deadline=row["notice_deadline"],
                           term_months=int(row["term_months"]) if row["term_months"] else None,
                           description=row["description"] or "")
               for row in _rows(opt_df) if row.get("option_type")]
    updated = a.model_copy(update=dict(
        document_type=doc_type,
        landlord=_clean(landlord), tenant_name=_clean(tenant_name), trade_name=_clean(trade_name),
        guarantor=_clean(guarantor), property_name=_clean(property_name),
        property_address=_clean(property_address), suite=_clean(suite), square_feet=square_feet,
        lease_date=lease_date, commencement_date=commencement, rent_commencement_date=rent_commencement,
        expiration_date=expiration, security_deposit=security_deposit,
        base_rent_schedule=sorted(rent_steps, key=lambda s: s.start_date or date.min),
        percentage_rent=PercentageRent(rate_pct=pr_rate, breakpoint_type=pr_type or None,
                                       breakpoint_amount=pr_amount, sales_reporting=_clean(pr_reporting))
        if pr_rate else None,
        recoveries=Recoveries(
            structure=structure, pro_rata_share_pct=share, pro_rata_denominator_notes=_clean(denominator),
            pays_cam=pays_cam, pays_taxes=pays_taxes, pays_insurance=pays_ins,
            cam_cap_pct=cap_pct, cam_cap_type=cap_type, base_year=int(base_year) if base_year else None,
            admin_fee_pct=admin_fee, gross_up_pct=gross_up,
            excluded_expenses=[x.strip() for x in exclusions.splitlines() if x.strip()]),
        options=options,
        permitted_use=_clean(permitted_use), exclusive_use=_clean(exclusive_use),
        co_tenancy=_clean(co_tenancy), radius_restriction=_clean(radius), notes=_clean(notes),
    ))
    # Re-validate so bad input from the editors surfaces here, not later.
    updated = LeaseAbstract.model_validate(updated.model_dump())
    with db.connect() as conn:
        db.save_review(conn, record.id, updated, approve=approve)
    if approve:
        st.session_state.pop("review_current", None)
        st.session_state.pop("review_doc_id", None)
        st.toast(f"Approved {updated.display_name()}", icon=":material/check_circle:")
    else:
        st.toast("Draft saved", icon=":material/save:")
    st.rerun()
