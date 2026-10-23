import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bootstrap"))
from display import StartupDisplay

MESSAGES = json.loads((Path(__file__).resolve().parents[1] / "config/messages.ru.json").read_text(encoding="utf-8"))


class DisplayTests(unittest.TestCase):
    def test_redirected_output_has_durations_and_no_control_codes(self):
        output = io.StringIO()
        current = [100.0]
        display = StartupDisplay(MESSAGES, stream=output, clock=lambda: current[0])
        display.begin("Проверка связи")
        current[0] += 1.25
        self.assertEqual(display.end(), 1.25)
        with patch.dict("os.environ", {"SAKUYA_LAUNCH_STARTED_AT": "invalid"}):
            display.summary("http://127.0.0.1:43210", "quiet")
        text = output.getvalue()
        self.assertIn("✓ Проверка связи · 1.25 с", text)
        self.assertIn("запуск за 1.25 с", text)
        self.assertIn("Режим: тихий", text)
        self.assertNotIn("\x1b", text)
        self.assertNotIn("\r", text)

    @unittest.skipIf(sys.platform == "win32", "Uses a simulated ANSI terminal, not a Windows console handle")
    def test_interactive_stage_replaces_its_line_and_recovers_after_notice(self):
        class Terminal(io.StringIO):
            def isatty(self):
                return True
        output = Terminal()
        with patch.dict("os.environ", {"TERM": "xterm"}):
            display = StartupDisplay(MESSAGES, stream=output)
        display.begin("Трей")
        self.assertFalse(output.getvalue().endswith("\n"))
        display.notice("⚠ Предупреждение")
        display.end("warning")
        text = output.getvalue()
        self.assertIn("\r\x1b[2K", text)
        self.assertIn("\x1b[93m", text)
        self.assertIn("⚠ Трей ·", text)
        self.assertTrue(text.endswith("\n"))

    def test_error_contains_action_and_blocks_escape_injection(self):
        output = io.StringIO()
        display = StartupDisplay(MESSAGES, stream=output)
        display.begin("Настройки")
        display.error(ValueError("ошибка\x1b[2J\rскрытый текст"), "Исправьте settings.json", "C:/Данные/logs/sakuya.log")
        text = output.getvalue()
        self.assertIn("✕ Настройки ·", text)
        self.assertIn("Что сделать: Исправьте settings.json", text)
        self.assertIn("Журнал: C:/Данные/logs/sakuya.log", text)
        self.assertNotIn("\x1b", text)
        self.assertNotIn("\r", text)


if __name__ == "__main__":
    unittest.main()
