"""日志窗口 —— 带颜色编码的 DockWidget 日志查看器。"""

import datetime

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QApplication, QDockWidget, QFileDialog, QHeaderView,
    QMenu, QMessageBox, QTreeWidget, QTreeWidgetItem,
)

LOG_INFO = 0; LOG_WARNING = 1; LOG_ERROR = 2
LOG_RECOVERY = 3; LOG_CHECK = 4; LOG_ANALYSIS = 5

LOG_COLORS = {
    LOG_INFO: QColor("#000000"), LOG_WARNING: QColor("#FFA500"),
    LOG_ERROR: QColor("#FF0000"), LOG_RECOVERY: QColor("#0000FF"),
    LOG_CHECK: QColor("#008000"), LOG_ANALYSIS: QColor("#800080"),
}
LOG_NAMES = {
    LOG_INFO: "信息", LOG_WARNING: "警告", LOG_ERROR: "错误",
    LOG_RECOVERY: "修复", LOG_CHECK: "检查", LOG_ANALYSIS: "分析",
}


class LogWindow(QDockWidget):
    """可停靠日志窗口 —— 也可作为独立 Widget 使用"""

    def __init__(self, parent=None):
        super().__init__("处理日志", parent)
        self.setObjectName("LogWindow")
        self.setFeatures(QDockWidget.DockWidgetMovable | QDockWidget.DockWidgetFloatable)

        self._tree = QTreeWidget()
        self._tree.setColumnCount(4)
        self._tree.setHeaderLabels(["时间", "类型", "来源", "消息"])
        self._tree.setAlternatingRowColors(True)
        self._tree.setRootIsDecorated(False)
        self._tree.setSelectionMode(QTreeWidget.ExtendedSelection)

        header = self._tree.header()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.Stretch)

        self._tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self._tree.customContextMenuRequested.connect(self._show_menu)
        self.setWidget(self._tree)
        self._items: list[QTreeWidgetItem] = []

    def add_log(self, message: str, log_type: int = LOG_INFO, source: str = ""):
        ts = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
        item = QTreeWidgetItem([ts, LOG_NAMES.get(log_type, "未知"), source, message])
        color = LOG_COLORS.get(log_type, QColor("#000000"))
        item.setForeground(1, QBrush(color))
        item.setForeground(3, QBrush(color))
        self._tree.addTopLevelItem(item)
        self._items.append(item)
        self._tree.scrollToItem(item)

    def clear_logs(self):
        self._tree.clear()
        self._items.clear()

    def save_logs(self):
        path, _ = QFileDialog.getSaveFileName(self, "保存日志", "", "文本文件 (*.txt)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write("时间\t类型\t来源\t消息\n")
                for item in self._items:
                    f.write(f"{item.text(0)}\t{item.text(1)}\t{item.text(2)}\t{item.text(3)}\n")
            QMessageBox.information(self, "保存成功", f"日志已保存到: {path}")
        except Exception as e:
            QMessageBox.critical(self, "保存失败", str(e))

    def _show_menu(self, pos):
        menu = QMenu()
        menu.addAction("清空日志", self.clear_logs)
        menu.addAction("保存日志...", self.save_logs)
        menu.addAction("复制选中", self._copy_selected)
        menu.addAction("全选", self._tree.selectAll)
        menu.exec_(self._tree.viewport().mapToGlobal(pos))

    def _copy_selected(self):
        lines = []
        for item in self._tree.selectedItems():
            lines.append(f"{item.text(0)} [{item.text(1)}] {item.text(2)}: {item.text(3)}")
        if lines:
            QApplication.clipboard().setText("\n".join(lines))
