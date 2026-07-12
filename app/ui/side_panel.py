"""侧边管理面板 —— 标签管理、ROI 模板、历史视频。"""

import json
import os

from PySide6.QtCore import Qt, QSettings, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFormLayout, QGroupBox,
    QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMessageBox, QPushButton, QVBoxLayout, QWidget,
)

from app.core.project import Project
from app.core.roi_templates import ROITemplateManager


class SidePanelWidget(QWidget):
    """左侧面板：标签管理 + ROI 模板 + 历史视频"""

    regions_changed = Signal()
    template_applied = Signal(str)
    tag_selected = Signal(int)
    label_colors_changed = Signal(dict)
    history_opened = Signal(str)

    DEFAULT_LABELS = ["击杀信息", "淘汰计数"]

    def __init__(self, parent=None):
        super().__init__(parent)
        self._project: Project | None = None
        self._label_types = list(self.DEFAULT_LABELS)
        self._label_colors = self._gen_colors()
        self._current_label = self._label_types[0]
        self._tpl_mgr = ROITemplateManager()
        self._setup_ui()
        self._load_history()

    def set_project(self, project: Project):
        self._project = project

    def label_colors(self) -> dict:
        return dict(self._label_colors)

    @property
    def current_label(self) -> str:
        return self._current_label

    # ---- UI ----

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setSpacing(8)

        g1 = QGroupBox("标签管理")
        gl = QVBoxLayout(g1)
        gl.setSpacing(5)
        tl = QHBoxLayout()
        tl.addWidget(QLabel("标签类型:"))
        self._type_combo = QComboBox()
        self._type_combo.addItems(self._label_types)
        self._type_combo.currentTextChanged.connect(
            lambda t: setattr(self, '_current_label', t))
        tl.addWidget(self._type_combo, 1)
        gl.addLayout(tl)
        self._tag_list = QListWidget()
        self._tag_list.setMinimumHeight(120)
        self._tag_list.itemSelectionChanged.connect(self._on_tag_selected)
        gl.addWidget(self._tag_list)
        btns = QHBoxLayout()
        btns.addWidget(QPushButton("编辑", clicked=self._edit_tag))
        btns.addWidget(QPushButton("删除", clicked=self._delete_tag))
        gl.addLayout(btns)
        layout.addWidget(g1)

        g2 = QGroupBox("ROI 模板")
        gl2 = QVBoxLayout(g2)
        gl2.setSpacing(5)
        self._tpl_list = QListWidget()
        self._tpl_list.setMaximumHeight(120)
        self._tpl_list.itemDoubleClicked.connect(self._on_template_double_clicked)
        gl2.addWidget(self._tpl_list)
        tbtns1 = QHBoxLayout()
        tbtns1.addWidget(QPushButton("应用选中", clicked=self._apply_template_from_selection))
        tbtns1.addWidget(QPushButton("保存当前", clicked=self._save_current_as_template))
        gl2.addLayout(tbtns1)
        tbtns2 = QHBoxLayout()
        self._default_btn = QPushButton("设为默认")
        self._default_btn.clicked.connect(self._set_default_template)
        tbtns2.addWidget(self._default_btn)
        tbtns2.addWidget(QPushButton("删除", clicked=self._delete_template))
        gl2.addLayout(tbtns2)
        layout.addWidget(g2)
        self.refresh_template_list()

        g3 = QGroupBox("历史视频")
        hl = QVBoxLayout(g3)
        self._history_list = QListWidget()
        self._history_list.itemDoubleClicked.connect(self._on_history_double_clicked)
        hl.addWidget(self._history_list)
        hl.addWidget(QPushButton("清除历史", clicked=self._clear_history))
        layout.addWidget(g3, 1)

    # ---- 标签管理 ----

    def _gen_colors(self) -> dict:
        base = [
            QColor(220, 50, 50, 200), QColor(50, 180, 50, 200),
            QColor(50, 120, 220, 200), QColor(220, 160, 50, 200),
            QColor(180, 50, 180, 200), QColor(50, 180, 180, 200),
            QColor(220, 120, 50, 200), QColor(150, 50, 220, 200),
        ]
        return {l: base[i % len(base)] for i, l in enumerate(self._label_types)}

    def _on_tag_selected(self):
        items = self._tag_list.selectedItems()
        idx = self._tag_list.row(items[0]) if items else -1
        self.tag_selected.emit(idx)

    def _edit_tag(self):
        items = self._tag_list.selectedItems()
        if not items:
            return
        idx = self._tag_list.row(items[0])
        if not self._project or idx < 0:
            return
        regions = self._project.annotations.regions
        if idx >= len(regions):
            return
        r = regions[idx]
        rois = self._project.annotations.to_pixel_rois(
            self._project.source.width, self._project.source.height)
        if idx >= len(rois):
            return
        roi = rois[idx]
        dlg = QDialog(self)
        dlg.setWindowTitle("编辑区域")
        lo = QFormLayout(dlg)
        xe = QLineEdit(str(roi.x))
        ye = QLineEdit(str(roi.y))
        we = QLineEdit(str(roi.w))
        he = QLineEdit(str(roi.h))
        lo.addRow("X:", xe)
        lo.addRow("Y:", ye)
        lo.addRow("宽:", we)
        lo.addRow("高:", he)
        cb = QComboBox()
        cb.addItems(self._label_types)
        cb.setCurrentText(r.label)
        lo.addRow("标签:", cb)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        lo.addRow(bb)
        if dlg.exec() == QDialog.Accepted:
            try:
                x, y, w, h = int(xe.text()), int(ye.text()), int(we.text()), int(he.text())
                self._project.annotations.remove_region(r.id)
                self._project.annotations.add_region(
                    cb.currentText(), x, y, w, h,
                    self._project.source.width, self._project.source.height)
                self._project.auto_save_roi()
                self.refresh_tag_list()
                self.regions_changed.emit()
            except ValueError:
                QMessageBox.warning(self, "错误", "请输入有效整数")

    def _delete_tag(self):
        items = self._tag_list.selectedItems()
        if not items:
            return
        idx = self._tag_list.row(items[0])
        if not self._project:
            return
        regions = self._project.annotations.regions
        if 0 <= idx < len(regions):
            self._project.annotations.remove_region(regions[idx].id)
            self._project.auto_save_roi()
            self.refresh_tag_list()
            self.regions_changed.emit()

    def refresh_tag_list(self):
        self._tag_list.clear()
        if not self._project or self._project.source.width == 0:
            return
        w = self._project.source.width
        h = self._project.source.height
        for r, roi in zip(
            self._project.annotations.regions,
            self._project.annotations.to_pixel_rois(w, h),
        ):
            text = f"{r.label} [{r.id}] - ({roi.x},{roi.y},{roi.w},{roi.h})"
            item = QListWidgetItem(text)
            item.setForeground(self._label_colors.get(r.label, Qt.black))
            self._tag_list.addItem(item)

    # ---- ROI 模板 ----

    def refresh_template_list(self):
        self._tpl_list.clear()
        default = self._tpl_mgr.default_name
        for name in self._tpl_mgr.list_names():
            prefix = "★ " if name == default else "  "
            item = QListWidgetItem(f"{prefix}{name}")
            self._tpl_list.addItem(item)
        self._default_btn.setEnabled(self._tpl_list.count() > 0)

    def _on_template_double_clicked(self, item):
        name = item.text().lstrip("★ ")
        self.template_applied.emit(name)

    def _apply_template_from_selection(self):
        items = self._tpl_list.selectedItems()
        if not items:
            return
        name = items[0].text().lstrip("★ ")
        self.template_applied.emit(name)

    def _save_current_as_template(self):
        if not self._project:
            return
        ann = self._project.annotations
        if ann.region_count == 0:
            QMessageBox.warning(self, "提示", "没有可保存的标注区域")
            return
        w, h = self._project.source.width, self._project.source.height
        regions = []
        for r, roi in zip(ann.regions, ann.to_pixel_rois(w, h)):
            regions.append({
                "id": r.id,
                "label": r.label,
                "center_x": round((roi.x + roi.w / 2) / w, 6),
                "center_y": round((roi.y + roi.h / 2) / h, 6),
                "width": round(roi.w / w, 6),
                "height": round(roi.h / h, 6),
            })
        name, ok = QInputDialog.getText(self, "保存模板", "模板名称:")
        if ok and name:
            self._tpl_mgr.save(name, regions)
            self.refresh_template_list()

    def _set_default_template(self):
        items = self._tpl_list.selectedItems()
        if not items:
            QMessageBox.information(self, "提示", "请先选中一个模板")
            return
        name = items[0].text().lstrip("★ ")
        self._tpl_mgr.set_default(name)
        self.refresh_template_list()

    def _delete_template(self):
        items = self._tpl_list.selectedItems()
        if not items:
            return
        name = items[0].text().lstrip("★ ")
        reply = QMessageBox.question(
            self, "确认", f"删除模板「{name}」？",
            QMessageBox.Yes | QMessageBox.No)
        if reply == QMessageBox.Yes:
            self._tpl_mgr.delete(name)
            self.refresh_template_list()

    # ---- 历史视频 ----

    def add_history(self, path: str):
        self._add_to_history(path)

    def _add_to_history(self, path: str):
        for i in range(self._history_list.count()):
            if self._history_list.item(i).data(Qt.UserRole) == path:
                self._history_list.takeItem(i)
                break
        item = QListWidgetItem(os.path.basename(path))
        item.setData(Qt.UserRole, path)
        item.setToolTip(path)
        self._history_list.insertItem(0, item)
        while self._history_list.count() > 10:
            self._history_list.takeItem(10)
        self._save_history()

    def _on_history_double_clicked(self, item):
        path = item.data(Qt.UserRole)
        if os.path.exists(path):
            self.history_opened.emit(path)
        else:
            self._history_list.takeItem(self._history_list.row(item))
            self._save_history()

    def _clear_history(self):
        self._history_list.clear()
        self._save_history()

    def _save_history(self):
        QSettings("GameVideoEdit", "PeaceEliteHighlights").setValue(
            "video_history",
            json.dumps([self._history_list.item(i).data(Qt.UserRole)
                        for i in range(self._history_list.count())]))

    def _load_history(self):
        data = QSettings("GameVideoEdit", "PeaceEliteHighlights").value(
            "video_history", "")
        if data:
            try:
                for p in json.loads(data):
                    if os.path.exists(p):
                        item = QListWidgetItem(os.path.basename(p))
                        item.setData(Qt.UserRole, p)
                        item.setToolTip(p)
                        self._history_list.addItem(item)
            except Exception:
                pass
