"""Paper management dialog."""
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
)

from paperbridge.journal_ui import JournalController, JournalSettingsDialog, ManualRankingDialog
from paperbridge.journals import issns, ranking_summary


class LibraryDialog(QDialog):
    def __init__(self, library, parent=None, controller=None):
        super().__init__(parent)
        self.library = library
        self.owns_controller = controller is None
        self.controller = controller or JournalController(library, self)
        self.dirty = False
        self.open_path = None
        self.setWindowTitle("文献管理")
        self.resize(1000, 800)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("按分类收录 PDF，并维护期刊与分区信息"))
        layout.addWidget(QLabel("中科院分区与 JCR 四分位分别保存；缺失数据保留为未获取，可配置官方接口或导入分区表。"))
        actions = QHBoxLayout()
        for title, callback in [("导入文献", self.import_papers), ("添加分类", self.add_category),
                                ("打开文献库文件夹", self.open_folder)]:
            btn = QPushButton(title)
            btn.clicked.connect(callback)
            actions.addWidget(btn)
        layout.addLayout(actions)
        sources = QHBoxLayout()
        self.lookup_button = QPushButton("查询 / 补全所选文献")
        self.lookup_button.clicked.connect(self.lookup_selected)
        self.force_button = QPushButton("联网刷新所选")
        self.force_button.clicked.connect(lambda: self.lookup_selected(force=True))
        self.batch_button = QPushButton("批量补全文献库")
        self.batch_button.clicked.connect(lambda: self.controller.submit(list(self.library.data["papers"].values())))
        self.settings_button = QPushButton("分区数据源")
        self.settings_button.clicked.connect(self.configure_sources)
        self.import_button = QPushButton("导入分区表")
        self.import_button.clicked.connect(self.import_rankings)
        self.template_button = QPushButton("导出表格模板")
        self.template_button.clicked.connect(self.export_template)
        for btn in (self.lookup_button, self.force_button, self.batch_button, self.settings_button, self.import_button, self.template_button):
            sources.addWidget(btn)
        layout.addLayout(sources)
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索文献、期刊、分类或分区")
        self.search.textChanged.connect(self.refresh)
        layout.addWidget(self.search)
        self.items = QListWidget()
        self.items.setWordWrap(True)
        self.items.currentItemChanged.connect(self.select)
        layout.addWidget(self.items, 1)
        form = QFormLayout()
        self.journal = QLineEdit()
        self.identifiers = QLineEdit()
        self.doi = QLineEdit()
        self.partition = QLineEdit()
        for field in (self.journal, self.identifiers, self.doi, self.partition):
            field.textEdited.connect(self.mark_dirty)
        self.category = QComboBox()
        form.addRow("期刊", self.journal)
        form.addRow("ISSN / eISSN", self.identifiers)
        form.addRow("DOI", self.doi)
        form.addRow("手工分区备注", self.partition)
        form.addRow("分类", self.category)
        layout.addLayout(form)
        self.ranking_details = QTextBrowser()
        self.ranking_details.setMaximumHeight(170)
        layout.addWidget(self.ranking_details)
        self.status = QLabel("查询成功后自动本地保存；重复查询优先读取同年份缓存。")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        buttons = QHBoxLayout()
        for title, callback in [("保存文献信息", self.save), ("打开阅读", self.open_paper), ("关闭", self.accept)]:
            btn = QPushButton(title)
            btn.clicked.connect(callback)
            buttons.addWidget(btn)
        self.manual_button = QPushButton("手动校正分区")
        self.manual_button.clicked.connect(self.correct_rankings)
        buttons.insertWidget(1, self.manual_button)
        layout.addLayout(buttons)
        self.controller.updated.connect(self.lookup_finished)
        self.controller.busy_changed.connect(self.set_query_busy)
        self.set_query_busy(self.controller.busy)
        self.refresh()

    def refresh(self):
        previous = self.items.currentItem()
        source = previous.data(Qt.ItemDataRole.UserRole)["source"] if previous else None
        self.items.clear()
        self.category.clear()
        self.category.addItems(["未分类"] + self.library.data["categories"])
        query = self.search.text().strip().casefold()
        for paper in self.library.data["papers"].values():
            text = f"{paper['title']}\n{paper.get('journal') or '期刊未填写'} · {paper.get('category') or '未分类'}\n{ranking_summary(paper)}"
            if query and query not in text.casefold():
                continue
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, paper)
            self.items.addItem(item)
        if self.items.count():
            target = next((i for i in range(self.items.count()) if self.items.item(i).data(Qt.ItemDataRole.UserRole)["source"] == source), 0)
            self.items.setCurrentRow(target)

    def select(self, item, previous=None):
        if item:
            paper = item.data(Qt.ItemDataRole.UserRole)
            self.journal.setText(paper.get("journal", ""))
            self.identifiers.setText("; ".join(paper.get("issns", [])))
            self.doi.setText(paper.get("doi", ""))
            self.partition.setText(paper.get("partition", ""))
            self.category.setCurrentText(paper.get("category") or "未分类")
            details = ranking_summary(paper)
            for record in paper.get("rankings", []):
                details += f"\n{record['scheme']} {record['year']} {record['category']} {record['zone']}\n来源：{record['source']}\n保存时间：{record['fetched_at']}"
            self.ranking_details.setPlainText(details)
            self.status.setText(paper.get("ranking_status") or "尚未查询，可先配置数据源或导入分区表。")
            self.dirty = False

    def mark_dirty(self):
        self.dirty = True

    def set_query_busy(self, busy):
        for widget in (self.lookup_button, self.force_button, self.batch_button, self.settings_button, self.import_button, self.manual_button):
            widget.setEnabled(not busy)
        if busy:
            self.status.setText("正在识别期刊 / 匹配分区，可继续阅读，结果将自动本地保存…")

    def lookup_finished(self, source):
        if not self.dirty:
            self.refresh()

    def lookup_selected(self, force=False):
        if not self.save():
            return
        item = self.items.currentItem()
        if item:
            self.controller.submit([item.data(Qt.ItemDataRole.UserRole)], force=force)

    def correct_rankings(self):
        if self.save():
            item = self.items.currentItem()
            if item:
                dialog = ManualRankingDialog(self.controller, item.data(Qt.ItemDataRole.UserRole), self)
                dialog.exec()
                self.refresh()

    def configure_sources(self):
        dialog = JournalSettingsDialog(self.controller.store, self)
        if dialog.exec():
            self.controller.rematch_all()

    def import_rankings(self):
        path, _ = QFileDialog.getOpenFileName(self, "导入中科院 / JCR 分区表", "", "分区表 (*.xlsx *.csv)")
        if not path:
            return
        try:
            count = self.controller.store.import_table(path)
            self.controller.rematch_all()
            self.status.setText(f"已导入 {count} 条分区记录，并按设置年份匹配文献库。")
        except (ValueError, OSError, KeyError, ImportError) as exc:
            QMessageBox.warning(self, "导入失败", str(exc))

    def export_template(self):
        path, _ = QFileDialog.getSaveFileName(self, "导出分区表模板", "分区表模板.xlsx", "Excel (*.xlsx);;CSV (*.csv)")
        if path:
            try:
                self.controller.store.export_template(path)
                self.status.setText("模板已导出：一行一个体系 / 年份 / 学科；中科院级别填大类或小类，JCR 填 JIF。")
            except (OSError, ImportError) as exc:
                QMessageBox.warning(self, "导出失败", str(exc))

    def add_category(self):
        name, ok = QInputDialog.getText(self, "添加分类", "分类名称（将在文献库中创建同名文件夹）")
        if ok:
            try:
                self.library.add_category(name)
                self.refresh()
            except (ValueError, OSError) as exc:
                QMessageBox.warning(self, "无法创建分类", str(exc))

    def import_papers(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "导入文献", "", "PDF (*.pdf)")
        try:
            for path in paths:
                self.library.register(path)
            self.refresh()
            if self.controller.store.config["auto"]:
                self.controller.submit([self.library.register(path) for path in paths])
        except (ValueError, OSError) as exc:
            QMessageBox.warning(self, "导入失败", str(exc))

    def save(self):
        item = self.items.currentItem()
        if not item:
            return False
        try:
            paper = self.library.data["papers"][item.data(Qt.ItemDataRole.UserRole)["source"]]
            identifiers = issns(self.identifiers.text())
            if self.identifiers.text().strip() and not identifiers:
                raise ValueError("ISSN 请填写 1234-567X 形式，多个可用分号分隔。")
            paper.update(issns=identifiers, doi=self.doi.text().strip())
            self.library.update(paper, self.journal.text(),
                                self.partition.text(), self.category.currentText() if self.category.currentIndex() > 0 else "")
            self.controller.rematch_all()
            self.dirty = False
            self.refresh()
            return True
        except (ValueError, OSError) as exc:
            QMessageBox.warning(self, "保存失败", str(exc))
            return False

    def open_paper(self):
        item = self.items.currentItem()
        if item:
            paper = item.data(Qt.ItemDataRole.UserRole)
            path = next((p for p in [paper["source"], paper.get("managed", "")] if p and Path(p).is_file()), None)
            if path:
                self.open_path = path
                self.accept()
            else:
                QMessageBox.warning(self, "无法打开", "文献文件已不存在。")

    def open_folder(self):
        self.library.root.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.library.root)))

    def done(self, result):
        if self.owns_controller and not self.controller.stop():
            return
        super().done(result)
