import os
import json
import datetime
from PySide6.QtWidgets import (
    QDockWidget, QTreeWidget, QTreeWidgetItem, QHeaderView,
    QMenu, QFileDialog, QMessageBox
)
from PySide6.QtGui import QColor, QBrush, QAction
from PySide6.QtCore import Qt


class LogWindow(QDockWidget):
    """带分类和颜色标记的日志窗口"""

    # 日志类型常量（与OCRProcessor中的定义匹配）
    LOG_INFO = 0
    LOG_WARNING = 1
    LOG_ERROR = 2
    LOG_RECOVERY = 3
    LOG_CHECK = 4
    LOG_ANALYSIS = 5

    # 日志类型到颜色的映射
    LOG_COLORS = {
        LOG_INFO: QColor("#000000"),  # 黑色 - 信息
        LOG_WARNING: QColor("#FFA500"),  # 橙色 - 警告
        LOG_ERROR: QColor("#FF0000"),  # 红色 - 错误
        LOG_RECOVERY: QColor("#0000FF"),  # 蓝色 - 恢复
        LOG_CHECK: QColor("#008000"),  # 绿色 - 检查
        LOG_ANALYSIS: QColor("#800080")  # 紫色 - 分析
    }

    # 日志类型名称
    LOG_TYPE_NAMES = {
        LOG_INFO: "信息",
        LOG_WARNING: "警告",
        LOG_ERROR: "错误",
        LOG_RECOVERY: "修复",
        LOG_CHECK: "检查",
        LOG_ANALYSIS: "分析"
    }

    def __init__(self, parent=None):
        super().__init__("处理日志", parent)
        self.setObjectName("LogWindow")
        self.setFeatures(QDockWidget.DockWidgetMovable | QDockWidget.DockWidgetFloatable)

        # 创建日志树形视图
        self.log_tree = QTreeWidget()
        self.log_tree.setColumnCount(4)
        self.log_tree.setHeaderLabels(["时间", "类型", "来源", "消息"])
        self.log_tree.setAlternatingRowColors(True)
        self.log_tree.setRootIsDecorated(False)
        self.log_tree.setSelectionMode(QTreeWidget.ExtendedSelection)

        # 设置列宽
        header = self.log_tree.header()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)  # 时间列自适应
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)  # 类型列自适应
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)  # 来源列自适应
        header.setSectionResizeMode(3, QHeaderView.Stretch)  # 消息列拉伸

        # 添加上下文菜单
        self.log_tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.log_tree.customContextMenuRequested.connect(self.show_context_menu)

        self.setWidget(self.log_tree)

        # 存储所有日志项
        self.log_items = []

        # 创建上下文菜单动作
        self.clear_action = QAction("清空日志", self)
        self.clear_action.triggered.connect(self.clear_logs)

        self.save_action = QAction("保存日志...", self)
        self.save_action.triggered.connect(self.save_logs)

        self.copy_action = QAction("复制选中日志", self)
        self.copy_action.triggered.connect(self.copy_selected)

        self.select_all_action = QAction("全选", self)
        self.select_all_action.triggered.connect(self.log_tree.selectAll)

    def add_log(self, message, log_type, source=""):
        """添加带时间戳和颜色的日志项"""
        # 获取当前时间
        timestamp = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]

        # 创建日志项
        log_item = QTreeWidgetItem([
            timestamp,
            self.LOG_TYPE_NAMES.get(log_type, "未知"),
            source,
            message
        ])

        # 设置日志颜色
        color = self.LOG_COLORS.get(log_type, QColor("#000000"))
        log_item.setForeground(1, QBrush(color))  # 类型列着色
        log_item.setForeground(3, QBrush(color))  # 消息列着色

        # 添加到树形视图
        self.log_tree.addTopLevelItem(log_item)
        self.log_items.append(log_item)

        # 自动滚动到底部
        self.log_tree.scrollToItem(log_item)

        # 返回日志项用于进一步处理
        return log_item

    def show_context_menu(self, position):
        """显示上下文菜单"""
        menu = QMenu()
        menu.addAction(self.clear_action)
        menu.addAction(self.save_action)
        menu.addAction(self.copy_action)
        menu.addSeparator()
        menu.addAction(self.select_all_action)
        menu.exec_(self.log_tree.viewport().mapToGlobal(position))

    def clear_logs(self):
        """清空所有日志"""
        self.log_tree.clear()
        self.log_items = []

    def save_logs(self):
        """保存日志到文件"""
        file_path, _ = QFileDialog.getSaveFileName(
            self, "保存日志", "", "文本文件 (*.txt);;所有文件 (*)"
        )

        if not file_path:
            return

        try:
            with open(file_path, 'w', encoding='utf-8') as f:
                # 添加标题行
                f.write("时间\t类型\t来源\t消息\n")

                for item in self.log_items:
                    time_str = item.text(0)
                    type_str = item.text(1)
                    source_str = item.text(2)
                    message_str = item.text(3)
                    f.write(f"{time_str}\t{type_str}\t{source_str}\t{message_str}\n")

            QMessageBox.information(self, "保存成功", f"日志已保存到: {file_path}")
        except Exception as e:
            QMessageBox.critical(self, "保存失败", f"无法保存日志: {str(e)}")

    def copy_selected(self):
        """复制选中的日志"""
        selected_items = self.log_tree.selectedItems()
        if not selected_items:
            return

        text = ""
        for item in selected_items:
            time_str = item.text(0)
            type_str = item.text(1)
            source_str = item.text(2)
            message_str = item.text(3)
            text += f"{time_str} [{type_str}] {source_str}: {message_str}\n"

        # 复制到剪贴板
        from PySide6.QtWidgets import QApplication
        clipboard = QApplication.clipboard()
        clipboard.setText(text)