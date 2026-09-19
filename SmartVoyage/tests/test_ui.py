import unittest
from pathlib import Path

from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).resolve().parents[1] / "app.py")


class UITests(unittest.TestCase):
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
