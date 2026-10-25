import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pymupdf
from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication

from paperbridge.library import PaperLibrary
from paperbridge.library_ui import LibraryDialog
from paperbridge.ui import MainWindow


class LibraryTests(unittest.TestCase):
    def test_categories_copy_collision_and_persistence(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            library = PaperLibrary(root / "文献库")
            for name in ("../escape", "CON", "bad/name", ""):
                with self.assertRaises(ValueError):
                    library.add_category(name)
            category = library.add_category("故障诊断")
            self.assertTrue(category.is_dir())
            first = root / "a" / "paper.pdf"
            second = root / "b" / "paper.pdf"
            first.parent.mkdir()
            second.parent.mkdir()
            first.write_bytes(b"original one")
            second.write_bytes(b"original two")
            for file in (first, second):
                item = library.register(file)
                library.update(dict(item), "Example Journal", "JCR 2025 Q1", "故障诊断")
            items = list(library.data["papers"].values())
            self.assertNotEqual(items[0]["managed"], items[1]["managed"])
            self.assertEqual(Path(items[0]["managed"]).read_bytes(), first.read_bytes())
            self.assertEqual(Path(items[1]["managed"]).read_bytes(), second.read_bytes())
            reopened = PaperLibrary(root / "文献库")
            self.assertEqual(reopened.register(items[0]["managed"])["journal"], "Example Journal")

    def test_wheel_at_bottom_turns_page_and_management_saves_metadata(self):
        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pdf = root / "sample.pdf"
            doc = pymupdf.open()
            for i in range(3):
                doc.new_page().insert_text((50, 50), f"Page {i}")
            doc.save(pdf)
            doc.close()
            with patch("paperbridge.ui.DATA", root / "userdata"):
                window = MainWindow()
                window.show()
                window.open_pdf(pdf)
                window.load_result({"mono": str(pdf), "dual": "", "glossary": ""}, [1, 2, 3])
                app.processEvents()
                window.library.add_category("测试")
                dialog = LibraryDialog(window.library)
                dialog.journal.setText("Test Journal")
                dialog.partition.setText("中科院 2025 2 区")
                dialog.category.setCurrentText("测试")
                dialog.save()
                window.refresh_paper_metadata()
                self.assertIn("Test Journal", window.journal_meta.text())
                self.assertIn("2 区", window.journal_meta.text())
                window.zoom.setCurrentText("200%")
                app.processEvents()
                scrollbar = window.left.view.verticalScrollBar()
                scrollbar.setValue(scrollbar.maximum())
                wheel = QWheelEvent(QPointF(20, 20), QPointF(20, 20), QPoint(), QPoint(0, -120),
                                    Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                                    Qt.ScrollPhase.NoScrollPhase, False)
                app.sendEvent(window.left.view.viewport(), wheel)
                app.processEvents()
                self.assertEqual(window.page.value(), 2)
                self.assertEqual(scrollbar.value(), scrollbar.minimum())
                self.assertEqual(window.right.view.pageNavigator().currentPage(), 1)
                window._last_scroll_turn = 0
                upward = QWheelEvent(QPointF(20, 20), QPointF(20, 20), QPoint(), QPoint(0, 120),
                                      Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                                      Qt.ScrollPhase.NoScrollPhase, False)
                app.sendEvent(window.left.view.viewport(), upward)
                app.processEvents()
                self.assertEqual(window.page.value(), 1)
                self.assertEqual(scrollbar.value(), scrollbar.maximum())
                dialog.close()
                window.close()
                window.deleteLater()
                dialog.deleteLater()
                QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
                app.processEvents()
