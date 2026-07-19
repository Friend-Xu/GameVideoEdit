"""设置对话框 —— 左侧导航 + 右侧内容区。"""

import logging
import yaml

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFileDialog,
    QFormLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMessageBox, QPushButton, QScrollArea,
    QSpinBox, QStackedWidget, QVBoxLayout, QWidget,
)

from app.core.project import DetectionConfig
from app.utils.paths import config_dir

_log = logging.getLogger("app.ui.settings_dialog")


class SettingsDialog(QDialog):
    """识别参数设置对话框"""

    preset_changed = Signal(str)

    def __init__(self, detection: DetectionConfig, dark: bool = False,
                 parent=None, matcher=None, preset_callback=None,
                 platform: str = "mobile", active_preset: str = ""):
        super().__init__(parent)
        self._detection = detection
        self._dark = dark
        self._matcher = matcher
        self._preset_callback = preset_callback
        self._platform = platform
        self._active_preset = active_preset
        self._loaded_preset_file = ""
        self._player_name = ""
        self._teammate_names = ""
        self._rules_modified = False
        self._initial = self._snapshot()
        self.setWindowTitle("设置")
        self.setMinimumSize(640, 460)
        self.resize(680, 520)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self._setup_ui()
        self._apply_theme()

    def _snapshot(self) -> dict:
        d = self._detection
        return {
            "mode": d.mode, "interval_sec": d.interval_sec,
            "skip_frames": d.skip_frames,
            "post_detect_skip_sec": d.post_detect_skip_sec,
            "padding_before": d.padding_before,
            "padding_after": d.padding_after,
            "merge_gap": d.merge_gap,
            "num_threads": d.num_threads, "rotation": d.rotation,
            "pipeline_mode": d.pipeline_mode,
            "cpu_workers": d.cpu_workers, "gpu_workers": d.gpu_workers,
            "refine_boundaries": d.refine_boundaries,
            "refine_search_window": d.refine_search_window,
            "cell_divide": d.cell_divide,
            "cell_min_gap": d.cell_min_gap,
        }

    def _restore(self, snapshot: dict):
        d = self._detection
        for k, v in snapshot.items():
            setattr(d, k, v)
        self._sync_controls()

    # ---- UI ----

    def _setup_ui(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # 左侧导航
        self._nav = QListWidget()
        self._nav.setFixedWidth(140)
        self._nav.setObjectName("settingsNav")
        self._nav.addItems(["识别", "性能", "时间", "规则"])
        self._nav.setCurrentRow(0)
        root.addWidget(self._nav)

        # 右侧
        right = QVBoxLayout()
        right.setContentsMargins(28, 24, 24, 16)
        right.setSpacing(0)

        self._stack = QStackedWidget()
        self._stack.addWidget(self._build_page(
            "识别参数", self._build_recognition_form()))
        self._stack.addWidget(self._build_page(
            "性能设置", self._build_performance_form()))
        self._stack.addWidget(self._build_page(
            "时间参数", self._build_timing_form()))
        self._stack.addWidget(self._build_rules_page())
        self._nav.currentRowChanged.connect(self._stack.setCurrentIndex)
        right.addWidget(self._stack, 1)

        # 底部按钮
        right.addSpacing(16)
        bl = QHBoxLayout()
        bl.addStretch()
        btn_reset = QPushButton("恢复默认")
        btn_reset.setObjectName("settingsResetBtn")
        btn_reset.clicked.connect(self._reset_defaults)
        bl.addWidget(btn_reset)
        btn_save = QPushButton("保存")
        btn_save.setObjectName("settingsSaveBtn")
        btn_save.setDefault(True)
        btn_save.clicked.connect(self._save_and_accept)
        bl.addWidget(btn_save)
        btn_cancel = QPushButton("取消")
        btn_cancel.clicked.connect(self.reject)
        bl.addWidget(btn_cancel)
        right.addLayout(bl)

        root.addLayout(right, 1)
        self._sync_controls()
        self.rejected.connect(lambda: self._restore(self._initial))

    @staticmethod
    def _build_page(title: str, form: QFormLayout) -> QWidget:
        page = QWidget()
        ly = QVBoxLayout(page)
        ly.setContentsMargins(0, 0, 0, 0)
        ly.setSpacing(0)

        header = QLabel(title)
        header.setObjectName("settingsSectionTitle")
        ly.addWidget(header)
        ly.addSpacing(16)

        form.setSpacing(10)
        form.setContentsMargins(0, 0, 0, 0)
        ly.addLayout(form)
        ly.addStretch()
        return page

    # ---- 规则页面 ----

    def _build_rules_page(self) -> QWidget:
        page = QWidget()
        ly = QVBoxLayout(page)
        ly.setContentsMargins(0, 0, 0, 0)
        ly.setSpacing(0)

        header = QLabel("规则配置")
        header.setObjectName("settingsSectionTitle")
        ly.addWidget(header)
        ly.addSpacing(12)

        # 预设选择行
        preset_row = QHBoxLayout()
        preset_row.setSpacing(8)
        self._preset_combo = QComboBox()
        self._preset_combo.setMinimumWidth(200)
        self._preset_combo.currentTextChanged.connect(self._on_preset_selected)
        preset_row.addWidget(QLabel("预设:"))
        preset_row.addWidget(self._preset_combo, 1)
        btn_import = QPushButton("导入")
        btn_import.clicked.connect(self._import_preset)
        preset_row.addWidget(btn_import)
        btn_export = QPushButton("导出")
        btn_export.clicked.connect(self._export_preset)
        preset_row.addWidget(btn_export)
        ly.addLayout(preset_row)

        # PC 模式: 玩家名 & 队友名输入
        self._pc_name_group = QWidget()
        pc_name_layout = QVBoxLayout(self._pc_name_group)
        pc_name_layout.setContentsMargins(0, 8, 0, 0)
        pc_name_layout.setSpacing(6)
        name_row = QHBoxLayout()
        name_row.addWidget(QLabel("玩家名:"))
        self._player_name_edit = QLineEdit()
        self._player_name_edit.setPlaceholderText("输入游戏内昵称")
        name_row.addWidget(self._player_name_edit, 1)
        pc_name_layout.addLayout(name_row)
        tm_row = QHBoxLayout()
        tm_row.addWidget(QLabel("队友名:"))
        self._teammate_edit = QLineEdit()
        self._teammate_edit.setPlaceholderText("逗号分隔多个队友名 (可选)")
        tm_row.addWidget(self._teammate_edit, 1)
        pc_name_layout.addLayout(tm_row)
        self._pc_name_group.setVisible(self._platform == "pc")
        ly.addWidget(self._pc_name_group)
        ly.addSpacing(12)

        # 规则列表（可滚动）
        self._rule_list = QListWidget()
        self._rule_list.setMaximumHeight(160)
        self._rule_list.currentRowChanged.connect(self._on_rule_selected)
        ly.addWidget(QLabel("规则列表（勾选 = 启用）:"))
        ly.addWidget(self._rule_list)
        ly.addSpacing(8)

        btn_row = QHBoxLayout()
        btn_add = QPushButton("+ 添加规则")
        btn_add.clicked.connect(self._add_rule)
        btn_row.addWidget(btn_add)
        btn_del = QPushButton("删除选中")
        btn_del.clicked.connect(self._delete_rule)
        btn_row.addWidget(btn_del)
        btn_row.addStretch()
        ly.addLayout(btn_row)
        ly.addSpacing(12)

        # 规则编辑器
        ly.addWidget(QLabel("编辑选中规则:"))
        self._rule_form = QFormLayout()
        self._rule_form.setSpacing(8)
        self._rule_form.setContentsMargins(0, 8, 0, 0)

        self._edit_signal = QLineEdit()
        self._edit_signal.setPlaceholderText("如: 你的队友")
        self._edit_action = QComboBox()
        self._edit_action.addItems(["淘汰", "击倒", "killed", "knocked out"])
        self._edit_action.setEditable(True)
        self._edit_actor = QComboBox()
        self._edit_actor.addItems(["自己", "敌人", "队友"])
        self._edit_actor.setEditable(True)
        self._edit_strategy = QComboBox()
        self._edit_strategy.addItems(["精确匹配", "模糊匹配"])
        self._edit_threshold = QDoubleSpinBox()
        self._edit_threshold.setRange(0.5, 1.0)
        self._edit_threshold.setSingleStep(0.05)
        self._edit_threshold.setDecimals(2)

        self._rule_form.addRow("触发词", self._edit_signal)
        self._rule_form.addRow("动作", self._edit_action)
        self._rule_form.addRow("归属", self._edit_actor)
        self._rule_form.addRow("匹配策略", self._edit_strategy)
        self._rule_form.addRow("模糊阈值", self._edit_threshold)
        ly.addLayout(self._rule_form)

        btn_save_rule = QPushButton("保存规则")
        btn_save_rule.clicked.connect(self._save_rule)
        ly.addWidget(btn_save_rule)
        ly.addStretch()

        self._refresh_presets()
        return page

    def _refresh_presets(self):
        from app.core.presets import PresetManager
        pm = PresetManager()
        self._preset_combo.blockSignals(True)
        self._preset_combo.clear()
        presets = pm.list(self._platform)
        for p in presets:
            self._preset_combo.addItem(
                f"{p['name']} ({p['game']}, {p['language']})", p["file"])
        # Also show current custom config
        self._preset_combo.insertItem(0, "（当前配置）", None)
        self._preset_combo.setCurrentIndex(0)
        # 选中当前激活的预设
        if self._active_preset:
            for i in range(self._preset_combo.count()):
                if self._preset_combo.itemData(i) == self._active_preset:
                    self._preset_combo.setCurrentIndex(i)
                    self._loaded_preset_file = self._active_preset
                    break
        self._preset_combo.blockSignals(False)
        self._refresh_rule_list()

    def _refresh_rule_list(self):
        self._rule_list.clear()
        if not self._matcher or not self._matcher._rules_config:
            return
        for rule in self._matcher._rules_config:
            sid = rule.get("actor_signal", "?")
            act = rule.get("action", "?")
            strat = rule.get("match_strategy", "exact")
            thresh = rule.get("similarity_threshold", 0.85)
            label = f"{sid} → {act}"
            if strat == "fuzzy":
                label += f"  [模糊 {thresh:.0%}]"
            else:
                label += "  [精确]"
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, rule.get("id"))
            item.setCheckState(Qt.Checked if rule.get("enabled", True) else Qt.Unchecked)
            self._rule_list.addItem(item)

    def _on_preset_selected(self, name: str):
        idx = self._preset_combo.currentIndex()
        file_name = self._preset_combo.itemData(idx)
        if not file_name:
            self._rule_list.clear()
            self._loaded_preset_file = ""
            return
        from app.core.presets import PresetManager
        from app.core.keywords import KeywordMatcher
        pm = PresetManager()
        config = pm.load(file_name.replace(".yaml", ""))
        self._matcher = KeywordMatcher.from_dict(config)
        self._loaded_preset_file = file_name
        self._refresh_rule_list()

    def _on_rule_selected(self, row: int):
        if row < 0 or not self._matcher or row >= len(self._matcher._rules_config):
            return
        rule = self._matcher._rules_config[row]
        self._edit_signal.setText(rule.get("actor_signal", ""))
        self._edit_action.setCurrentText(rule.get("action", ""))
        self._edit_actor.setCurrentText(rule.get("actor", ""))
        strat = rule.get("match_strategy", "exact")
        self._edit_strategy.setCurrentIndex(0 if strat == "exact" else 1)
        self._edit_threshold.setValue(rule.get("similarity_threshold", 0.85))

    def _save_rule(self):
        row = self._rule_list.currentRow()
        if row < 0 or not self._matcher or row >= len(self._matcher._rules_config):
            return
        rule = self._matcher._rules_config[row]
        rule["actor_signal"] = self._edit_signal.text()
        rule["action"] = self._edit_action.currentText()
        rule["actor"] = self._edit_actor.currentText()
        rule["match_strategy"] = "exact" if self._edit_strategy.currentIndex() == 0 else "fuzzy"
        rule["similarity_threshold"] = self._edit_threshold.value()
        self._rules_modified = True
        self._refresh_rule_list()
        self._rule_list.setCurrentRow(row)

    def _add_rule(self):
        new_rule = {
            "id": f"rule_{len(self._matcher._rules_config) + 1}",
            "actor": "敌人", "actor_signal": "", "action": "淘汰",
            "match_strategy": "exact", "similarity_threshold": 0.85,
        }
        if not self._matcher:
            from app.core.keywords import KeywordMatcher
            self._matcher = KeywordMatcher.from_dict({"rules": []})
        self._matcher._rules_config.append(new_rule)
        self._rules_modified = True
        self._refresh_rule_list()
        self._rule_list.setCurrentRow(len(self._matcher._rules_config) - 1)

    def _delete_rule(self):
        row = self._rule_list.currentRow()
        if row < 0 or not self._matcher or row >= len(self._matcher._rules_config):
            return
        del self._matcher._rules_config[row]
        self._rules_modified = True
        self._refresh_rule_list()

    def _import_preset(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "导入规则预设", "", "JSON 文件 (*.json);;所有文件 (*)")
        if not path:
            return
        try:
            from app.core.presets import PresetManager
            from app.core.keywords import KeywordMatcher
            pm = PresetManager()
            name = pm.import_json(path)
            config = pm.load(name)
            self._matcher = KeywordMatcher.from_dict(config)
            self._rules_modified = True
            self._refresh_presets()
            self._preset_combo.setCurrentText(name)
        except Exception as e:
            QMessageBox.warning(self, "导入失败", str(e))

    def _export_preset(self):
        idx = self._preset_combo.currentIndex()
        file_name = self._preset_combo.itemData(idx)
        if not file_name:
            QMessageBox.warning(self, "导出失败", "请先选择一个预设")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "导出规则预设", file_name, "JSON 文件 (*.json)")
        if not path:
            return
        try:
            from app.core.presets import PresetManager
            pm = PresetManager()
            pm.export_json(file_name.replace(".yaml", ""), path)
        except Exception as e:
            QMessageBox.warning(self, "导出失败", str(e))

    # ---- 识别页面 ----

    def _build_recognition_form(self) -> QFormLayout:
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignLeft)

        self._mode_combo = QComboBox()
        self._mode_combo.addItems(["时间间隔", "帧间隔"])
        self._mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        form.addRow("识别模式", self._mode_combo)

        self._interval_spin = QDoubleSpinBox()
        self._interval_spin.setRange(0.1, 10.0)
        self._interval_spin.setSingleStep(0.1)
        self._interval_spin.setDecimals(1)
        self._interval_spin.setSuffix(" 秒")
        self._interval_spin.valueChanged.connect(
            lambda v: setattr(self._detection, 'interval_sec', v))
        self._interval_label = QLabel("采样间隔")
        form.addRow(self._interval_label, self._interval_spin)

        self._skip_spin = QSpinBox()
        self._skip_spin.setRange(1, 300)
        self._skip_spin.setSuffix(" 帧")
        self._skip_spin.setToolTip("每 N 帧采样一次，60 = 1 fps @ 60fps")
        self._skip_spin.valueChanged.connect(
            lambda v: setattr(self._detection, 'skip_frames', v))
        self._skip_label = QLabel("跳帧间隔")
        form.addRow(self._skip_label, self._skip_spin)

        self._post_detect = QDoubleSpinBox()
        self._post_detect.setRange(0.0, 10.0)
        self._post_detect.setSingleStep(0.1)
        self._post_detect.setDecimals(1)
        self._post_detect.setSuffix(" 秒")
        self._post_detect.setToolTip("检测命中后跳过的时间，避免重复")
        self._post_detect.valueChanged.connect(
            lambda v: setattr(self._detection, 'post_detect_skip_sec', v))
        form.addRow("命中跳秒", self._post_detect)

        self._rot_combo = QComboBox()
        self._rot_combo.addItems(["0°", "90°", "180°", "270°"])
        self._rot_combo.currentIndexChanged.connect(
            lambda i: setattr(self._detection, 'rotation', i * 90))
        form.addRow("视频旋转", self._rot_combo)

        return form

    # ---- 性能页面 ----

    def _build_performance_form(self) -> QFormLayout:
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignLeft)

        self._pipeline_combo = QComboBox()
        self._pipeline_combo.addItem("Pool（CPU / GPU 解耦）", "pool")
        self._pipeline_combo.addItem("Legacy（捆绑分段）", "legacy")
        self._pipeline_combo.currentIndexChanged.connect(self._on_pipeline_changed)
        form.addRow("流水线模式", self._pipeline_combo)

        self._num_threads = QSpinBox()
        self._num_threads.setRange(1, 16)
        self._num_threads.setToolTip(
            "Pool 模式下为并行段数，Legacy 模式下为 OCRWorker 数量")
        self._num_threads.valueChanged.connect(
            lambda v: setattr(self._detection, 'num_threads', v))
        form.addRow("并行数", self._num_threads)

        self._cpu_workers = QSpinBox()
        self._cpu_workers.setRange(1, 16)
        self._cpu_workers.setToolTip("CPU 解码线程数（仅 Pool 模式生效）")
        self._cpu_workers.valueChanged.connect(
            lambda v: setattr(self._detection, 'cpu_workers', v))
        form.addRow("CPU 线程", self._cpu_workers)

        self._gpu_workers = QSpinBox()
        self._gpu_workers.setRange(1, 8)
        self._gpu_workers.setToolTip("GPU OCR 线程数（仅 Pool 模式生效）")
        self._gpu_workers.valueChanged.connect(
            lambda v: setattr(self._detection, 'gpu_workers', v))
        form.addRow("GPU 线程", self._gpu_workers)

        self._gate_combo = QComboBox()
        self._gate_combo.addItem("像素门控", "pixel")
        self._gate_combo.addItem("神经门控", "neural")
        self._gate_combo.setToolTip("像素门控(<1ms): 白像素计数; 神经门控(5-10ms): EasyOCR CRAFT 检测")
        self._gate_combo.currentIndexChanged.connect(
            lambda i: setattr(self._detection, 'gate_mode',
                              self._gate_combo.itemData(i)))
        form.addRow("门控模式", self._gate_combo)

        return form

    # ---- 时间页面 ----

    def _build_timing_form(self) -> QFormLayout:
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignLeft)

        self._padding = QSpinBox()
        self._padding.setRange(1, 30)
        self._padding.setSuffix(" 秒")
        self._padding.setToolTip("剪辑片段前后预留时间")
        self._padding.valueChanged.connect(self._on_padding_changed)
        form.addRow("预留时间", self._padding)

        self._merge_gap = QSpinBox()
        self._merge_gap.setRange(1, 120)
        self._merge_gap.setSuffix(" 秒")
        self._merge_gap.setToolTip("相邻片段合并的最大时间间隔")
        self._merge_gap.valueChanged.connect(
            lambda v: setattr(self._detection, 'merge_gap', v))
        form.addRow("合并间隔", self._merge_gap)

        # ── 后处理参数 ──
        self._refine_window = QDoubleSpinBox()
        self._refine_window.setRange(0.5, 5.0)
        self._refine_window.setSingleStep(0.5)
        self._refine_window.setDecimals(1)
        self._refine_window.setSuffix(" 秒")
        self._refine_window.setToolTip("二分搜索的搜索范围")
        self._refine_window.valueChanged.connect(
            lambda v: setattr(self._detection, 'refine_search_window', v))
        form.addRow("搜索范围", self._refine_window)

        self._cell_divide_combo = QComboBox()
        self._cell_divide_combo.addItem("开", True)
        self._cell_divide_combo.addItem("关", False)
        self._cell_divide_combo.setToolTip("BinSeg 递归搜索：在大事件中分裂查找子事件")
        self._cell_divide_combo.currentIndexChanged.connect(
            lambda i: setattr(self._detection, 'cell_divide',
                              self._cell_divide_combo.itemData(i)))
        form.addRow("细胞分裂", self._cell_divide_combo)

        self._cell_min_gap = QDoubleSpinBox()
        self._cell_min_gap.setRange(0.5, 10.0)
        self._cell_min_gap.setSingleStep(0.5)
        self._cell_min_gap.setDecimals(1)
        self._cell_min_gap.setSuffix(" 秒")
        self._cell_min_gap.setToolTip("子事件的最小间隔（小于此值不再分裂）")
        self._cell_min_gap.valueChanged.connect(
            lambda v: setattr(self._detection, 'cell_min_gap', v))
        form.addRow("最小间隔", self._cell_min_gap)

        return form

    # ---- 交互 ----

    def _on_padding_changed(self, v):
        self._detection.padding_before = v
        self._detection.padding_after = v

    def _on_mode_changed(self, idx):
        is_time = idx == 0
        self._detection.mode = "time" if is_time else "frame"
        self._update_recognition_controls()

    def _on_pipeline_changed(self, idx):
        mode = self._pipeline_combo.itemData(idx)
        self._detection.pipeline_mode = mode
        is_pool = mode == "pool"
        self._cpu_workers.setEnabled(is_pool)
        self._gpu_workers.setEnabled(is_pool)
        self._num_threads.setEnabled(not is_pool)
        self._num_threads.setToolTip(
            "Legacy 模式下为 OCRWorker 分段数" if is_pool else
            "Pool 模式不分段，并行度由 CPU/GPU 线程数控制")

    def _update_recognition_controls(self):
        is_time = self._detection.mode == "time"
        self._interval_label.setVisible(is_time)
        self._interval_spin.setVisible(is_time)
        self._skip_label.setVisible(not is_time)
        self._skip_spin.setVisible(not is_time)

    # ---- 同步 ----

    def _sync_controls(self):
        d = self._detection
        is_time = d.mode == "time"
        is_pool = d.pipeline_mode == "pool"

        self._mode_combo.blockSignals(True)
        self._mode_combo.setCurrentIndex(0 if is_time else 1)
        self._mode_combo.blockSignals(False)

        self._interval_label.setVisible(is_time)
        self._interval_spin.blockSignals(True)
        self._interval_spin.setValue(d.interval_sec)
        self._interval_spin.setVisible(is_time)
        self._interval_spin.blockSignals(False)

        self._skip_label.setVisible(not is_time)
        self._skip_spin.blockSignals(True)
        self._skip_spin.setValue(d.skip_frames)
        self._skip_spin.setVisible(not is_time)
        self._skip_spin.blockSignals(False)

        self._post_detect.blockSignals(True)
        self._post_detect.setValue(d.post_detect_skip_sec)
        self._post_detect.blockSignals(False)

        self._rot_combo.blockSignals(True)
        self._rot_combo.setCurrentIndex(d.rotation // 90)
        self._rot_combo.blockSignals(False)

        pip_idx = 0 if is_pool else 1
        self._pipeline_combo.blockSignals(True)
        self._pipeline_combo.setCurrentIndex(pip_idx)
        self._pipeline_combo.blockSignals(False)

        self._num_threads.blockSignals(True)
        self._num_threads.setValue(d.num_threads)
        self._num_threads.setEnabled(not is_pool)
        self._num_threads.blockSignals(False)

        self._cpu_workers.blockSignals(True)
        self._cpu_workers.setValue(d.cpu_workers)
        self._cpu_workers.setEnabled(is_pool)
        self._cpu_workers.blockSignals(False)

        self._gpu_workers.blockSignals(True)
        self._gpu_workers.setValue(d.gpu_workers)
        self._gpu_workers.setEnabled(is_pool)
        self._gpu_workers.blockSignals(False)

        self._gate_combo.blockSignals(True)
        gate_idx = 0 if d.gate_mode == "pixel" else 1
        self._gate_combo.setCurrentIndex(gate_idx)
        self._gate_combo.blockSignals(False)

        self._padding.blockSignals(True)
        self._padding.setValue(d.padding_before)
        self._padding.blockSignals(False)

        self._merge_gap.blockSignals(True)
        self._merge_gap.setValue(d.merge_gap)
        self._merge_gap.blockSignals(False)

        # 后处理控件
        self._refine_window.blockSignals(True)
        self._refine_window.setValue(d.refine_search_window)
        self._refine_window.blockSignals(False)

        self._cell_divide_combo.blockSignals(True)
        self._cell_divide_combo.setCurrentIndex(0 if d.cell_divide else 1)
        self._cell_divide_combo.blockSignals(False)

        self._cell_min_gap.blockSignals(True)
        self._cell_min_gap.setValue(d.cell_min_gap)
        self._cell_min_gap.blockSignals(False)

    # ---- 操作 ----

    def _save_and_accept(self):
        self._write_defaults()
        if self._rules_modified and self._matcher:
            self._save_rules_to_yaml()
        self.accept()

    def _save_rules_to_yaml(self):
        """保存当前规则到 keywords.yaml 并通知主窗口重建 matcher。"""
        if not self._matcher:
            return
        from app.core.presets import PresetManager
        pm = PresetManager()
        config = self._matcher.to_dict()
        pm.save("current_rules", config)
        if self._preset_callback:
            pf = self._loaded_preset_file if not self._rules_modified else ""
            self._preset_callback(self._matcher.to_dict(), pf)

    def _reset_defaults(self):
        path = config_dir() / "default.yaml"
        if not path.exists():
            return
        with open(path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        d = cfg.get("detection", {})
        det = self._detection
        det.mode = d.get("mode", "time")
        det.interval_sec = float(d.get("interval_sec", 1.0))
        det.skip_frames = int(d.get("skip_frames", 3))
        det.post_detect_skip_sec = float(d.get("post_detect_skip_sec", 0.3))
        det.padding_before = float(d.get("padding_before", 10.0))
        det.padding_after = float(d.get("padding_after", 10.0))
        det.merge_gap = float(d.get("merge_gap", 30.0))
        det.num_threads = int(d.get("num_threads", 4))
        det.rotation = int(d.get("rotation", 0))
        det.pipeline_mode = d.get("pipeline_mode", "legacy")
        det.cpu_workers = int(d.get("cpu_workers", 6))
        det.gpu_workers = int(d.get("gpu_workers", 2))
        det.refine_boundaries = bool(d.get("refine_boundaries", False))
        det.refine_search_window = float(d.get("refine_search_window", 2.0))
        det.cell_divide = bool(d.get("cell_divide", False))
        det.cell_min_gap = float(d.get("cell_min_gap", 2.0))
        det.gate_mode = d.get("gate_mode", "neural")
        self._sync_controls()

    def _write_defaults(self):
        path = config_dir() / "default.yaml"
        cfg = {}
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
        det = self._detection
        cfg["detection"] = {
            "mode": det.mode,
            "interval_sec": det.interval_sec,
            "skip_frames": det.skip_frames,
            "post_detect_skip_sec": det.post_detect_skip_sec,
            "padding_before": det.padding_before,
            "padding_after": det.padding_after,
            "merge_gap": det.merge_gap,
            "num_threads": det.num_threads,
            "rotation": det.rotation,
            "pipeline_mode": det.pipeline_mode,
            "cpu_workers": det.cpu_workers,
            "gpu_workers": det.gpu_workers,
            "refine_boundaries": det.refine_boundaries,
            "refine_search_window": det.refine_search_window,
            "cell_divide": det.cell_divide,
            "cell_min_gap": det.cell_min_gap,
            "gate_mode": det.gate_mode,
        }
        with open(path, "w", encoding="utf-8") as f:
            yaml.dump(cfg, f, allow_unicode=True, default_flow_style=False,
                      sort_keys=False)

    # ---- 主题 ----

    def _apply_theme(self):
        self.setStyleSheet(_DARK_QSS if self._dark else _LIGHT_QSS)


# ── 浅色主题 ──────────────────────────────────────────

_LIGHT_QSS = """
QDialog { background: #fafafa; }

/* 导航 */
QListWidget#settingsNav {
    background: #f0f0f0; border: none; border-right: 1px solid #e4e4e4;
    outline: none; padding: 12px 0; font-size: 13px;
}
QListWidget#settingsNav::item {
    padding: 9px 20px; margin: 1px 10px; color: #666;
    border-radius: 6px; border: none;
}
QListWidget#settingsNav::item:selected {
    background: #e8e8ee; color: #2962ff; font-weight: 500;
}
QListWidget#settingsNav::item:hover:!selected {
    background: #e4e4e4;
}

/* 标题 */
QLabel#settingsSectionTitle {
    font-size: 18px; font-weight: 600; color: #222;
    padding: 0; margin: 0;
}

/* 表单标签 */
QFormLayout > QLabel {
    color: #777; font-size: 12px; min-width: 70px;
}

/* 输入控件 */
QComboBox, QSpinBox, QDoubleSpinBox {
    background: #fff; border: 1px solid #e0e0e0; border-radius: 6px;
    padding: 6px 12px; color: #333; min-height: 22px; min-width: 100px;
    font-size: 13px;
}
QComboBox { padding-right: 24px; }
QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {
    border-color: #2962ff;
}
QComboBox::drop-down {
    subcontrol-origin: padding; subcontrol-position: top right;
    width: 20px; border: none; border-left: 1px solid #eee;
    border-top-right-radius: 6px; border-bottom-right-radius: 6px;
}
QComboBox QAbstractItemView {
    background: #fff; border: 1px solid #e0e0e0; border-radius: 4px;
    selection-background-color: #e8e8ee; selection-color: #2962ff;
    outline: none; padding: 4px;
}

QSpinBox::up-button, QDoubleSpinBox::up-button {
    border: none; border-left: 1px solid #eee; width: 20px;
    border-top-right-radius: 6px;
}
QSpinBox::down-button, QDoubleSpinBox::down-button {
    border: none; border-left: 1px solid #eee; width: 20px;
    border-bottom-right-radius: 6px;
}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow { width: 8px; height: 8px; }
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow { width: 8px; height: 8px; }

/* 按钮 */
QPushButton {
    border: 1px solid #ddd; border-radius: 6px;
    padding: 7px 24px; background: #fff; color: #444; font-size: 13px;
}
QPushButton:hover { background: #f0f0f0; }
QPushButton#settingsSaveBtn {
    background: #2962ff; color: #fff; border: none; font-weight: 500;
}
QPushButton#settingsSaveBtn:hover { background: #1e4fdb; }
QPushButton#settingsResetBtn {
    color: #999; border: none; background: transparent;
}
QPushButton#settingsResetBtn:hover { color: #e53935; }
QPushButton:disabled { color: #bbb; }
"""

# ── 深色主题 ──────────────────────────────────────────

_DARK_QSS = """
QDialog { background: #1c1c1c; }

/* 导航 */
QListWidget#settingsNav {
    background: #222; border: none; border-right: 1px solid #2a2a2a;
    outline: none; padding: 12px 0; font-size: 13px;
}
QListWidget#settingsNav::item {
    padding: 9px 20px; margin: 1px 10px; color: #999;
    border-radius: 6px; border: none;
}
QListWidget#settingsNav::item:selected {
    background: #2a2a35; color: #7ba4ff; font-weight: 500;
}
QListWidget#settingsNav::item:hover:!selected {
    background: #2a2a2a;
}

/* 标题 */
QLabel#settingsSectionTitle {
    font-size: 18px; font-weight: 600; color: #eee;
    padding: 0; margin: 0;
}

/* 表单标签 */
QFormLayout > QLabel {
    color: #888; font-size: 12px; min-width: 70px;
}

/* 输入控件 */
QComboBox, QSpinBox, QDoubleSpinBox {
    background: #2a2a2a; border: 1px solid #3a3a3a; border-radius: 6px;
    padding: 6px 12px; color: #ddd; min-height: 22px; min-width: 100px;
    font-size: 13px;
}
QComboBox { padding-right: 24px; }
QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {
    border-color: #3d74ff;
}
QComboBox::drop-down {
    subcontrol-origin: padding; subcontrol-position: top right;
    width: 20px; border: none; border-left: 1px solid #333;
    border-top-right-radius: 6px; border-bottom-right-radius: 6px;
}
QComboBox QAbstractItemView {
    background: #2a2a2a; border: 1px solid #3a3a3a; border-radius: 4px;
    selection-background-color: #2a2a35; selection-color: #7ba4ff;
    outline: none; padding: 4px;
}

QSpinBox::up-button, QDoubleSpinBox::up-button {
    border: none; border-left: 1px solid #333; width: 20px;
    border-top-right-radius: 6px;
}
QSpinBox::down-button, QDoubleSpinBox::down-button {
    border: none; border-left: 1px solid #333; width: 20px;
    border-bottom-right-radius: 6px;
}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow { width: 8px; height: 8px; }
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow { width: 8px; height: 8px; }

/* 按钮 */
QPushButton {
    border: 1px solid #3a3a3a; border-radius: 6px;
    padding: 7px 24px; background: #2a2a2a; color: #bbb; font-size: 13px;
}
QPushButton:hover { background: #333; }
QPushButton#settingsSaveBtn {
    background: #2962ff; color: #fff; border: none; font-weight: 500;
}
QPushButton#settingsSaveBtn:hover { background: #3d74ff; }
QPushButton#settingsResetBtn {
    color: #777; border: none; background: transparent;
}
QPushButton#settingsResetBtn:hover { color: #ff5252; }
QPushButton:disabled { color: #555; }
"""
