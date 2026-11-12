"""Lease summaries: one document (the library) or a tenant's merged current terms (Tenants page)."""

from datetime import date

import pandas as pd
import streamlit as st

from app import ui
from leasedesk import portfolio
from leasedesk.db import LeaseRecord
from leasedesk.merge import MergedTerms
from leasedesk.models import LeaseAbstract


def render(record: LeaseRecord, today: date) -> None:
    """One document's abstract, with buttons to edit it or download the PDF."""
    a = record.abstract
    h1, h2, h3 = st.columns([4, 1, 1])
    h1.subheader(a.display_name())
    h1.caption(f"{a.tenant_name or ''} · {portfolio.record_property(record)} · {record.file_name}")
    if h2.button("Edit", icon=":material/edit:", width="stretch", key=f"edit-{record.id}"):
        ui.open_in_review(record.id)
    with open(record.stored_path, "rb") as fh:
        h3.download_button("PDF", fh.read(), file_name=record.file_name, mime="application/pdf",
                           icon=":material/picture_as_pdf:", width="stretch",
                           key=f"pdf-{record.id}")
    render_terms(a, today)


def render_terms(a: LeaseAbstract, today: date, merged: MergedTerms | None = None) -> None:
    """The terms, laid out in panels. With ``merged``, values that a later document changed
    are labelled with that document."""
    base_id = merged.applied[0].id if merged else None

    def src(name: str) -> str:
        s = merged.source(name) if merged else None
        return f" :gray[· from {s.label}]" if s and s.doc_id != base_id else ""

    def helptext(name: str) -> str | None:
        s = merged.source(name) if merged else None
        return f"From {s.label}: {s.file_name}" if s else None

    k = st.columns(4)
    k[0].metric("Square feet", f"{a.square_feet:,.0f}" if a.square_feet else "—", help=helptext("square_feet"))
    k[1].metric("Current monthly rent", ui.money(a.current_monthly_rent(today), cents=True),
                help=helptext("base_rent_schedule"))
    k[2].metric("Expires", ui.fmt_date(a.expiration_date), help=helptext("expiration_date"))
    k[3].metric("Pro-rata share", ui.pct(a.recoveries.pro_rata_share_pct),
                help=helptext("recoveries.pro_rata_share_pct"))

    left, right = st.columns(2, gap="large")
    with left:
        with st.container(border=True):
            st.markdown("**Term**")
            st.markdown(f"Lease date: {ui.fmt_date(a.lease_date)}  \n"
                        f"Commencement: {ui.fmt_date(a.commencement_date)}  \n"
                        f"Rent commencement: {ui.fmt_date(a.rent_commencement_date)}  \n"
                        f"Expiration: {ui.fmt_date(a.expiration_date)}{src('expiration_date')}  \n"
                        f"Suite: {a.suite or '—'}{src('suite')}  \n"
                        f"Tenant: {a.tenant_name or '—'}{src('tenant_name')}  \n"
                        f"Security deposit: {ui.money(a.security_deposit, cents=True)}{src('security_deposit')}  \n"
                        f"Guarantor: {a.guarantor or '—'}{src('guarantor')}")
        with st.container(border=True):
            st.markdown(f"**Base rent schedule**{src('base_rent_schedule')}")
            st.dataframe(
                pd.DataFrame([{"Start": s.start_date, "End": s.end_date, "Monthly": s.monthly_rent,
                               "$/SF/yr": s.annual_rent_psf,
                               "Current": "◀" if (s.start_date or date.min) <= today <= (s.end_date or date.max)
                               else ""}
                              for s in a.base_rent_schedule]),
                hide_index=True, width="stretch",
                column_config={"Start": st.column_config.DateColumn(format="MM/DD/YYYY"),
                               "End": st.column_config.DateColumn(format="MM/DD/YYYY"),
                               "Monthly": st.column_config.NumberColumn(format="dollar"),
                               "$/SF/yr": st.column_config.NumberColumn(format="$%.2f")})
            if a.percentage_rent:
                pr = a.percentage_rent
                st.markdown(f"**Percentage rent:** {ui.pct(pr.rate_pct)} of sales over "
                            f"{ui.money(pr.breakpoint_amount)} ({pr.breakpoint_type or 'breakpoint'})"
                            f"{src('percentage_rent')}")
    with right:
        r = a.recoveries
        with st.container(border=True):
            st.markdown("**Recoveries**")
            paid = (("CAM", r.pays_cam), ("taxes", r.pays_taxes), ("insurance", r.pays_insurance))
            passes = [n for n, v in paid if v]
            label = r.structure.value.replace("_", " ")
            structure = label.upper() if r.structure.value == "nnn" else label.title()
            cap_kind = f" ({r.cam_cap_type.value.replace('_', '-')})" if r.cam_cap_pct else ""
            st.markdown(f"Structure: {structure}{src('recoveries.structure')}  \n"
                        f"Pays: {', '.join(passes) or '—'}  \n"
                        f"Pro-rata share: {ui.pct(r.pro_rata_share_pct)}{src('recoveries.pro_rata_share_pct')}  \n"
                        f"CAM cap: {ui.pct(r.cam_cap_pct)}{cap_kind}{src('recoveries.cam_cap_pct')}  \n"
                        f"Base year: {r.base_year or '—'}{src('recoveries.base_year')}  \n"
                        f"Admin fee: {ui.pct(r.admin_fee_pct)}{src('recoveries.admin_fee_pct')} · "
                        f"Gross-up: {ui.pct(r.gross_up_pct)}{src('recoveries.gross_up_pct')}")
            if r.excluded_expenses:
                st.caption("Excluded: " + "; ".join(r.excluded_expenses))
        with st.container(border=True):
            st.markdown(f"**Options**{src('options')}")
            if a.options:
                for o in a.options:
                    st.markdown(f"- **{o.option_type.value.replace('_', ' ').title()}**, notice by "
                                f"{ui.fmt_date(o.notice_deadline)}: {o.description}")
            else:
                st.write("None")
        with st.container(border=True):
            st.markdown("**Use and restrictions**")
            for label, value in (("Permitted use", a.permitted_use), ("Exclusive", a.exclusive_use),
                                 ("Co-tenancy", a.co_tenancy), ("Radius", a.radius_restriction),
                                 ("Notes", a.notes)):
                if value:
                    st.markdown(f"**{label}:** {value}")
