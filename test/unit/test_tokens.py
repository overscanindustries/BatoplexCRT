import importlib
import os
import pkgutil
import re
import sys
import unittest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(__file__))

from batoplex import tokens  # noqa: E402

PKG = os.path.dirname(tokens.__file__)


def sources():
    for root, _d, files in os.walk(PKG):
        for f in files:
            if f.endswith(".py"):
                p = os.path.join(root, f)
                with open(p, encoding="utf-8") as fh:
                    yield p, fh.read()


class TestTokens(unittest.TestCase):
    def test_vertical_tokens_on_the_4_px_grid(self):
        for name in ("CTRL_H", "CHIP_H", "ROW_H", "ROW_PITCH", "ROW_COMPACT_H",
                     "ROW_COMPACT_PITCH", "ROW_TWO_H", "ROW_TWO_PITCH", "HEAD_H", "INSET",
                     "PANEL_PAD", "DIALOG_PAD", "Q_Y", "MIN_STEP_Y"):
            self.assertEqual(getattr(tokens, name) % 4, 0, name)
        for v in tokens.SPACE:
            self.assertEqual(v % 4, 0)

    def test_type_sizes_and_line_boxes_are_even(self):
        for name, (face, size, line, _up) in tokens.TYPE.items():
            self.assertIn(face, tokens.FONT_FILES, name)
            self.assertEqual(size % 2, 0, name)
            self.assertEqual(line % 2, 0, name)
            self.assertGreaterEqual(size, 16, name)

    def test_every_referenced_token_exists(self):
        """A token is removed only after its last caller. Every tokens.X and every
        name imported from tokens must exist."""
        missing = set()
        for path, src in sources():
            for name in re.findall(r"\btokens\.([A-Z_][A-Z0-9_]*)\b", src):
                if not hasattr(tokens, name):
                    missing.add((os.path.basename(path), name))
            for group in re.findall(r"^\s*from \.+tokens import \(?([^)\n]+)", src, re.M):
                for name in (n.strip() for n in group.split(",")):
                    if name and not hasattr(tokens, name.split(" as ")[0]):
                        missing.add((os.path.basename(path), name))
        self.assertEqual(missing, set())

    def test_every_referenced_colour_role_exists(self):
        missing = set()
        for path, src in sources():
            for role in re.findall(r'\bC\["([a-z_0-9]+)"\]', src):
                if role not in tokens.C:
                    missing.add((os.path.basename(path), role))
        self.assertEqual(missing, set())

    def test_every_module_imports(self):
        """Import smoke check after each landing."""
        for info in pkgutil.walk_packages([PKG], "batoplex."):
            if info.name.endswith("__main__"):
                continue
            importlib.import_module(info.name)

    def test_no_accent_text(self):
        """The accent is never a text colour."""
        bad = []
        for path, src in sources():
            for m in re.finditer(r'\b(?:draw\.text|rec\.text|text)\(([^()]|\([^()]*\))*\)', src):
                call = m.group(0)
                args = call.split(",")
                if len(args) > 3 and 'C["accent' in args[3]:
                    bad.append((os.path.basename(path), call[:60]))
        self.assertEqual(bad, [])


if __name__ == "__main__":
    unittest.main()
