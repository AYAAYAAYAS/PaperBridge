from __future__ import annotations

import json
import os
import shutil
from datetime import datetime
from pathlib import Path
from uuid import uuid4

import pymupdf
from PySide6.QtCore import QPointF, Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QDesktopServices, QFont, QKeySequence
from PySide6.QtPdf import QPdfDocument
from PySide6.QtPdfWidgets import QPdfView
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from paperbridge.core import DATA, atomic_json, parse_pages, read_settings, validate_service, write_settings
from paperbridge.process import EngineProcess

LANGUAGES = [("英语", "en"), ("简体中文", "zh-CN"), ("繁体中文", "zh-TW"),
             ("日语", "ja"), ("德语", "de"), ("法语", "fr"), ("韩语", "ko")]

STYLE = """
QWidget { font-family: 'Microsoft YaHei UI', 'Segoe UI'; font-size: 13px; color: #263a39; }
QMainWindow, QDialog { background: #f6f8f7; }
QFrame#header, QFrame#viewerHeader { background: #ffffff; border-bottom: 1px solid #e0e8e5; }
QFrame#sidebar { background: #f0f4f2; border-right: 1px solid #dde5e1; }
QLabel#brand { color: #0e6556; font-size: 23px; font-weight: 700; }
QLabel#muted { color: #73847e; font-size: 12px; }
QLabel#section { color: #76877e; font-size: 11px; font-weight: 600; }
QLabel#title { font-size: 25px; font-weight: 600; color: #193e35; }
QLabel#badge { background: #e8f2ed; color: #357b61; border-radius: 5px; padding: 4px 9px; font-size: 11px; }
QLabel#paperTitle { font-size: 15px; font-weight: 600; }
QPushButton { background: white; border: 1px solid #d8e2dc; border-radius: 7px; padding: 8px 14px; }
QPushButton:hover { background: #e9f3ed; border-color: #95b4a2; }
QPushButton:pressed { background: #d8e9de; }
QPushButton#primary { background: #146b56; color: white; border-color: #146b56; font-weight: 600; }
QPushButton#primary:hover { background: #0d5846; }
QPushButton:disabled { background: #e9eeeb; color: #a2afa7; border-color: #e1e8e3; }
QLineEdit, QSpinBox, QComboBox, QPlainTextEdit { background: #fff; border: 1px solid #d8e2dc; border-radius: 6px; padding: 7px; selection-background-color: #23745b; }
QLineEdit:focus, QPlainTextEdit:focus { border-color: #458d71; }
QComboBox::drop-down { border: none; width: 22px; }
QListWidget { background: transparent; border: none; outline: none; }
QListWidget::item { padding: 10px; margin: 2px 0; border-radius: 6px; }
QListWidget::item:selected { background: #dbece1; color: #145a42; }
QListWidget::item:hover { background: #e4eee7; }
QSplitter::handle { background: #dce5e0; width: 2px; }
QProgressBar { border: none; border-radius: 3px; background: #e4ece6; height: 6px; }
QProgressBar::chunk { background: #3b9070; border-radius: 3px; }
QScrollBar:vertical { background: #edf1ee; width: 9px; }
QScrollBar::handle:vertical { background: #b5c7bc; border-radius: 4px; min-height: 30px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar:horizontal { background: #edf1ee; height: 9px; }
QScrollBar::handle:horizontal { background: #b5c7bc; border-radius: 4px; min-width: 30px; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
QStatusBar { background: #fff; color: #6c7f73; border-top: 1px solid #e0e8e3; }
QToolTip { background: #fff; color: #263a39; padding: 6px; border: 1px solid #cbdcd1; }
"""


def label(text, name=None, wrap=False):
    item = QLabel(text)
    if name:
        item.setObjectName(name)
    item.setWordWrap(wrap)
    return item


def button(text, callback, primary=False):
    item = QPushButton(text)
    if primary:
        item.setObjectName("primary")
    item.clicked.connect(callback)
    item.setCursor(Qt.CursorShape.PointingHandCursor)
    return item


