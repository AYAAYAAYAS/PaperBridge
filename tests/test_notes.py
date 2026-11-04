import hashlib
import tempfile
import unittest
from pathlib import Path

import pymupdf
from PySide6.QtWidgets import QApplication

from paperbridge.notes import NoteEditor, NoteStore


class NoteTests(unittest.TestCase):
    def test_notes_persist_undo_export_and_leave_original_intact(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "paper.pdf"
            with pymupdf.open() as doc:
                doc.new_page()
                doc.new_page()
                doc.save(source)
            before = hashlib.sha256(source.read_bytes()).hexdigest()
            store = NoteStore(root / "notes", source)
            for kind, points in [("ink", [[20, 20], [70, 70]]),
                                 ("highlight", [[30, 80], [100, 100]]),
                                 ("comment", [[120, 120]])]:
                store.add(1, {"kind": kind, "points": points, "color": "#ff0000", "text": "中文批注"})
            store = NoteStore(root / "notes", source)
            self.assertEqual(len(store.items(1)), 3)
            target = root / "annotated.pdf"
            store.export(target)
            with pymupdf.open(target) as doc:
                self.assertEqual(len(list(doc[0].annots() or [])), 0)
                notes = list(doc[1].annots())
                self.assertEqual(len(notes), 3)
                self.assertIn("中文批注", [x.info.get("content") for x in notes])
            store.undo(1)
            self.assertEqual(len(NoteStore(root / "notes", source).items(1)), 2)
            self.assertEqual(before, hashlib.sha256(source.read_bytes()).hexdigest())
            with self.assertRaises(ValueError):
                store.export(source)

    def test_editor_changes_page_and_renders_saved_coordinates(self):
        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "paper.pdf"
            with pymupdf.open() as doc:
                doc.new_page()
                doc.new_page()
                doc.save(source)
            editor = NoteEditor()
            editor.load(source, root / "notes")
            editor.store.add(1, {"kind": "ink", "points": [[20, 20], [60, 60]], "color": "#008800"})
            editor.set_page(1)
            editor.show()
            app.processEvents()
            self.assertEqual(editor.page, 1)
            self.assertFalse(editor.canvas.image.isNull())
            self.assertIn("1", editor.count.text())
            editor.close()
