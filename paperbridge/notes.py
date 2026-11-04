"""Page-coordinate ink, highlight and text notes stored beside reader history."""
import hashlib
import json
from pathlib import Path

import pymupdf
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPen
from PySide6.QtWidgets import (
    QColorDialog,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from paperbridge.core import atomic_json


class NoteStore:
    def __init__(self, root, source):
        self.source = Path(source).resolve()
        digest = hashlib.sha256(str(self.source).casefold().encode()).hexdigest()
        self.path = Path(root) / (digest + ".json")
        try:
            self.data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.data = {"source": str(self.source), "pages": {}}

    def items(self, page):
        return self.data["pages"].get(str(page), [])

    def add(self, page, item):
        self.data["pages"].setdefault(str(page), []).append(item)
        self.save()

    def undo(self, page):
        if self.items(page):
            self.data["pages"][str(page)].pop()
            self.save()

    def save(self):
        atomic_json(self.path, self.data)

    def export(self, target):
        if Path(target).resolve() == self.source:
            raise ValueError("请选择其他文件名。")
        with pymupdf.open(self.source) as doc:
            for number, items in self.data["pages"].items():
                page = doc[int(number)]
                for item in items:
                    color = QColor(item["color"])
                    rgb = (color.redF(), color.greenF(), color.blueF())
                    points = item["points"]
                    if item["kind"] == "comment":
                        annotation = page.add_text_annot(points[0], item["text"])
                    elif item["kind"] == "highlight":
                        rect = pymupdf.Rect(*points[0], *points[-1]).normalize()
                        if rect.is_empty:
                            continue
                        annotation = page.add_highlight_annot(rect)
                    else:
                        annotation = page.add_ink_annot([points if len(points) > 1 else points * 2])
                    annotation.set_colors(stroke=rgb)
                    annotation.update()
            doc.save(target, garbage=3, deflate=True)


class NoteCanvas(QWidget):
    def __init__(self, editor):
        super().__init__()
        self.editor = editor
        self.image = QImage()
        self.scale = 1
        self.points = []
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.CrossCursor)

    def render_page(self, width):
        if not self.editor.store:
            return
        with pymupdf.open(self.editor.store.source) as doc:
            page = doc[self.editor.page]
            self.scale = max(0.3, min(3, width / page.rect.width))
            pix = page.get_pixmap(matrix=pymupdf.Matrix(self.scale, self.scale), alpha=False)
            self.image = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format.Format_RGB888).copy()
        self.setFixedSize(self.image.size())
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.drawImage(0, 0, self.image)
        if not self.editor.store:
            return
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        items = self.editor.store.items(self.editor.page)
        if self.points:
            items = items + [{"kind": self.editor.mode.currentData(), "color": self.editor.color,
                              "points": self.points}]
        for item in items:
            points = [QPointF(x * self.scale, y * self.scale) for x, y in item["points"]]
            color = QColor(item["color"])
            painter.setPen(QPen(color, max(1.5, self.scale * 1.5)))
            if item["kind"] == "highlight":
                color.setAlpha(85)
                painter.fillRect(QRectF(points[0], points[-1]).normalized(), color)
            elif item["kind"] == "comment":
                painter.setBrush(color)
                painter.drawEllipse(points[0], 8, 8)
                painter.setBrush(Qt.BrushStyle.NoBrush)
            else:
                for a, b in zip(points, points[1:]):
                    painter.drawLine(a, b)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.editor.store:
            self.points = [[event.position().x() / self.scale, event.position().y() / self.scale]]

    def mouseMoveEvent(self, event):
        if self.points and event.buttons() & Qt.MouseButton.LeftButton:
            self.points.append([event.position().x() / self.scale, event.position().y() / self.scale])
            self.update()
        elif self.editor.store:
            point = event.position() / self.scale
            notes = [x.get("text", "") for x in self.editor.store.items(self.editor.page)
                     if x["kind"] == "comment" and (QPointF(*x["points"][0]) - point).manhattanLength() < 20]
            self.setToolTip("\n".join(notes))

    def mouseReleaseEvent(self, event):
        if not self.points or event.button() != Qt.MouseButton.LeftButton:
            return
        self.points.append([event.position().x() / self.scale, event.position().y() / self.scale])
        item = {"kind": self.editor.mode.currentData(), "color": self.editor.color, "points": self.points}
        self.points = []
        if item["kind"] == "comment":
            text, ok = QInputDialog.getMultiLineText(self, "批注", "", "")
            if not ok or not text.strip():
                self.update()
                return
            item["text"] = text.strip()
        try:
            self.editor.store.add(self.editor.page, item)
            self.editor.refresh_list()
        except OSError as exc:
            QMessageBox.warning(self, "保存失败", str(exc))
        self.update()


class NoteEditor(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.store = None
        self.page = 0
        self.color = "#f2c94c"
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        bar = QHBoxLayout()
        self.mode = QComboBox()
        for text, mode in [("画线", "ink"), ("标色", "highlight"), ("批注", "comment")]:
            self.mode.addItem(text, mode)
        bar.addWidget(self.mode)
        for title, callback in [("颜色", self.choose_color), ("撤销", self.undo), ("本页笔记", self.show_notes), ("导出批注", self.export)]:
            btn = QPushButton(title)
            btn.clicked.connect(callback)
            bar.addWidget(btn)
        layout.addLayout(bar)
        self.scroll = QScrollArea()
        self.scroll.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.canvas = NoteCanvas(self)
        self.scroll.setWidget(self.canvas)
        layout.addWidget(self.scroll)
        self.count = QLabel()
        layout.addWidget(self.count)

    def load(self, source, root, page=0):
        self.store = NoteStore(root, source)
        self.set_page(page)

    def set_page(self, page):
        self.page = page
        self.canvas.points = []
        self.canvas.render_page(max(300, self.scroll.viewport().width() - 20))
        self.scroll.verticalScrollBar().setValue(0)
        self.refresh_list()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.canvas.render_page(max(300, self.scroll.viewport().width() - 20))

    def refresh_list(self):
        items = self.store.items(self.page) if self.store else []
        self.count.setText(f"{len(items)} 条笔记")

    def choose_color(self):
        color = QColorDialog.getColor(QColor(self.color), self)
        if color.isValid():
            self.color = color.name()

    def undo(self):
        if self.store:
            self.store.undo(self.page)
            self.refresh_list()
            self.canvas.update()

    def show_notes(self):
        if self.store:
            texts = [x["text"] for x in self.store.items(self.page) if x["kind"] == "comment"]
            QMessageBox.information(self, "本页批注", "\n\n".join(texts) or "暂无批注")

    def export(self):
        if not self.store:
            return
        path, _ = QFileDialog.getSaveFileName(self, "导出批注", str(self.store.source.with_name(self.store.source.stem + "_批注.pdf")), "PDF (*.pdf)")
        if path:
            try:
                self.store.export(path)
            except (OSError, ValueError, RuntimeError) as exc:
                QMessageBox.warning(self, "导出失败", str(exc))