class SettingsDialog(QDialog):
    def __init__(self, settings, key, parent=None):
        super().__init__(parent)
        self.setWindowTitle("翻译设置 · 知译")
        self.resize(610, 640)
        self.settings = dict(settings)
        self.key = key
        self.runner = EngineProcess(self)
        self.runner.event.connect(self.on_event)
        self.runner.ended.connect(self.on_end)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(14)
        layout.addWidget(label("让译文符合你的研究语境", "title"))
        layout.addWidget(label("支持兼容聊天接口的云端模型，以及 Ollama 等本地服务。", "muted", True))
        form = QFormLayout()
        form.setSpacing(12)
        self.url = QLineEdit(settings["base_url"])
        self.url.setPlaceholderText("https://你的服务域名/v1")
        self.model = QLineEdit(settings["model"])
        self.model.setPlaceholderText("填写服务商提供的模型名称")
        self.secret = QLineEdit(key)
        self.secret.setEchoMode(QLineEdit.EchoMode.Password)
        self.secret.setPlaceholderText("API 密钥；本地服务可以留空")
        self.remember = QCheckBox("使用 Windows 加密保存密钥")
        self.remember.setChecked(settings["remember_key"])
        self.remember.setEnabled(os.name == "nt")
        self.domain = QLineEdit(settings["domain"])
        self.domain.setPlaceholderText("例如：射频电路；gain 统一译为“增益”")
        self.rate = QSpinBox()
        self.rate.setRange(1, 8)
        self.rate.setValue(settings["qps"])
        self.rate.setSuffix(" 次 / 秒")
        self.glossary = QLineEdit(settings["glossary"])
        self.glossary.setPlaceholderText("可选：source,target 两列的 CSV 文件")
        glossary_row = QWidget()
        gl = QHBoxLayout(glossary_row)
        gl.setContentsMargins(0, 0, 0, 0)
        gl.addWidget(self.glossary)
        gl.addWidget(button("选择", self.browse_glossary))
        self.auto_terms = QCheckBox("自动提取全文术语，保持译法一致")
        self.auto_terms.setChecked(settings["auto_glossary"])
        self.tables = QCheckBox("翻译表格文字（实验功能）")
        self.tables.setChecked(settings["translate_tables"])
        form.addRow("服务地址", self.url)
        form.addRow("模型名称", self.model)
        form.addRow("API 密钥", self.secret)
        form.addRow("", self.remember)
        form.addRow("研究领域", self.domain)
        form.addRow("术语表", glossary_row)
        form.addRow("请求速度", self.rate)
        form.addRow("", self.auto_terms)
        form.addRow("", self.tables)
        layout.addLayout(form)
        layout.addWidget(label("点击翻译后，正文与术语会发送至你配置的服务；原 PDF 留在本机。"
                               "使用云端服务会按服务商规则计费。", "muted", True))
        local = button("填入本地 Ollama 地址", self.use_local)
        layout.addWidget(local)
        self.message = label("内置学术意译规则：忠实含义、保留限定条件、统一术语。", "muted", True)
        layout.addWidget(self.message)
        layout.addStretch()
        row = QHBoxLayout()
        self.test = button("测试连接", self.test_connection)
        row.addWidget(self.test)
        row.addStretch()
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存设置")
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        self.buttons.accepted.connect(self.save)
        self.buttons.rejected.connect(self.reject)
        row.addWidget(self.buttons)
        layout.addLayout(row)

    def use_local(self):
        self.url.setText("http://localhost:11434/v1")
        self.model.setPlaceholderText("填写 Ollama 中已下载的模型名称")
        self.secret.clear()

    def browse_glossary(self):
        path, _ = QFileDialog.getOpenFileName(self, "导入术语表", "", "术语表 (*.csv)")
        if path:
            self.glossary.setText(path)

    def values(self):
        values = dict(self.settings)
        values.update(base_url=self.url.text().strip(), model=self.model.text().strip(),
                      domain=self.domain.text().strip(), glossary=self.glossary.text().strip(),
                      qps=self.rate.value(), remember_key=self.remember.isChecked(),
                      auto_glossary=self.auto_terms.isChecked(), translate_tables=self.tables.isChecked())
        return values, self.secret.text().strip()

    def test_connection(self):
        values, key = self.values()
        try:
            validate_service(values, key)
            self.runner.start({"mode": "check", "settings": values, "key": key})
        except (ValueError, OSError) as exc:
            self.message.setText(str(exc))
            return
        self.test.setEnabled(False)
        self.message.setText("正在发送一条简短测试请求…")

    def on_event(self, event):
        if event["type"] == "checked":
            self.message.setText("连接成功，模型可以正常返回文本。")
        elif event["type"] == "error":
            self.message.setText(event["message"])

    def on_end(self, code, cancelled):
        self.test.setEnabled(True)

    def save(self):
        values, key = self.values()
        if values["glossary"] and not Path(values["glossary"]).is_file():
            self.message.setText("术语表文件不存在，请重新选择。")
            return
        try:
            write_settings(values, key)
        except OSError as exc:
            self.message.setText(str(exc))
            return
        self.settings, self.key = values, key
        self.accept()

    def done(self, result):
        if self.runner.running:
            self.runner.cancel()
            self.runner.process.waitForFinished(2000)
        super().done(result)


