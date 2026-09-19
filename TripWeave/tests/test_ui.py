import unittest
from unittest.mock import patch
from pathlib import Path

from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).resolve().parents[1] / "app.py")


class UITests(unittest.TestCase):
    def test_unconfigured_llm_keeps_page_and_example_usable(self):
        with patch.dict('os.environ', {'TRIPWEAVE_API_KEY': '', 'TRIPWEAVE_MODEL': '', 'TRIPWEAVE_STACK': '0', 'TRIPWEAVE_MODEL_MODE': 'rules'}):
            app = AppTest.from_file(APP).run(timeout=15)
            app.sidebar.selectbox[0].select('LLM 多 Agent 协作').run()
            self.assertFalse(app.exception)
            self.assertTrue(app.chat_input)
            self.assertTrue(any('未调用 LLM' in w.value for w in app.warning))
            next(b for b in app.button if b.label == '了解数据来源').click().run()
            self.assertFalse(app.exception)
            self.assertEqual(len(app.chat_message), 2)
            self.assertIn('样例数据库', app.chat_message[-1].markdown[0].value)

    def test_model_configured_but_agents_not_restarted_is_not_live(self):
        with patch.dict('os.environ', {'TRIPWEAVE_API_KEY': 'test-placeholder', 'TRIPWEAVE_MODEL': 'test', 'TRIPWEAVE_MODEL_MODE': 'rules', 'TRIPWEAVE_STACK': '0'}):
            app = AppTest.from_file(APP).run(timeout=15)
            app.sidebar.selectbox[0].select('LLM 多 Agent 协作').run()
            self.assertFalse(app.exception)
            self.assertTrue(app.warning)
            self.assertEqual(app.session_state['engine_mode'], '离线演示')

    def test_knowledge_question_shows_evidence(self):
        app = AppTest.from_file(APP).run(timeout=15)
        self.assertEqual(len(app.exception), 0)
        app.chat_input[0].set_value("项目票务数据是真的吗").run()
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(len(app.chat_message), 2)
        self.assertTrue(any("project-scope" in item.label for item in app.expander))

    def test_confirmation_and_clear(self):
        app = AppTest.from_file(APP).run(timeout=15)
        app.chat_input[0].set_value("帮我模拟预订一张火车票").run()
        button = next(b for b in app.button if b.label == "确认模拟操作")
        button.click().run()
        self.assertEqual(len(app.exception), 0)
        self.assertFalse(any(b.label == "确认模拟操作" for b in app.button))
        self.assertTrue(any("模拟工具已调用" in m.value for m in app.markdown))
        next(b for b in app.button if b.label == "清空当前会话").click().run()
        self.assertEqual(len(app.chat_message), 0)

    def test_mixed_task_shows_both_results(self):
        app = AppTest.from_file(APP).run(timeout=15)
        app.chat_input[0].set_value("北京明天天气如何，项目票务数据是真的吗").run()
        self.assertEqual(len(app.exception), 0)
        answer = app.chat_message[-1].markdown[0].value
        self.assertIn("weather", answer)
        self.assertIn("knowledge", answer)


if __name__ == "__main__":
    unittest.main()
