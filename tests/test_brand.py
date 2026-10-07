import io
import os
import unittest
from unittest.mock import patch
import terminal_ui


class BrandTests(unittest.TestCase):
    def test_logo_palette_in_tty(self):
        with patch('sys.stdout.isatty', return_value=True), patch.dict(os.environ, {'TERM': 'xterm-256color'}, clear=True):
            rendered = terminal_ui.brand()
        self.assertIn('\033[1;38;2;56;182;255mr', rendered)
        self.assertIn('\033[1;38;2;255;194;194me', rendered)
        self.assertIn('\033[1;38;2;255;205;26mf', rendered)
        self.assertIn('\033[1;38;2;115;190;0mi', rendered)
        self.assertIn('\033[1;38;2;226;169;241ml', rendered)
        self.assertIn('\033[1;38;2;255;87;87ml', rendered)

    def test_plain_brand_for_pipes_no_color_and_dumb_terminal(self):
        cases = [(False, {}), (True, {'NO_COLOR': '1'}), (True, {'TERM': 'dumb'})]
        for tty, environment in cases:
            with self.subTest(tty=tty, environment=environment), patch('sys.stdout.isatty', return_value=tty), patch.dict(os.environ, environment, clear=True):
                self.assertEqual(terminal_ui.brand(), 'refill')

    def test_heading_has_one_blank_line_before_first_section(self):
        with patch('sys.stdout', new_callable=io.StringIO) as output:
            terminal_ui.heading('monitor', 'Live account check · Read-only')
            terminal_ui.section('Service')
        self.assertEqual(output.getvalue(), 'refill / monitor\nLive account check · Read-only\n\nSERVICE\n')