class PdfPane(QWidget):
    def __init__(self, title, subtitle, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        header = QFrame()
        header.setObjectName("viewerHeader")
        bar = QHBoxLayout(header)
        bar.setContentsMargins(20, 14, 20, 14)
        bar.addWidget(label(title, "paperTitle"))
        bar.addStretch()
        self.badge = label(subtitle, "badge")
        bar.addWidget(self.badge)
        layout.addWidget(header)
        self.stack = QStackedWidget()
        self.empty = QWidget()
        empty_layout = QVBoxLayout(self.empty)
        empty_layout.setContentsMargins(48, 40, 48, 40)
        empty_layout.addStretch()
        self.empty_title = label("译文将在这里呈现", "title", True)
        self.empty_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.addWidget(self.empty_title)
        self.empty_text = label("完成翻译后，与左侧原文同步阅读。\n保留页面结构、图表与公式。", "muted", True)
        self.empty_text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.addWidget(self.empty_text)
        empty_layout.addStretch()
        self.stack.addWidget(self.empty)
        self.doc = QPdfDocument(self)
        self.view = QPdfView(self)
        self.view.setDocument(self.doc)
        self.view.setPageMode(QPdfView.PageMode.SinglePage)
        self.view.setZoomMode(QPdfView.ZoomMode.FitToWidth)
        self.stack.addWidget(self.view)
        layout.addWidget(self.stack)

    def load(self, path):
        self.doc.close()
        error = self.doc.load(str(path))
        if error != QPdfDocument.Error.None_:
            self.stack.setCurrentIndex(0)
            raise ValueError("Qt 无法打开这份 PDF，请检查文件是否损坏或加密。")
        self.stack.setCurrentIndex(1)

    def clear(self):
        self.doc.close()
        self.stack.setCurrentIndex(0)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("知译 · PaperBridge — 学术文献对照阅读")
        self.resize(1480, 960)
        self.setMinimumSize(1080, 720)
        self.setAcceptDrops(True)
        self.settings, self.key = read_settings()
        self.source = None
        self.result = None
        self.result_pages = []
        self.page_count = 0
        self.job = None
        self._pending_result = None
        self._error_text = ""
        self._syncing = False
        self.runner = EngineProcess(self)
        self.runner.event.connect(self.on_event)
        self.runner.log.connect(self.append_log)
        self.runner.ended.connect(self.on_ended)
        self.build_ui()
        self.load_history()
        self.statusBar().showMessage("就绪 · 导入一篇文献，开始对照阅读")
        for shortcut, callback in [("Ctrl+O", self.choose_pdf), ("Ctrl+,", self.open_settings),
                                   ("Alt+Right", lambda: self.turn_page(1)), ("Alt+Left", lambda: self.turn_page(-1))]:
            action = QAction(self)
            action.setShortcut(QKeySequence(shortcut))
            action.triggered.connect(callback)
            self.addAction(action)

    def build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        header = QFrame()
        header.setObjectName("header")
        row = QHBoxLayout(header)
        row.setContentsMargins(24, 17, 24, 17)
        row.setSpacing(16)
        row.addWidget(label("知译", "brand"))
        row.addWidget(label("PAPERBRIDGE  /  学术阅读工作台", "muted"))
        row.addStretch()
        self.import_btn = button("＋  导入 PDF", self.choose_pdf)
        row.addWidget(self.import_btn)
        self.settings_btn = button("翻译设置", self.open_settings)
        row.addWidget(self.settings_btn)
        self.export_btn = button("导出 PDF  ▾", self.export_menu)
        self.export_btn.setEnabled(False)
        row.addWidget(self.export_btn)
        layout.addWidget(header)
        body = QHBoxLayout()
        body.setSpacing(0)
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(250)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(20, 22, 20, 18)
        side.setSpacing(12)
        side.addWidget(label("当前文献", "section"))
        self.doc_title = label("尚未导入文献", "paperTitle", True)
        side.addWidget(self.doc_title)
        self.doc_meta = label("支持可选中文字的 PDF", "muted", True)
        side.addWidget(self.doc_meta)
        side.addSpacing(10)
        side.addWidget(label("文献目录", "section"))
        self.outline = QListWidget()
        self.outline.itemClicked.connect(lambda item: self.page.setValue(item.data(Qt.ItemDataRole.UserRole)))
        side.addWidget(self.outline, 3)
        side.addWidget(label("最近阅读", "section"))
        self.history_list = QListWidget()
        self.history_list.setMaximumHeight(165)
        self.history_list.itemClicked.connect(self.open_recent)
        side.addWidget(self.history_list, 1)
        side.addSpacing(10)
        side.addWidget(label("翻译偏好", "section"))
        languages = QHBoxLayout()
        self.source_lang = QComboBox()
        self.target_lang = QComboBox()
        for name, code in LANGUAGES:
            self.source_lang.addItem(name, code)
            self.target_lang.addItem(name, code)
        self.source_lang.setCurrentIndex(max(0, self.source_lang.findData(self.settings["source"])))
        self.target_lang.setCurrentIndex(max(0, self.target_lang.findData(self.settings["target"])))
        languages.addWidget(self.source_lang)
        languages.addWidget(label("→", "muted"))
        languages.addWidget(self.target_lang)
        side.addLayout(languages)
        self.scope = QComboBox()
        self.scope.addItems(["翻译全文", "只翻译当前页", "自定义页码"])
        self.scope.currentIndexChanged.connect(self.scope_changed)
        side.addWidget(self.scope)
        self.pages = QLineEdit()
        self.pages.setPlaceholderText("例如 1-5,8")
        self.pages.setVisible(False)
        side.addWidget(self.pages)
        self.translate_btn = button("开始学术翻译", self.start_translation, True)
        self.translate_btn.setEnabled(False)
        side.addWidget(self.translate_btn)
        self.cancel_btn = button("取消翻译", self.runner.cancel)
        self.cancel_btn.setVisible(False)
        side.addWidget(self.cancel_btn)
        side.addWidget(label("保留版式与公式 · 语境翻译\n建议先试译 1–2 页，核对术语。", "muted", True))
        body.addWidget(sidebar)
        content = QWidget()
        center = QVBoxLayout(content)
        center.setContentsMargins(0, 0, 0, 0)
        center.setSpacing(0)
        toolbar = QWidget()
        nav = QHBoxLayout(toolbar)
        nav.setContentsMargins(20, 12, 20, 12)
        self.filename = label("原文与译文，放在一起读。", "muted")
        nav.addWidget(self.filename, 1)
        nav.addWidget(button("‹", lambda: self.turn_page(-1)))
        self.page = QSpinBox()
        self.page.setRange(1, 1)
        self.page.setFixedWidth(76)
        self.page.valueChanged.connect(self.go_page)
        nav.addWidget(self.page)
        self.total_label = label("/ 0 页", "muted")
        nav.addWidget(self.total_label)
        nav.addWidget(button("›", lambda: self.turn_page(1)))
        self.zoom = QComboBox()
        self.zoom.addItems(["适合宽度", "整页", "75%", "100%", "125%", "150%", "200%"])
        self.zoom.currentIndexChanged.connect(self.change_zoom)
        nav.addWidget(self.zoom)
        self.sync = QCheckBox("同步滚动")
        self.sync.setChecked(True)
        nav.addWidget(self.sync)
        center.addWidget(toolbar)
        self.reader_stack = QStackedWidget()
        self.reader_stack.addWidget(self.welcome())
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.left = PdfPane("原文", "ORIGINAL")
        self.right = PdfPane("译文", "待翻译")
        self.splitter.addWidget(self.left)
        self.splitter.addWidget(self.right)
        self.splitter.setSizes([590, 590])
        self.splitter.setChildrenCollapsible(False)
        self.reader_stack.addWidget(self.splitter)
        center.addWidget(self.reader_stack, 1)
        for first, second in ((self.left, self.right), (self.right, self.left)):
            first.view.verticalScrollBar().valueChanged.connect(lambda value, a=first, b=second: self.sync_scroll(a, b, value))
            first.view.pageNavigator().currentPageChanged.connect(self.navigator_changed)
        bottom = QWidget()
        bottom_layout = QVBoxLayout(bottom)
        bottom_layout.setContentsMargins(20, 12, 20, 12)
        bottom_layout.setSpacing(8)
        info_row = QHBoxLayout()
        self.job_label = label("准备就绪", "muted")
        info_row.addWidget(self.job_label, 1)
        self.log_btn = button("处理记录", self.toggle_logs)
        info_row.addWidget(self.log_btn)
        bottom_layout.addLayout(info_row)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(5)
        bottom_layout.addWidget(self.progress)
        self.logs = QPlainTextEdit()
        self.logs.setReadOnly(True)
        self.logs.setMaximumBlockCount(800)
        self.logs.setMaximumHeight(140)
        self.logs.setVisible(False)
        bottom_layout.addWidget(self.logs)
        center.addWidget(bottom)
        body.addWidget(content, 1)
        layout.addLayout(body, 1)

    def welcome(self):
        widget = QWidget()
        outer = QVBoxLayout(widget)
        outer.setContentsMargins(70, 45, 70, 45)
        outer.addStretch(2)
        outer.addWidget(label("READ BEYOND LANGUAGE", "section"))
        outer.addSpacing(14)
        title = label("跨越语言，专注研究。", "title")
        title.setStyleSheet("font-size: 36px; font-weight: 600; color: #193e35;")
        outer.addWidget(title)
        outer.addSpacing(10)
        outer.addWidget(label("原文在左，译文在右。\n在熟悉的论文版面中，读懂每一个论点。", "muted", True))
        outer.addSpacing(30)
        start = button("＋  选择一篇 PDF 文献", self.choose_pdf, True)
        start.setFixedWidth(240)
        start.setMinimumHeight(48)
        outer.addWidget(start)
        outer.addWidget(label("也可以将 PDF 文件拖入窗口  ·  Ctrl + O", "muted"))
        outer.addSpacing(40)
        cards = QHBoxLayout()
        cards.setSpacing(18)
        for number, title, text in [
            ("01", "版面保留", "识别分栏、正文与图表，\n在原页面结构中呈现译文。"),
            ("02", "公式保护", "单独识别数学表达式，\n保留公式内容与符号。"),
            ("03", "学术语境", "结合段落与专业术语，\n让译文准确、自然。"),
        ]:
            frame = QFrame()
            frame.setStyleSheet("QFrame {background: white; border: 1px solid #e0e8e2; border-radius: 10px;}")
            box = QVBoxLayout(frame)
            box.setContentsMargins(20, 20, 20, 20)
            for text_item, name in ((number, "section"), (title, "paperTitle"), (text, "muted")):
                item = label(text_item, name, True)
                item.setStyleSheet("border: none;")
                box.addWidget(item)
            cards.addWidget(frame)
        outer.addLayout(cards)
        outer.addStretch(3)
        outer.addWidget(label("首次使用：在「翻译设置」填写服务地址、模型和密钥。\n"
                              "复杂排版可能出现局部字距或换行调整；扫描件请先进行 OCR。", "muted", True))
        return widget

    def scope_changed(self, index):
        self.pages.setVisible(index == 2)

    def choose_pdf(self):
        path, _ = QFileDialog.getOpenFileName(self, "导入文献", str(self.source.parent) if self.source else "", "PDF 文献 (*.pdf)")
        if path:
            self.open_pdf(path)

    def open_pdf(self, path, result=None, translated_pages=None):
        if self.runner.running:
            self.statusBar().showMessage("请等待当前翻译结束，或先取消翻译。", 5000)
            return
        path = Path(path).resolve()
        try:
            with pymupdf.open(path) as doc:
                if not doc.is_pdf:
                    raise ValueError("请选择 PDF 文件。")
                if doc.needs_pass:
                    raise ValueError("这份 PDF 有密码保护，请先另存为可直接打开的 PDF。")
                count = len(doc)
                if count == 0:
                    raise ValueError("PDF 没有可阅读的页面。")
                toc = doc.get_toc()
                scanned = not any(doc[i].get_text().strip() for i in range(min(5, count)))
            # Validate with a temporary Qt document before replacing the current reader.
            probe = QPdfDocument(self)
            error = probe.load(str(path))
            probe.close()
            probe.deleteLater()
            if error != QPdfDocument.Error.None_:
                raise ValueError("Qt 无法打开此 PDF。")
            self.left.load(path)
        except Exception as exc:
            QMessageBox.warning(self, "无法导入", str(exc))
            return
        self.source = path
        self.page_count = count
        self.result = None
        self.result_pages = []
        self.right.clear()
        self.right.badge.setText("待翻译")
        self.right.empty_title.setText("译文将在这里呈现")
        self.right.empty_text.setText("在左侧选择语言和页码，点击开始学术翻译。\n生成译文后，两侧同步阅读。")
        self.export_btn.setEnabled(False)
        self.doc_title.setText(path.stem)
        self.doc_meta.setText(f"PDF 文献 · {count} 页\n{path.stat().st_size / 1024 / 1024:.1f} MB")
        self.filename.setText(path.name)
        self.filename.setToolTip(str(path))
        self.page.setRange(1, count)
        self.page.setValue(1)
        self.total_label.setText(f"/ {count} 页")
        self.outline.clear()
        if toc:
            for level, title, page in toc:
                if page < 1:
                    continue
                item = QListWidgetItem("  " * min(level - 1, 2) + title)
                item.setData(Qt.ItemDataRole.UserRole, min(page, count))
                item.setToolTip(f"第 {page} 页 · {title}")
                self.outline.addItem(item)
        else:
            for page in range(1, count + 1):
                item = QListWidgetItem(f"第 {page:02d} 页")
                item.setData(Qt.ItemDataRole.UserRole, page)
                self.outline.addItem(item)
        self.translate_btn.setEnabled(True)
        self.reader_stack.setCurrentIndex(1)
        self.go_page(1)
        if result and Path(result.get("mono", "")).is_file():
            try:
                self.load_result(result, translated_pages or list(range(1, count + 1)))
            except Exception as exc:
                self.append_log(f"历史译文无法加载：{exc}")
        self.remember_document()
        self.job_label.setText("可能为扫描件：请先使用 OCR 生成文本层。" if scanned else "文献已导入 · 可先选择当前页试译")
        self.statusBar().showMessage(f"已导入 {path.name}")

    def go_page(self, page):
        if not self.source:
            return
        self._syncing = True
        for pane in (self.left, self.right):
            if pane.doc.pageCount() >= page:
                pane.view.pageNavigator().jump(page - 1, QPointF(0, 0))
        self._syncing = False
        self.update_page_badge()

    def navigator_changed(self, page):
        if not self._syncing and page >= 0 and self.source:
            self.page.setValue(page + 1)

    def turn_page(self, delta):
        self.page.setValue(self.page.value() + delta)

    def update_page_badge(self):
        if self.result:
            self.right.badge.setText("已翻译" if self.page.value() in self.result_pages else "本页未翻译 · 原文")

    def sync_scroll(self, source, target, value):
        if not self.sync.isChecked() or self._syncing or not target.doc.pageCount():
            return
        self._syncing = True
        a, b = source.view.verticalScrollBar(), target.view.verticalScrollBar()
        b.setValue(round(value / max(1, a.maximum()) * b.maximum()))
        self._syncing = False

    def change_zoom(self, index):
        for pane in (self.left, self.right):
            if index < 2:
                pane.view.setZoomMode(QPdfView.ZoomMode.FitToWidth if index == 0 else QPdfView.ZoomMode.FitInView)
            else:
                pane.view.setZoomMode(QPdfView.ZoomMode.Custom)
                pane.view.setZoomFactor(float(self.zoom.currentText().strip("%")) / 100)

    def open_settings(self):
        self.settings["source"] = self.source_lang.currentData()
        self.settings["target"] = self.target_lang.currentData()
        dialog = SettingsDialog(self.settings, self.key, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.settings, self.key = dialog.settings, dialog.key
            self.statusBar().showMessage("翻译设置已保存", 4000)

    def start_translation(self):
        if not self.source or self.runner.running:
            return
        self.settings["source"] = self.source_lang.currentData()
        self.settings["target"] = self.target_lang.currentData()
        try:
            validate_service(self.settings, self.key)
        except ValueError:
            self.open_settings()
            try:
                validate_service(self.settings, self.key)
            except ValueError:
                return
        try:
            if self.settings["source"] == self.settings["target"]:
                raise ValueError("原文语言和译文语言不能相同。")
            requested = "" if self.scope.currentIndex() == 0 else str(self.page.value()) if self.scope.currentIndex() == 1 else self.pages.text()
            if self.scope.currentIndex() == 2 and not requested.strip():
                raise ValueError("请填写需要翻译的页码。")
            selected = parse_pages(requested, self.page_count)
            with pymupdf.open(self.source) as doc:
                if not any(doc[p - 1].get_text().strip() for p in selected):
                    raise ValueError("选中页面没有可提取的文字。请先对扫描件进行 OCR，再导入翻译。")
            if self.settings["glossary"]:
                import csv
                with open(self.settings["glossary"], encoding="utf-8-sig", newline="") as file:
                    reader = csv.reader(file)
                    headings = next(reader, [])
                    if headings != ["source", "target"]:
                        raise ValueError("术语表 CSV 的第一行应为 source,target。")
            output = DATA / "translations" / (datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid4().hex[:6])
            output.mkdir(parents=True)
            self.job = {"source": str(self.source), "output": str(output), "settings": dict(self.settings),
                        "key": self.key, "pages": requested, "selected": selected}
            self._pending_result = None
            self._error_text = ""
            self.logs.clear()
            self.runner.start(self.job)
        except Exception as exc:
            QMessageBox.warning(self, "无法开始翻译", str(exc))
            return
        self.set_busy(True)
        self.progress.setRange(0, 0)
        self.job_label.setText("正在准备翻译…")
        self.append_log("开始处理 " + self.source.name + "；页码：" + (requested or "全文"))

    def set_busy(self, busy):
        for item in (self.import_btn, self.settings_btn, self.translate_btn, self.scope,
                     self.pages, self.source_lang, self.target_lang, self.history_list):
            item.setEnabled(not busy)
        self.translate_btn.setVisible(not busy)
        self.cancel_btn.setVisible(busy)

    def on_event(self, event):
        kind = event["type"]
        if kind == "progress":
            stage = event.get("stage", "")
            stages = {"Parse PDF and Create Intermediate Representation": "解析 PDF 页面",
                      "Translate Paragraphs": "按学术语境翻译正文", "Typesetting": "在原版面排版译文",
                      "Generate PDF": "生成译文 PDF", "Automatic Term Extraction": "提取并统一学术术语",
                      "DetectScannedFile": "检查页面是否为扫描件", "Parse Page Layout": "识别分栏与页面布局",
                      "Parse Paragraphs": "识别段落结构", "Parse Formulas and Styles": "识别并保护公式与文字样式",
                      "Add Fonts": "嵌入译文字体", "Generate drawing instructions": "生成译文 PDF",
                      "Parse Table": "识别表格", "Save PDF": "保存译文 PDF"}
            stage = stages.get(stage, stage)
            self.job_label.setText(stage or "正在处理文献…")
            if isinstance(event.get("percent"), (int, float)):
                self.progress.setRange(0, 100)
                self.progress.setValue(max(0, min(99, round(event["percent"]))))
        elif kind == "result":
            self._pending_result = event
        elif kind == "error":
            self._error_text = event["message"]
            self.append_log(self._error_text)

    def on_ended(self, code, cancelled):
        self.set_busy(False)
        self.progress.setRange(0, 100)
        if cancelled:
            self.progress.setValue(0)
            self.job_label.setText("已取消 · 已有译文保留，可重新开始")
        elif code == 0 and self._pending_result:
            try:
                self.load_result(self._pending_result, self.job["selected"])
                manifest = {k: v for k, v in self.job.items() if k != "key"}
                manifest["result"] = self.result
                atomic_json(Path(self.job["output"]) / "job.json", manifest)
                self.remember_document()
                self.progress.setValue(100)
                self.job_label.setText(f"翻译完成 · {len(self.result_pages)} 页 · 可导出译文或左右双语 PDF")
                self.statusBar().showMessage("译文已生成，建议核对术语、公式及复杂图表。")
            except Exception as exc:
                self._error_text = str(exc)
                self.show_failure()
        else:
            if not self._error_text:
                self._error_text = "翻译引擎提前退出。请查看处理记录，检查网络、服务配置或文档兼容性。"
            self.show_failure()
        if self.job:
            self.job.pop("key", None)
        self.job = None

    def show_failure(self):
        self.progress.setValue(0)
        self.job_label.setText("翻译未完成 · 详见处理记录")
        self.logs.setVisible(True)
        self.append_log(self._error_text)
        QMessageBox.warning(self, "翻译未完成", self._error_text[:1200])

    def load_result(self, result, selected):
        mono = Path(result["mono"])
        with pymupdf.open(mono) as doc:
            if len(doc) != self.page_count:
                raise ValueError("译文页数与原文不同，无法同步对照。")
        self.right.load(mono)
        self.result = {k: result.get(k, "") for k in ("mono", "dual", "glossary")}
        self.result_pages = selected
        self.export_btn.setEnabled(True)
        self.go_page(self.page.value())
        self.change_zoom(self.zoom.currentIndex())

    def append_log(self, text):
        text = text.strip()
        if text:
            self.logs.appendPlainText(text)

    def toggle_logs(self):
        self.logs.setVisible(not self.logs.isVisible())

    def export_menu(self):
        menu = QMenu(self)
        for text, key, ext in (("导出译文 PDF", "mono", "pdf"), ("导出左右双语 PDF", "dual", "pdf"),
                               ("导出自动提取的术语表", "glossary", "csv")):
            action = menu.addAction(text)
            action.setEnabled(bool(self.result and self.result.get(key) and Path(self.result[key]).is_file()))
            action.triggered.connect(lambda checked=False, k=key, e=ext: self.export_file(k, e))
        menu.addSeparator()
        menu.addAction("打开译文所在文件夹", lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(self.result["mono"]).parent))))
        menu.exec(self.export_btn.mapToGlobal(self.export_btn.rect().bottomLeft()))

    def export_file(self, key, extension):
        if not self.result or not self.result.get(key):
            return
        source = Path(self.result[key])
        suffix = {"mono": "译文", "dual": "双语对照", "glossary": "术语表"}[key]
        name = self.source.stem + "_" + suffix + "." + extension
        path, _ = QFileDialog.getSaveFileName(self, "导出文件", str(self.source.parent / name), f"文件 (*.{extension})")
        if not path:
            return
        try:
            target = Path(path)
            if not target.suffix:
                target = target.with_suffix("." + extension)
            if target.resolve() == self.source.resolve():
                raise ValueError("请选择其他文件名，避免覆盖原始文献。")
            if target.resolve() != source.resolve():
                shutil.copy2(source, target)
            self.statusBar().showMessage("已导出至 " + str(target), 12000)
        except OSError as exc:
            QMessageBox.warning(self, "导出失败", str(exc))
        except ValueError as exc:
            QMessageBox.warning(self, "导出失败", str(exc))

    def load_history(self):
        try:
            self.history = json.loads((DATA / "history.json").read_text(encoding="utf-8"))
            if not isinstance(self.history, list):
                self.history = []
        except (ValueError, OSError):
            self.history = []
        self.refresh_history()

    def refresh_history(self):
        self.history_list.clear()
        for entry in self.history[:10]:
            if not isinstance(entry, dict) or not entry.get("source"):
                continue
            item = QListWidgetItem(Path(entry["source"]).stem)
            item.setData(Qt.ItemDataRole.UserRole, entry)
            item.setToolTip(entry["source"])
            self.history_list.addItem(item)

    def remember_document(self):
        if not self.source:
            return
        entry = {"source": str(self.source), "result": self.result, "pages": self.result_pages}
        self.history = [entry] + [x for x in self.history if isinstance(x, dict) and x.get("source") != str(self.source)]
        self.history = self.history[:10]
        try:
            atomic_json(DATA / "history.json", self.history)
        except OSError as exc:
            self.append_log("无法保存阅读历史：" + str(exc))
        self.refresh_history()

    def open_recent(self, item):
        entry = item.data(Qt.ItemDataRole.UserRole)
        self.open_pdf(entry["source"], entry.get("result"), entry.get("pages"))

    def dragEnterEvent(self, event):
        if not self.runner.running and event.mimeData().hasUrls() and any(u.toLocalFile().lower().endswith(".pdf") for u in event.mimeData().urls()):
            event.acceptProposedAction()

    def dropEvent(self, event):
        for url in event.mimeData().urls():
            if url.toLocalFile().lower().endswith(".pdf"):
                self.open_pdf(url.toLocalFile())
                break

    def closeEvent(self, event):
        if self.runner.running:
            choice = QMessageBox.question(self, "翻译正在进行", "关闭软件将取消本次翻译。是否关闭？")
            if choice != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self.runner.cancel()
            self.runner.process.waitForFinished(3000)
        self.left.view.setDocument(None)
        self.right.view.setDocument(None)
        self.left.doc.close()
        self.right.doc.close()
        event.accept()


def run():
    import sys
    app = QApplication(sys.argv)
    app.setApplicationName("PaperBridge")
    app.setOrganizationName("PaperBridge")
    app.setFont(QFont("Microsoft YaHei UI", 10))
    app.setStyleSheet(STYLE)
    window = MainWindow()
    window.show()
    if len(sys.argv) > 1 and Path(sys.argv[1]).is_file():
        QTimer.singleShot(0, lambda: window.open_pdf(sys.argv[1]))
    return app.exec()
