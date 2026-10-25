"""Background journal lookup and data source setup."""
from collections import deque
from copy import deepcopy

from PySide6.QtCore import QObject, QThread, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QSpinBox,
    QVBoxLayout,
)

from paperbridge.journals import JournalStore, LookupCancelled, timestamp


class JournalWorker(QThread):
    result = Signal(str, dict)

    def __init__(self, store, papers, force, parent=None):
        super().__init__(parent)
        self.store, self.papers, self.force = store, deepcopy(papers), force

    def run(self):
        for paper in self.papers:
            if self.isInterruptionRequested():
                break
            try:
                result = self.store.lookup(paper, self.force, self.isInterruptionRequested)
            except LookupCancelled:
                break
            except Exception:
                result = {"status": "查询未完成，请检查网络或本地数据文件。"}
            result["baseline"] = {k: paper.get(k) for k in ("journal", "issns", "doi")}
            self.result.emit(paper["source"], result)


class JournalController(QObject):
    updated = Signal(str)
    busy_changed = Signal(bool)

    def __init__(self, library, parent=None):
        super().__init__(parent)
        self.library = library
        self.store = JournalStore(library.root)
        self.worker = None
        self.queue = deque()
        self.stopping = False

    @property
    def busy(self):
        return bool(self.worker and self.worker.isRunning())

    def submit(self, papers, force=False):
        if not papers:
            return
        self.queue.append((deepcopy(papers), force))
        self._next()

    def _next(self):
        if self.worker is not None or not self.queue or self.stopping:
            return
        papers, force = self.queue.popleft()
        worker = JournalWorker(self.store, papers, force, self)
        self.worker = worker
        worker.result.connect(self.apply)
        worker.finished.connect(self._finished)
        self.busy_changed.emit(True)
        worker.start()

    def _finished(self):
        previous = self.worker
        self.worker = None
        if previous:
            previous.deleteLater()
        self.busy_changed.emit(False)
        self._next()

    def apply(self, source, result):
        if self.stopping:
            return
        paper = self.library.data["papers"].get(source)
        if not paper:
            return
        metadata = result.get("metadata", {})
        untouched = all(paper.get(k) == v for k, v in result.get("baseline", {}).items())
        if untouched:
            if metadata.get("journal") and not paper.get("journal"):
                paper["journal"] = metadata["journal"]
            for key in ("doi", "issns", "metadata_source", "identified_at"):
                if metadata.get(key) and not paper.get(key):
                    paper[key] = metadata[key]
        if "rankings" in result:
            # Re-match after any manual edit made while a lookup was running.
            paper["ranking_year"] = result["year"]
            paper["rankings"] = self.store.cached(paper.get("journal", ""), paper.get("issns", []), result["year"])
        paper["ranking_status"] = result.get("status", "")
        paper["ranking_checked_at"] = timestamp()
        try:
            self.library.save()
        except OSError:
            paper["ranking_status"] = "结果已获取，但本地保存失败，请检查目录权限和剩余空间。"
        self.updated.emit(source)

    def rematch_all(self):
        year = int(self.store.config["year"])
        for paper in self.library.data["papers"].values():
            paper["ranking_year"] = year
            paper["rankings"] = self.store.cached(paper.get("journal", ""), paper.get("issns", []), year)
        self.library.save()
        self.updated.emit("")

    def stop(self):
        self.stopping = True
        self.queue.clear()
        if self.worker and self.worker.isRunning():
            self.worker.requestInterruption()
            return self.worker.wait(15000)
        return True


