"""History of immutable master and per-property statements, with email delivery."""
from zoneinfo import ZoneInfo
import pandas as pd
import streamlit as st
from psycopg import Error as DatabaseError
from statement_store import (list_statements,get_statement,ensure_property_pdfs,
                             list_property_pdfs,get_property_pdf,StatementError)
import statement_delivery_ui


def render(building_id):
    st.subheader('Ιστορικό κοινοχρήστων')
    st.caption('Εκδοθείσες εκκαθαρίσεις. Τα παλιά PDF και ποσά δεν επανυπολογίζονται.')
    try:
        statements=list_statements(building_id)
    except DatabaseError:
        st.error('Δεν ήταν δυνατή η φόρτωση του ιστορικού. Ελέγξτε τη σύνδεση και τα migrations.')
        return
    if not statements:
        st.info('Δεν έχουν εκδοθεί ακόμη εκκαθαρίσεις για αυτή την πολυκατοικία.')
        return
    st.dataframe(pd.DataFrame([{'Περίοδος':s['period_key'],'Έκδοση':s['revision'],
        'Έκδοση στις (Αθήνα)':s['issued_at'].astimezone(ZoneInfo('Europe/Athens')).strftime('%d/%m/%Y %H:%M'),
        'Ιδιοκτησίες':s['property_count'],'Σύνολο €':float(s['total'])} for s in statements]),
        hide_index=True,use_container_width=True)
    chosen=st.selectbox('Επιλογή εκκαθάρισης',statements,
        format_func=lambda s:f"{s['period_key']} · Έκδοση {s['revision']} · {s['issued_at'].astimezone(ZoneInfo('Europe/Athens')):%d/%m/%Y}",
        key=f'history_selection_{building_id}')
    try:
        statement=get_statement(building_id,chosen['id'])
        report=statement['report']
        st.metric('Σύνολο εκκαθάρισης',f"{float(report['grand_total']):,.2f} €")
        st.download_button('Λήψη συνολικού PDF',statement['pdf'],
            file_name=f"koinoxrista_{statement['period_key']}_v{statement['revision']}.pdf",
            mime='application/pdf',key=f"history_pdf_{statement['id']}")
        tab_files,tab_email=st.tabs(['Ατομικά PDF','Αποστολή email'])
        with tab_files:
            documents=ensure_property_pdfs(building_id,statement['id'])
            st.dataframe(pd.DataFrame([{'Ιδιοκτησία':d['property']['code'],
                'Όνομα / περιγραφή':d['property'].get('name',''),
                'Ένοικος':d['property'].get('tenant_name',''),
                'Ιδιοκτήτης':d['property'].get('owner_name',''),
                'Ενοίκου €':float(d['property']['totals']['TENANT']),
                'Ιδιοκτήτη €':float(d['property']['totals']['OWNER']),
                'Λοιπές €':float(d['property']['totals']['OTHER']),
                'Σύνολο €':float(d['property']['total'])} for d in documents]),
                hide_index=True,use_container_width=True)
            for d in documents:
                p=d['property']
                with st.expander(f"{p['code']} · {float(p['total']):,.2f} €"):
                    st.download_button('Λήψη ατομικού PDF',
                        get_property_pdf(building_id,statement['id'],d['apartment_id'])['pdf'],
                        file_name=f"koinoxrista_{statement['period_key']}_v{statement['revision']}_{p['code']}.pdf",
                        mime='application/pdf',key=f"history_property_{statement['id']}_{d['apartment_id']}")
        with tab_email:
            statement_delivery_ui.render(building_id,statement)
    except (StatementError,DatabaseError,ValueError) as exc:
        st.error(str(exc))
