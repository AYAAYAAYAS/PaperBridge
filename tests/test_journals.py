import csv
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QApplication

from paperbridge.journal_ui import JournalController, ManualRankingDialog
from paperbridge.journals import HEADERS, JournalStore, parse_cas, parse_jcr
from paperbridge.library import PaperLibrary
from paperbridge.library_ui import LibraryDialog


class JournalTests(unittest.TestCase):
    def test_background_cache_matching_manual_correction_and_dialog_display(self):
        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            library = PaperLibrary(root)
            paper = library.register(root / "paper.pdf")
            paper.update(journal="Example Journal", issns=["1234-567X"])
            controller = JournalController(library)
            controller.store.config["year"] = 2025
            for scheme in ("CAS", "JCR"):
                controller.store.store_records([{
                    "journal": "Example Journal", "issns": ["1234-567X"], "scheme": scheme,
                    "year": 2025, "scope": "大类" if scheme == "CAS" else "JIF",
                    "category": "Mechanics", "zone": "2区" if scheme == "CAS" else "Q2",
                    "source": "Test fixture", "source_url": "", "fetched_at": "2026-10-21"}])
            spy = QSignalSpy(controller.updated)
            with patch("paperbridge.journals.pdf_doi", return_value=""):
                controller.submit([paper])
                deadline = time.monotonic() + 4
                while spy.count() == 0 and time.monotonic() < deadline:
                    app.processEvents()
                    time.sleep(0.01)
                controller.stop()
                self.assertGreater(spy.count(), 0)
            self.assertEqual(len(paper["rankings"]), 2)
            controller.stop()
            dialog = LibraryDialog(library, controller=controller)
            self.assertIn("Q2", dialog.ranking_details.toPlainText())
            manual = ManualRankingDialog(controller, paper)
            manual.scheme.setCurrentIndex(2)
            manual.subject.setCurrentText("Mechanics")
            manual.zone.setCurrentText("Q3")
            manual.save()
            self.assertIn("Q3", dialog.ranking_details.toPlainText())
            saved = PaperLibrary(root).register(root / "paper.pdf")
            self.assertEqual(next(r["zone"] for r in saved["rankings"] if r["scheme"] == "JCR"), "Q3")
            manual.deleteLater()
            dialog.close()
            dialog.deleteLater()
            controller.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            app.processEvents()

    def test_cas_small_category_is_not_clarivate_quartile(self):
        payload = {"Year": 2025, "Title": "Example Journal", "ISSN": "1234-567X",
                   "ZKY": [{"Name": "Engineering", "Section": 2}],
                   "JCR": [{"Name": "MECHANICS", "Section": 1}]}
        records = parse_cas(payload, "Example Journal", ["1234-567X"], 2025)
        self.assertEqual([r["scheme"] for r in records], ["CAS", "CAS"])
        self.assertEqual([r["scope"] for r in records], ["大类", "小类"])
        self.assertEqual(records[1]["zone"], "1区")
        with self.assertRaises(ValueError):
            parse_cas(payload, "Example Journal", ["1234-567X"], 2024)

    def test_jcr_keeps_all_jif_subjects_and_ignores_jci(self):
        payload = {"year": 2025, "ranks": {
            "jif": [{"category": "A", "quartile": "Q1"}, {"category": "B", "quartile": "Q2"}],
            "jci": [{"category": "A", "quartile": "Q4"}]}}
        records = parse_jcr(payload, {"journal": "Example Journal", "issns": []}, 2025)
        self.assertEqual([r["zone"] for r in records], ["Q1", "Q2"])
        with self.assertRaises(ValueError):
            parse_jcr(payload, {"journal": "Example Journal", "issns": []}, 2024)

    def test_table_import_is_atomic_matches_issn_and_retains_years(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = JournalStore(root)
            path = root / "rankings.csv"
            def write(rows):
                with path.open("w", encoding="utf-8-sig", newline="") as file:
                    writer = csv.writer(file)
                    writer.writerow(HEADERS)
                    writer.writerows(rows)
            write([["Example Journal", "1234-567X", "中科院", "2025", "2区", "Engineering", "大类", "official export"],
                   ["Example Journal", "1234-567X", "JCR", "2025", "Q1", "MECHANICS", "JIF", "official export"],
                   ["Example Journal", "1234-567X", "JCR", "2024", "Q2", "MECHANICS", "JIF", "older export"]])
            self.assertEqual(store.import_table(path), 3)
            self.assertEqual(len(store.cached("different journal spelling", ["1234-567X"], 2025)), 2)
            self.assertEqual(store.cached("Example Journal", ["9876-5432"], 2025), [])
            self.assertEqual(store.cached("Example Journal", [], 2026), [])
            before = store.cache_file.read_bytes()
            write([["Example Journal", "1234-567X", "JCR", "2025", "Q4", "MECHANICS", "JIF", ""],
                   ["Example Journal", "1234-567X", "JCR", "wrong year", "Q2", "MECHANICS", "JIF", ""]])
            with self.assertRaises(ValueError):
                store.import_table(path)
            self.assertEqual(before, store.cache_file.read_bytes())
            reopened = JournalStore(root)
            self.assertEqual(reopened.cached("Example Journal", [], 2024)[0]["zone"], "Q2")

    def test_excel_round_trip(self):
        from openpyxl import load_workbook
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = JournalStore(root)
            path = root / "rankings.xlsx"
            store.export_template(path)
            book = load_workbook(path)
            book.active.append(["Journal", "1234-567X", "JCR", 2025, "Q3", "Subject", "JIF", "University export"])
            book.save(path)
            book.close()
            self.assertEqual(store.import_table(path), 1)
            self.assertEqual(store.cached("Journal", [], 2025)[0]["zone"], "Q3")

    def test_authorized_providers_and_doi_cached_without_credentials_on_disk(self):
        original_client = httpx.Client
        calls = []
        def handler(request):
            calls.append(str(request.url))
            if request.url.host == "api.crossref.org":
                data = {"message": {"type": "journal-article", "container-title": ["Example Journal"], "ISSN": ["1234-567X"]}}
            elif request.url.host == "webapi.fenqubiao.com":
                data = {"Year": 2025, "Title": "Example Journal", "ISSN": "1234-567X",
                        "ZKY": [{"Name": "Engineering", "Section": 2}], "JCR": [{"Name": "Mechanics", "Section": 1}]}
            elif request.url.path.endswith("/journals"):
                data = {"hits": [{"id": "EXAMPLE"}]}
            elif request.url.path.endswith("/EXAMPLE"):
                data = {"name": "Example Journal", "issn": "1234-567X"}
            else:
                data = {"year": 2025, "ranks": {"jif": [{"category": "Mechanics", "quartile": "Q3"}]}}
            return httpx.Response(200, json=data)
        with tempfile.TemporaryDirectory() as temp:
            store = JournalStore(Path(temp))
            store.config.update(year=2025, cas_user="private-user", cas_password="private-password", jcr_key="private-key")
            paper = {"source": "unneeded.pdf", "doi": "10.1234/example", "journal": ""}
            def client_factory(**kwargs):
                return original_client(transport=httpx.MockTransport(handler))
            with patch("paperbridge.journals.httpx.Client", side_effect=client_factory):
                result = store.lookup(paper)
                first_count = len(calls)
                again = store.lookup(paper)
            self.assertEqual(len(result["rankings"]), 3)
            self.assertEqual(result["metadata"]["journal"], "Example Journal")
            self.assertEqual(len(calls), first_count)
            self.assertEqual(len(again["rankings"]), 3)
            saved = store.cache_file.read_text(encoding="utf-8")
            for secret in ("private-user", "private-password", "private-key"):
                self.assertNotIn(secret, saved)

    def test_missing_authorization_keeps_rankings_unknown(self):
        with tempfile.TemporaryDirectory() as temp:
            store = JournalStore(Path(temp))
            with patch("paperbridge.journals.pdf_doi", return_value=""), \
                    patch("paperbridge.journals.httpx.Client") as client:
                result = store.lookup({"source": "not-needed.pdf", "journal": "Example Journal"})
            self.assertEqual(result["rankings"], [])
            self.assertIn("未配置授权", result["status"])
            client.assert_not_called()

    @unittest.skipUnless(os.name == "nt", "Windows encrypted credential store")
    def test_credentials_are_encrypted_only_when_requested(self):
        with tempfile.TemporaryDirectory() as temp:
            store = JournalStore(Path(temp))
            values = dict(store.config, remember=True, cas_user="user", cas_password="secret-password", jcr_key="secret-api-key")
            store.configure(values)
            text = store.config_file.read_text()
            self.assertNotIn("secret-password", text)
            self.assertNotIn("secret-api-key", text)
            self.assertEqual(JournalStore(Path(temp)).config["cas_password"], "secret-password")
            store.configure(dict(values, remember=False))
            self.assertNotIn("protected", json.loads(store.config_file.read_text()))
            self.assertEqual(JournalStore(Path(temp)).config["jcr_key"], "")
