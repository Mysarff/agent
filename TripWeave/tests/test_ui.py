import unittest
from pathlib import Path
from unittest.mock import patch
from streamlit.testing.v1 import AppTest
APP = str(Path(__file__).resolve().parents[1] / 'app.py')


class UITests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict('os.environ', {'TRIPWEAVE_MODEL_MODE':'rules', 'TRIPWEAVE_STACK':'0', 'TRIPWEAVE_ACCESS_PASSWORD':''})
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_unconfigured_llm_keeps_page_usable(self):
        with patch.dict('os.environ', {'TRIPWEAVE_API_KEY':'', 'TRIPWEAVE_MODEL':''}):
            app=AppTest.from_file(APP).run(timeout=20)
            app.sidebar.selectbox[0].select('LLM 多 Agent 协作').run()
            self.assertFalse(app.exception)
            self.assertTrue(app.warning)
            self.assertTrue(app.chat_input)

    def test_no_rag_and_complete_multiturn_booking_then_clear(self):
        app=AppTest.from_file(APP).run(timeout=20)
        self.assertNotIn('knowledge', app.session_state['engine'].registry.items)
        for q in ('北京2026-10-01的天气','查同一天去上海的火车票','订第二个'):
            app.chat_input[0].set_value(q).run(timeout=20)
            self.assertFalse(app.exception)
        self.assertEqual(len(app.chat_message),6)
        self.assertFalse(any(b.label=='确认模拟操作' for b in app.button))
        app.chat_input[0].set_value('1张').run(timeout=20)
        self.assertIn('DEMO-TRAIN-002',app.session_state['last_run'].render())
        next(b for b in app.button if b.label=='确认模拟操作').click().run(timeout=20)
        self.assertFalse(app.exception)
        self.assertIn('模拟订单',app.chat_message[-1].markdown[0].value)
        next(b for b in app.button if b.label=='清空当前会话').click().run()
        self.assertEqual(app.session_state['engine'].session.slots,{})
        self.assertEqual(len(app.chat_message),0)

    def test_new_message_replaces_confirmation(self):
        app=AppTest.from_file(APP).run(timeout=20)
        app.chat_input[0].set_value('预订 DEMO-TRAIN-001，1张').run()
        old=app.session_state['last_run'].pending_token
        app.chat_input[0].set_value('改成2张').run()
        self.assertNotEqual(old,app.session_state['last_run'].pending_token)
        self.assertNotIn(old,app.session_state['engine'].pending)
        self.assertEqual(app.session_state['last_run'].results[-1].data['quote']['quantity'],2)
