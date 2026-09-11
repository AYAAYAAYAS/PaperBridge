import hashlib
import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import pymupdf
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication

from paperbridge import core
from paperbridge.ui import STYLE, MainWindow
from paperbridge.worker import check_service


class ApiHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        self.server.received = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.send_response(self.server.response_code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        if self.server.response_payload:
            payload = self.server.response_payload
        elif self.path == "/api/chat":
            payload = {"message": {"role": "assistant", "content": "OK"}, "done": True}
        else:
            payload = {"choices": [{"message": {"content": "OK"}}]}
        self.wfile.write(json.dumps(payload).encode())

    def log_message(self, *args):
        pass


class CoreTests(unittest.TestCase):
    def test_page_range_validation(self):
        self.assertEqual(core.parse_pages("1-3，5,2", 8), [1, 2, 3, 5])
        self.assertEqual(core.parse_pages("", 2), [1, 2])
        for value in ("0", "9", "3-2", "1-", "x", "1,,2"):
            with self.assertRaises(ValueError):
                core.parse_pages(value, 8)

    def test_service_validation(self):
        self.assertEqual(core.validate_service({"base_url": "http://localhost:11434/v1/chat/completions/", "model": "local"}, ""),
                         ("http://localhost:11434/v1", "local", "local"))
        with self.assertRaises(ValueError):
            core.validate_service({"base_url": "https://example.test/v1", "model": "test"}, "")
        with self.assertRaises(ValueError):
            core.validate_service({"base_url": "https://key@example.test/v1", "model": "test"}, "key")

    @unittest.skipUnless(os.name == "nt", "Windows DPAPI test")
    def test_encrypted_key_roundtrip_and_forget(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(core, "DATA", Path(temp)):
            settings = dict(core.DEFAULTS, remember_key=True)
            core.write_settings(settings, "test-secret-not-real")
            self.assertNotIn("test-secret-not-real", (Path(temp) / "settings.json").read_text())
            self.assertEqual(core.read_settings()[1], "test-secret-not-real")
            settings["remember_key"] = False
            core.write_settings(settings, "test-secret-not-real")
            self.assertEqual(core.read_settings()[1], "")
            self.assertNotIn("protected_key", (Path(temp) / "settings.json").read_text())

    def test_service_success_and_auth_failure(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), ApiHandler)
        server.response_code = 200
        server.response_payload = None
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        job = {"settings": {"base_url": f"http://127.0.0.1:{server.server_port}/v1", "model": "test"}, "key": ""}
        try:
            check_service(job)
            self.assertEqual(server.received["model"], "test")
            self.assertIs(server.received["think"], False)
            server.response_code = 401
            with self.assertRaisesRegex(ValueError, "Ollama 连接失败"):
                check_service(job)
            server.response_code = 400
            server.response_payload = {"error": {"message": '"broken:4b" does not support chat'}}
            with self.assertRaisesRegex(ValueError, "不能对话"):
                check_service(job)
        finally:
            server.shutdown()
            server.server_close()


class ReaderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setStyleSheet(STYLE)

    def test_load_sync_partial_badge_and_history(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            pdf = folder / "测试文献.pdf"
            doc = pymupdf.open()
            for page in range(3):
                doc.new_page().insert_text((70, 70), f"Scientific document page {page + 1}")
            doc.save(pdf)
            doc.close()
            before = hashlib.sha256(pdf.read_bytes()).hexdigest()
            with patch("paperbridge.ui.DATA", folder / "data"):
                window = MainWindow()
                window.show()
                window.open_pdf(pdf)
                for _ in range(5):
                    self.app.processEvents()
                self.assertEqual(window.page_count, 3)
                window.load_result({"mono": str(pdf), "dual": "", "glossary": ""}, [1])
                window.page.setValue(2)
                self.app.processEvents()
                self.assertEqual(window.left.view.pageNavigator().currentPage(), 1)
                self.assertEqual(window.right.view.pageNavigator().currentPage(), 1)
                self.assertIn("未翻译", window.right.badge.text())
                window.zoom.setCurrentText("150%")
                self.app.processEvents()
                self.assertEqual(window.left.view.zoomFactor(), 1.5)
                self.assertEqual(window.right.view.zoomFactor(), 1.5)
                window.left.view.verticalScrollBar().setValue(100)
                self.app.processEvents()
                self.assertEqual(window.left.view.verticalScrollBar().value(), window.right.view.verticalScrollBar().value())
                window.remember_document()
                window.close()
                window.deleteLater()
                second = MainWindow()
                self.assertEqual(second.history[0]["pages"], [1])
                second.close()
                second.deleteLater()
                QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
                self.app.processEvents()
            self.assertEqual(before, hashlib.sha256(pdf.read_bytes()).hexdigest())


if __name__ == "__main__":
    unittest.main()
