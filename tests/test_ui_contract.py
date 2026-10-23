"""Static UI contract checks for navigation, chat and icon accessibility."""
from html.parser import HTMLParser
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]


class Markup(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = []
        self.uses = []
        self.labels = []
        self.controls = []
    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if "id" in values:
            self.ids.append(values["id"])
        if tag == "use" and values.get("href", "").startswith("#"):
            self.uses.append(values["href"][1:])
        if tag == "label" and values.get("for"):
            self.labels.append(values["for"])
        if tag in ("input", "select", "textarea") and values.get("id"):
            self.controls.append(values["id"])


class UiContractTests(unittest.TestCase):
    def test_ids_icons_and_labels(self):
        parser = Markup()
        parser.feed((ROOT / "app/index.html").read_text(encoding="utf-8"))
        self.assertEqual(len(parser.ids),len(set(parser.ids)),"Duplicate HTML id")
        self.assertFalse(set(parser.uses)-set(parser.ids),"Missing referenced SVG symbol")
        self.assertFalse(set(parser.labels)-set(parser.ids),"Label missing target")
        self.assertTrue({"chat-input","cloudru-key","cloudru-model"}.issubset(set(parser.labels)))

    def test_all_navigation_targets_exist(self):
        html = (ROOT / "app/index.html").read_text(encoding="utf-8")
        for page in ("home","settings","chat","tools","updates"):
            with self.subTest(page=page):
                self.assertIn('id="nav-'+page+'"',html)
                self.assertIn('id="page-'+page+'"',html)

    def test_essential_responsive_chat_rules(self):
        css=(ROOT / "app/app.css").read_text(encoding="utf-8")
        script=(ROOT / "app/app.js").read_text(encoding="utf-8")
        self.assertIn("@media(max-width:650px)",css)
        self.assertIn("overflow-wrap:anywhere",css)
        self.assertIn("container.scrollTop = container.scrollHeight",script)
        self.assertNotIn("item.scrollIntoView(",script)


if __name__=="__main__":
    unittest.main()