class JournalSettingsDialog(QDialog):
    def __init__(self, store, parent=None):
        super().__init__(parent)
        self.store = store
        self.setWindowTitle("期刊与分区数据源")
        self.resize(650, 430)
        layout = QVBoxLayout(self)
        note = QLabel("期刊名称可通过 DOI 自动识别。分区优先匹配本地缓存；在线查询需要对应官方接口授权。学校网站登录权限不一定包含 API 权限。")
        note.setWordWrap(True)
        layout.addWidget(note)
        form = QFormLayout()
        self.year = QSpinBox()
        self.year.setRange(2000, 2100)
        self.year.setValue(int(store.config["year"]))
        self.auto = QCheckBox("导入 / 打开文献时自动识别期刊并匹配分区")
        self.auto.setChecked(store.config["auto"])
        self.cas_user = QLineEdit(store.config["cas_user"])
        self.cas_password = QLineEdit(store.config["cas_password"])
        self.jcr_key = QLineEdit(store.config["jcr_key"])
        for field in (self.cas_password, self.jcr_key):
            field.setEchoMode(QLineEdit.EchoMode.Password)
        self.remember = QCheckBox("在此电脑加密保存接口授权信息")
        self.remember.setChecked(store.config["remember"])
        for title, widget in [("分区数据年份", self.year), ("", self.auto),
                              ("中科院 API 用户名", self.cas_user), ("中科院 API 密码", self.cas_password),
                              ("Clarivate Journals API Key", self.jcr_key), ("", self.remember)]:
            form.addRow(title, widget)
        layout.addLayout(form)
        links = QLabel('<a href="https://webapi.fenqubiao.com/">中科院接口申请</a>　'
                       '<a href="https://developer.clarivate.com/apis/wos-journal">JCR 接口说明</a>')
        links.setOpenExternalLinks(True)
        layout.addWidget(links)
        layout.addWidget(QLabel("没有 API 授权也可导入 Excel / CSV 分区表，在本地自动匹配。"))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def save(self):
        values = dict(year=self.year.value(), auto=self.auto.isChecked(), remember=self.remember.isChecked(),
                      cas_user=self.cas_user.text().strip(), cas_password=self.cas_password.text().strip(),
                      jcr_key=self.jcr_key.text().strip())
        try:
            self.store.configure(values)
            self.accept()
        except (OSError, ValueError):
            QMessageBox.warning(self, "保存失败", "无法保存数据源设置或加密授权信息，请检查软件目录权限。")


class ManualRankingDialog(QDialog):
    def __init__(self, controller, paper, parent=None):
        super().__init__(parent)
        self.controller, self.paper = controller, paper
        self.setWindowTitle("手动校正分区")
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("每次保存一个学科；请核对年份、体系及学科。记录来源标记为用户手动校正。"))
        form = QFormLayout()
        self.scheme = QComboBox()
        self.scheme.addItems(["中科院大类", "中科院小类", "JCR（JIF 四分位）"])
        self.year = QSpinBox()
        self.year.setRange(2000, 2100)
        self.year.setValue(int(controller.store.config["year"]))
        self.subject = QComboBox()
        self.subject.setEditable(True)
        self.zone = QComboBox()
        for title, widget in [("体系 / 级别", self.scheme), ("分区年份", self.year),
                              ("学科", self.subject), ("分区", self.zone)]:
            form.addRow(title, widget)
        layout.addLayout(form)
        self.scheme.currentIndexChanged.connect(self.populate)
        self.year.valueChanged.connect(self.populate)
        self.populate()
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def populate(self):
        self.zone.clear()
        self.zone.addItems(["Q1", "Q2", "Q3", "Q4"] if self.scheme.currentIndex() == 2 else ["1区", "2区", "3区", "4区"])
        self.subject.clear()
        scheme = "JCR" if self.scheme.currentIndex() == 2 else "CAS"
        scope = ["大类", "小类", "JIF"][self.scheme.currentIndex()]
        records = [r for r in self.controller.store.cached(self.paper.get("journal", ""), self.paper.get("issns", []), self.year.value())
                   if r["scheme"] == scheme and r["scope"] == scope]
        self.subject.addItems([r["category"] for r in records])
        if records:
            self.zone.setCurrentText(records[0]["zone"])

    def save(self):
        record = {"journal": self.paper.get("journal", ""), "issns": self.paper.get("issns", []),
                  "scheme": "JCR" if self.scheme.currentIndex() == 2 else "CAS", "year": self.year.value(),
                  "scope": ["大类", "小类", "JIF"][self.scheme.currentIndex()],
                  "category": self.subject.currentText().strip(), "zone": self.zone.currentText(),
                  "source": "用户手动校正", "source_url": "", "fetched_at": timestamp()}
        if not (record["journal"] or record["issns"]) or not record["category"]:
            QMessageBox.warning(self, "信息不完整", "请先填写期刊 / ISSN，并填写本次分区对应的学科。")
            return
        try:
            self.controller.store.store_records([record])
            self.controller.rematch_all()
            self.accept()
        except OSError:
            QMessageBox.warning(self, "保存失败", "无法写入本地分区缓存。")
