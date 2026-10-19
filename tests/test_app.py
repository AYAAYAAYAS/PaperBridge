import hashlib
import json
import os
import tempfile
import threading
import unittest
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import Mock, patch

import pymupdf
from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtWidgets import QApplication, QListWidgetItem

from paperbridge import core, network
from paperbridge.references import Reference, extract_references, resolve_reference
from paperbridge.ui import STYLE, MainWindow
from paperbridge.worker import (
    check_service,
    is_deepseek,
    missing_translation_excerpts,
    usable_translation,
)


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
    def test_https_missing_certificate_path_retries_without_system_proxy(self):
        response = object()
        opener = Mock()
        opener.open.return_value = response
        missing_certificate = urllib.error.URLError(FileNotFoundError(2, "missing certificate"))
        with patch("paperbridge.network.urllib.request.urlopen", side_effect=missing_certificate), \
                patch("paperbridge.network.urllib.request.build_opener", return_value=opener):
            self.assertIs(network.urlopen("https://example.test", timeout=7), response)
        opener.open.assert_called_once_with("https://example.test", timeout=7)

    def test_nested_packaged_app_finds_project_environment(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "requirements.txt").write_text("", encoding="utf-8")
            (root / "paperbridge").mkdir()
            (root / "paperbridge" / "worker.py").write_text("", encoding="utf-8")
            executable = root / "dist" / "updated" / "PaperBridge" / "PaperBridge.exe"
            executable.parent.mkdir(parents=True)
            executable.touch()
            python = root / ".venv" / "Scripts" / "python.exe"
            python.parent.mkdir(parents=True)
            python.touch()
            self.assertEqual(core.project_root_for_executable(executable), root.resolve())
            with patch.object(core, "ROOT", root):
                self.assertEqual(core.worker_command()[0], str(python))

    def test_legacy_settings_and_history_are_merged(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            data = root / "userdata"
            legacy = root / "dist" / "userdata"
            data.mkdir(parents=True)
            legacy.mkdir(parents=True)
            (data / "settings.json").write_text('{"model":"old"}', encoding="utf-8")
            (legacy / "settings.json").write_text('{"model":"new","protected_key":"encrypted"}', encoding="utf-8")
            os.utime(data / "settings.json", (10, 10))
            os.utime(legacy / "settings.json", (20, 20))
            (data / "history.json").write_text(json.dumps([{"source": "previous.pdf"}]), encoding="utf-8")
            (legacy / "history.json").write_text(json.dumps([{"source": "latest.pdf"}]), encoding="utf-8")
            os.utime(data / "history.json", (10, 10))
            os.utime(legacy / "history.json", (20, 20))
            core.migrate_legacy_data(root, data)
            self.assertEqual(json.loads((data / "settings.json").read_text())["model"], "new")
            self.assertEqual([entry["source"] for entry in json.loads((data / "history.json").read_text())],
                             ["latest.pdf", "previous.pdf"])

    def test_reference_sections_ignore_body_and_preprint_footer(self):
        doc = pymupdf.open()
        doc.new_page().insert_text((50, 50), "Body text https://doi.org/10.1234/not-a-reference")
        page = doc.new_page()
        page.insert_text((50, 50), "Acknowledgments\nSupport statement\nReference\n"
                         "Smith, A., Lee, B., 2024. A Study of Bearings.\n"
                         "Journal of Testing. https://doi.org/10.1234/bearings\n"
                         "This preprint research paper has not been peer reviewed.\n"
                         "Jones, C., 2023. Another Scientific Study.\n"
                         "Journal of Results. https://doi.org/10.5678/results")
        references = extract_references(doc)
        self.assertEqual(len(references), 2)
        self.assertTrue(references[0].citation.startswith("Smith,"))
        self.assertTrue(references[0].direct_url.endswith("10.1234/bearings"))
        self.assertTrue(references[1].citation.startswith("Jones,"))
        doc.close()

    def test_reference_lookup_rejects_unrelated_crossref_match(self):
        reference = Reference("Smith A. A Study of Bearing Fault Diagnosis. 2024.", 2)
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return json.dumps({"message": {"items": [
                    {"title": ["Unrelated Paper on Weather Prediction"], "DOI": "10.1/wrong"},
                    {"title": ["A Study of Bearing Fault Diagnosis"], "DOI": "10.1234/correct"},
                ]}}).encode()

        with patch("paperbridge.references.urlopen", return_value=Response()):
            self.assertEqual(resolve_reference(reference), "https://doi.org/10.1234/correct")

    def test_reference_lookup_prefers_public_full_text_page(self):
        reference = Reference("Example journal citation", 1, doi="10.1234/example")
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return json.dumps({"best_oa_location": {
                    "landing_page_url": "https://repository.example.org/paper/123",
                    "pdf_url": "https://repository.example.org/paper/123.pdf",
                }}).encode()

        with patch("paperbridge.references.urlopen", return_value=Response()):
            self.assertEqual(resolve_reference(reference), "https://repository.example.org/paper/123")

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

    def test_deepseek_output_validation_and_blank_tracking(self):
        self.assertTrue(is_deepseek("https://api.deepseek.com"))
        self.assertFalse(is_deepseek("https://example.test/v1"))
        self.assertTrue(usable_translation('[{"id":0,"output":"译文"}]', True))
        self.assertTrue(usable_translation('```json\n{"id":0,"output":"译文"}\n```', True))
        self.assertFalse(usable_translation("", True))
        self.assertFalse(usable_translation("not json", True))
        with tempfile.TemporaryDirectory() as temp:
            tracking = Path(temp) / "paper" / "translate_tracking.json"
            tracking.parent.mkdir()
            tracking.write_text(json.dumps({"page": [{"paragraph": [
                {"input": "translated", "output": "已翻译", "llm_translate_trackers": []},
                {"input": "missing paragraph", "output": "", "llm_translate_trackers": []},
            ]}]}), encoding="utf-8")
            self.assertEqual(missing_translation_excerpts(Path(temp)), ["missing paragraph"])

    def test_deepseek_connection_disables_thinking(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), ApiHandler)
        server.response_code = 200
        server.response_payload = None
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        job = {"settings": {
            "base_url": f"http://127.0.0.1:{server.server_port}/v1",
            "model": "deepseek-flash",
        }, "key": ""}
        try:
            with patch("paperbridge.worker.is_local_ollama", return_value=False), \
                    patch("paperbridge.worker.is_deepseek", return_value=True):
                check_service(job)
            self.assertEqual(server.received["thinking"], {"type": "disabled"})
            self.assertEqual(server.received["temperature"], 0)
            self.assertEqual(server.received["max_tokens"], 16)
        finally:
            server.shutdown()
            server.server_close()

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

    def test_single_click_reference_opens_public_paper_page(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            with patch("paperbridge.ui.DATA", folder / "data"), \
                    patch("paperbridge.core.DATA", folder / "data"), \
                    patch("paperbridge.ui.QDesktopServices.openUrl") as open_url:
                window = MainWindow()
                item = QListWidgetItem("[1] arXiv:2401.12345")
                item.setData(Qt.ItemDataRole.UserRole, Reference("[1] Example. arXiv:2401.12345", 1,
                                            arxiv="2401.12345"))
                window.references_list.addItem(item)
                window.references_list.itemClicked.emit(item)
                self.assertEqual(open_url.call_args.args[0].toString(),
                                 "https://arxiv.org/abs/2401.12345")
                window.close()
                window.deleteLater()


if __name__ == "__main__":
    unittest.main()
