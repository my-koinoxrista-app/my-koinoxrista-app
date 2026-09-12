import unittest
from streamlit.testing.v1 import AppTest

APP = '''
from unittest.mock import patch
from types import SimpleNamespace
import streamlit as st
import KoinoxristaAPP as app
st.session_state.setdefault('page', 'Πολυκατοικία')
st.session_state.setdefault('building_id', 'fixture')
st.session_state.setdefault('config', {})
st.session_state.setdefault('_ui_building_choice', 'fixture')
st.session_state.setdefault('selected_month', (2026, 9))
st.session_state.setdefault('period_scope', ('fixture', '2026-09'))
st.session_state.setdefault('expenses', [{'id': 'draft', 'amount': '12.50'}])
with patch.object(app.store, 'initialize'), patch.object(app, 'list_buildings', return_value=[{'id':'fixture', 'name':'Fixture'}]), patch.object(app, 'load_building', return_value=SimpleNamespace(name='Fixture', address='Test')):
    with patch.object(app.configuration_forms, 'render', side_effect=lambda *a: st.text('CONFIG_RENDERED')), patch.object(app.statement_history_ui, 'render', side_effect=lambda *a: st.text('HISTORY_RENDERED')):
        app.render()
'''


class LazyTabTests(unittest.TestCase):
    def test_hidden_history_not_loaded_and_drafts_survive_navigation(self):
        at = AppTest.from_string(APP, default_timeout=15).run()
        self.assertEqual(len(at.exception), 0)
        self.assertIn('CONFIG_RENDERED', [x.value for x in at.text])
        self.assertNotIn('HISTORY_RENDERED', [x.value for x in at.text])
        at.session_state['building_tabs_fixture'] = 'Ιστορικό'
        at.run()
        self.assertEqual(len(at.exception), 0)
        self.assertIn('HISTORY_RENDERED', [x.value for x in at.text])
        self.assertNotIn('CONFIG_RENDERED', [x.value for x in at.text])
        self.assertEqual(at.session_state['expenses'][0]['amount'], '12.50')
        self.assertEqual(at.session_state['selected_month'], (2026, 9))
        at.session_state['building_tabs_fixture'] = 'Στοιχεία & χιλιοστά'
        at.run()
        self.assertEqual(len(at.exception), 0)
        self.assertNotIn('HISTORY_RENDERED', [x.value for x in at.text])


if __name__ == '__main__': unittest.main()
