"""Drag papers onto sidebar category folders."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QDrag
from PySide6.QtWidgets import QAbstractItemView, QTreeWidget


class LibraryTree(QTreeWidget):
    reclassify = Signal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setHeaderHidden(True)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)

    def startDrag(self, supported_actions):
        item = self.currentItem()
        if not item or not item.data(0, Qt.ItemDataRole.UserRole):
            return
        drag = QDrag(self)
        drag.setMimeData(self.mimeData([item]))
        drag.exec(Qt.DropAction.MoveAction)

    def dropEvent(self, event):
        source = self.currentItem()
        target = self.itemAt(event.position().toPoint())
        if target and target.parent():
            target = target.parent()
        identity = source.data(0, Qt.ItemDataRole.UserRole) if source else None
        if event.source() is not self or not identity or target is None or target.parent():
            event.ignore()
            return
        category = target.data(0, Qt.ItemDataRole.UserRole + 1)
        if category is None:
            event.ignore()
            return
        # Persistence owns the move; Qt must not move or clone stale tree items.
        event.setDropAction(Qt.DropAction.MoveAction)
        event.accept()
        self.reclassify.emit(identity, category)
