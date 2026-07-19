"""主窗口 —— 集成视频播放器、OCR、导出。"""

import json
import logging
import os
from pathlib import Path

from PySide6.QtCore import QEvent, Qt, QSettings, QThread, QTimer, Signal
from PySide6.QtWidgets import QApplication
from PySide6.QtWidgets import (
    QFileDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMainWindow, QMessageBox, QProgressBar, QPushButton, QSpinBox, QSplitter,
    QTextEdit, QVBoxLayout, QWidget, QComboBox, QDoubleSpinBox,
)

from app.core.detector import TimeRange, DetectionEngine
from app.core.exporter import ExportConfig
from app.core.keywords import KeywordMatcher
from app.core.project import Project, ClipResult
from app.ui.video_player import VideoPlayerWidget
from app.ui.side_panel import SidePanelWidget
from app.ui.settings_dialog import SettingsDialog
from app.workers.ocr_worker import OCRWorker
from app.workers.export_worker import ExportWorker


class MainWindow(QMainWindow):
    """应用主窗口 —— 药药的剪辑工具"""

    log = Signal(str, int)
    _log = logging.getLogger("app.ui.main_window")

    def __init__(self):
        super().__init__()
        self.setWindowTitle("药药的剪辑工具"); self.resize(1200, 800)
        self._dark = False
        self._matcher: KeywordMatcher | None = None
        self._project = Project()
        self._ocr_threads: list[OCRWorker] = []
        self._thread_results: dict[int, list] = {}
        self._thread_reports: dict[int, object] = {}
        self._completed = 0
        self._ocr_detecting = False
        self._detect_hit_count = 0
        self._worker: ExportWorker | None = None
        self._setup_ui(); self._apply_theme()
        from app.utils.logger import attach_qt_bridge
        attach_qt_bridge(self.log)
        QApplication.instance().installEventFilter(self)
        QTimer.singleShot(100, self._load_matcher)

    def _setup_ui(self):
        central = QWidget(); self.setCentralWidget(central)
        ml = QVBoxLayout(central); ml.setContentsMargins(10, 10, 10, 10); ml.setSpacing(8)

        # ---- 顶部工具栏 ----
        tl = QHBoxLayout()
        btn_open = QPushButton("打开视频"); btn_open.setProperty("cssClass", "primary")
        btn_open.clicked.connect(self._open_video); tl.addWidget(btn_open)
        title = QLabel("药药的剪辑工具"); title.setObjectName("titleLabel")
        tl.addWidget(title)
        # 平台切换 (手机/PC)
        self._platform_mobile_btn = QPushButton("手机")
        self._platform_mobile_btn.setCheckable(True); self._platform_mobile_btn.setChecked(True)
        self._platform_mobile_btn.setStyleSheet(
            "QPushButton { padding: 4px 12px; border: 1px solid #9C27B0; "
            "border-radius: 4px 0 0 4px; color: white; background: #9C27B0; font-weight: bold; }"
            "QPushButton:checked { background: #9C27B0; }"
            "QPushButton:!checked { background: transparent; color: #aaa; }")
        self._platform_mobile_btn.clicked.connect(lambda: self._on_platform_changed("mobile"))
        tl.addWidget(self._platform_mobile_btn)
        self._platform_pc_btn = QPushButton("PC")
        self._platform_pc_btn.setCheckable(True)
        self._platform_pc_btn.setStyleSheet(
            "QPushButton { padding: 4px 12px; border: 1px solid #9C27B0; "
            "border-radius: 0 4px 4px 0; color: #aaa; background: transparent; font-weight: bold; }"
            "QPushButton:checked { background: #9C27B0; color: white; }"
            "QPushButton:!checked { background: transparent; color: #aaa; }")
        self._platform_pc_btn.clicked.connect(lambda: self._on_platform_changed("pc"))
        tl.addWidget(self._platform_pc_btn)
        tl.addWidget(QLabel("  语言与正则预设"))
        self._toolbar_preset_combo = QComboBox()
        self._toolbar_preset_combo.setMinimumWidth(180)
        self._toolbar_preset_combo.currentIndexChanged.connect(self._on_toolbar_preset_changed)
        tl.addWidget(self._toolbar_preset_combo)
        tl.addStretch()
        self._settings_btn = QPushButton("⚙ 设置")
        self._settings_btn.clicked.connect(self._open_settings)
        tl.addWidget(self._settings_btn)
        self._detect_btn = QPushButton("开始识别"); self._detect_btn.setStyleSheet(
            "QPushButton { background-color: #9C27B0; color: white; font-weight: bold; "
            "border-radius: 5px; padding: 8px 18px; } QPushButton:hover { background-color: #7B1FA2; }"
            "QPushButton:disabled { background-color: #666; }")
        self._detect_btn.clicked.connect(self._on_detect_btn)
        self._detect_btn.setEnabled(False); tl.addWidget(self._detect_btn)
        self._theme_btn = QPushButton("深色模式"); self._theme_btn.setProperty("cssClass", "primary")
        self._theme_btn.clicked.connect(self._toggle_theme); tl.addWidget(self._theme_btn)
        ml.addLayout(tl)

        # ---- 中央分栏 ----
        splitter = QSplitter(Qt.Horizontal)

        self._side_panel = SidePanelWidget()
        self._side_panel.set_project(self._project)
        self._side_panel.setMinimumWidth(340)
        splitter.addWidget(self._side_panel)

        self._player = VideoPlayerWidget()
        self._player.set_project(self._project)
        self._player.setMinimumWidth(320)
        self._player.frame_changed.connect(self._on_frame_changed)
        self._player.timeline.clipSelected.connect(self._on_timeline_clip_selected)
        splitter.addWidget(self._player)

        # 右侧: 结果画廊 + 导出
        right = QWidget(); right.setMinimumWidth(300)
        rl = QVBoxLayout(right); rl.setContentsMargins(5, 5, 5, 5); rl.setSpacing(8)
        self._result_label = QLabel("识别结果"); self._result_label.setFixedHeight(22)
        rl.addWidget(self._result_label)
        self._filter_bar = self._build_filter_bar()
        self._filter_bar.setVisible(False); rl.addWidget(self._filter_bar)
        self._progress_widget = self._build_progress_widget()
        rl.addWidget(self._progress_widget)
        self._progress_widget.setVisible(False)
        self._result_list = QListWidget()
        self._result_list.itemDoubleClicked.connect(self._on_result_double_clicked)
        self._result_list.itemChanged.connect(lambda: self._update_export_buttons())
        self._result_list.currentRowChanged.connect(self._player.select_timeline_clip)
        rl.addWidget(self._result_list, 1)

        # undo / redo
        ul = QHBoxLayout()
        self._undo_btn = QPushButton("↩ 撤销"); self._undo_btn.clicked.connect(self._undo_result)
        self._undo_btn.setEnabled(False); ul.addWidget(self._undo_btn)
        self._redo_btn = QPushButton("↪ 重做"); self._redo_btn.clicked.connect(self._redo_result)
        self._redo_btn.setEnabled(False); ul.addWidget(self._redo_btn)
        ul.addStretch()
        rl.addLayout(ul)

        # 导出按钮
        el = QHBoxLayout()
        self._export_selected_btn = QPushButton("导出选中(0)"); self._export_selected_btn.setStyleSheet(
            "QPushButton { background-color: #FF5722; color: white; font-weight: bold; "
            "border-radius: 5px; padding: 8px 18px; } QPushButton:hover { background-color: #E64A19; }"
            "QPushButton:disabled { background-color: #666; color: #CCC; }")
        self._export_selected_btn.clicked.connect(lambda: self._start_export(selected_only=True))
        self._export_selected_btn.setEnabled(False); el.addWidget(self._export_selected_btn)
        self._export_all_btn = QPushButton("导出全部(0)"); self._export_all_btn.setStyleSheet(
            "QPushButton { background-color: #FF5722; color: white; font-weight: bold; "
            "border-radius: 5px; padding: 8px 18px; } QPushButton:hover { background-color: #E64A19; }"
            "QPushButton:disabled { background-color: #666; color: #CCC; }")
        self._export_all_btn.clicked.connect(lambda: self._start_export(selected_only=False))
        self._export_all_btn.setEnabled(False); el.addWidget(self._export_all_btn)
        rl.addLayout(el)

        pline = QHBoxLayout()
        pline.addWidget(QLabel("输出:"))
        self._export_path_label = QLabel("(未设置)")
        self._export_path_label.setStyleSheet("color: #888;")
        pline.addWidget(self._export_path_label, 1)
        btn_path = QPushButton("另存为")
        btn_path.clicked.connect(self._choose_export_path); pline.addWidget(btn_path)
        rl.addLayout(pline)

        # 日志区域
        self._log_view = QTextEdit()
        self._log_view.setReadOnly(True)
        self._log_view.document().setMaximumBlockCount(2000)
        self._log_view.setMinimumHeight(60)
        self._log_view.setPlaceholderText("检测日志...")
        self.log.connect(self._append_log)
        rl.addWidget(self._log_view, 1)

        splitter.addWidget(right); splitter.setSizes([340, 640, 320])
        for i in range(3):
            splitter.setCollapsible(i, False)
        ml.addWidget(splitter, 1)
        self.statusBar().showMessage("就绪")
        self._wire_side_panel()
        self._init_detection_config()

    def _build_filter_bar(self) -> QWidget:
        w = QWidget(); l = QHBoxLayout(w); l.setContentsMargins(0, 0, 0, 0); l.setSpacing(4)
        self._filter_btns: dict[str, QPushButton] = {}
        self._filter_all_btn = QPushButton("全部"); self._filter_all_btn.setCheckable(True)
        self._filter_all_btn.setChecked(True)
        self._filter_all_btn.clicked.connect(lambda: self._filter_by_actor(None))
        l.addWidget(self._filter_all_btn)
        for actor in ["自己", "队友", "敌人"]:
            btn = QPushButton(actor); btn.setCheckable(True)
            btn.clicked.connect(lambda checked, a=actor: self._filter_by_actor(a))
            self._filter_btns[actor] = btn; l.addWidget(btn)
        l.addStretch()
        return w

    def _filter_by_actor(self, actor: str | None):
        self._filter_all_btn.setChecked(actor is None)
        for a, btn in self._filter_btns.items():
            btn.setChecked(a == actor)
        for i in range(self._result_list.count()):
            item = self._result_list.item(i)
            if actor is None:
                item.setHidden(False)
            else:
                has_actor = actor in item.text()
                item.setHidden(not has_actor)
        self._update_export_buttons()

    def _build_progress_widget(self) -> QWidget:
        w = QWidget(); l = QVBoxLayout(w); l.setContentsMargins(0, 0, 0, 0); l.setSpacing(6)
        self._total_bar = QProgressBar(); self._total_bar.setRange(0, 100); self._total_bar.setFixedHeight(22)
        l.addWidget(self._total_bar)
        self._gap_bar = QProgressBar(); self._gap_bar.setRange(0, 100); self._gap_bar.setFixedHeight(18)
        self._gap_bar.setFormat("精确边界搜索: %p%")
        self._gap_bar.setVisible(False)
        l.addWidget(self._gap_bar)
        self._thread_bars: list[QProgressBar] = []
        self._thread_container = QVBoxLayout(); l.addLayout(self._thread_container)
        l.addStretch()
        return w

    def _wire_side_panel(self):
        sp = self._side_panel
        vp = self._player

        sp.regions_changed.connect(vp.refresh_regions)
        sp.tag_selected.connect(vp.set_selected_region)
        sp.label_colors_changed.connect(vp.set_label_colors)
        sp.history_opened.connect(self._open_video_from_history)
        sp.template_applied.connect(self._on_template_applied)

        vp.regions_changed.connect(sp.refresh_tag_list)

        vp.set_label_colors(sp.label_colors())
        vp.set_current_label(sp.current_label)
        sp.label_changed.connect(vp.set_current_label)

    def _on_template_applied(self, name: str):
        from app.core.roi_templates import ROITemplateManager
        tmpl = ROITemplateManager().get(name)
        if tmpl and tmpl.regions:
            self._project.annotations.replace_regions(tmpl.regions)
            self._project.auto_save_roi()
            self._player.refresh_regions()
            self._side_panel.refresh_tag_list()

    def _open_video_from_history(self, path: str):
        if os.path.exists(path):
            self._player.open_video_file(path)
            self._detect_btn.setEnabled(True)
            self._on_video_opened(path)

    def _should_intercept_key(self) -> bool:
        """文本输入控件有焦点时不拦截快捷键，避免干扰打字。"""
        fw = self.focusWidget()
        if fw is None:
            return True
        return not isinstance(fw, (QLineEdit, QSpinBox, QDoubleSpinBox, QTextEdit))

    def eventFilter(self, obj, event):
        if event.type() == QEvent.KeyPress:
            key = event.key()
            if key in (Qt.Key_Space, Qt.Key_Left, Qt.Key_Right):
                if self._player._player.is_open and self._should_intercept_key():
                    if key == Qt.Key_Space:
                        self._player._toggle_play()
                    elif key == Qt.Key_Left:
                        self._player._prev_frame()
                    elif key == Qt.Key_Right:
                        self._player._next_frame()
                    return True  # 消费事件，不传给焦点控件
        return super().eventFilter(obj, event)

    def _toggle_theme(self):
        self._dark = not self._dark; self._apply_theme()

    def _apply_theme(self):
        qss_file = Path(__file__).parent / "styles" / ("dark.qss" if self._dark else "light.qss")
        if qss_file.exists():
            with open(qss_file, "r", encoding="utf-8") as f: self.setStyleSheet(f.read())
        self._theme_btn.setText("浅色模式" if self._dark else "深色模式")

    # ---- 参数加载 & 设置 ----

    def _load_detection_defaults(self) -> dict:
        import yaml
        from app.utils.paths import config_dir
        yaml_path = config_dir() / "default.yaml"
        if yaml_path.exists():
            try:
                with open(yaml_path, "r", encoding="utf-8") as f:
                    cfg = yaml.safe_load(f) or {}
                return cfg.get("detection", {})
            except Exception:
                pass
        return {}

    def _init_detection_config(self):
        """从 default.yaml 加载检测配置到 Project.detection"""
        d = self._load_detection_defaults()
        det = self._project.detection
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
        det.cpu_workers = int(d.get("cpu_workers", 3))
        det.gpu_workers = int(d.get("gpu_workers", 2))
        det.refine_boundaries = bool(d.get("refine_boundaries", False))
        det.refine_search_window = float(d.get("refine_search_window", 2.0))
        det.cell_divide = bool(d.get("cell_divide", False))
        det.cell_min_gap = float(d.get("cell_min_gap", 2.0))
        det.gate_mode = d.get("gate_mode", "neural")
        det.ocr_engine = d.get("ocr_engine", "rapidocr")

    def _on_platform_changed(self, platform: str):
        """平台切换：Project 自动隔离状态，这里只刷新 UI。"""
        if self._project.platform == platform:
            return
        self._project.platform = platform
        self._platform_mobile_btn.setChecked(platform == "mobile")
        self._platform_pc_btn.setChecked(platform == "pc")
        # 刷新预设下拉框 + 加载当前平台的 matcher
        self._refresh_toolbar_presets(platform)
        self._load_matcher_for_platform(platform)
        self._sync_toolbar_preset_to_file(self._project.preset_file)
        # 刷新 ROI 标注（加载新平台默认模板）
        self._project._auto_load_roi(platform)
        self._player.refresh_regions()
        # 刷新侧边栏（标签 + 模板列表）
        self._side_panel.on_platform_changed(platform)
        # 清空旧平台的识别结果展示
        self._result_list.clear()
        self._filter_bar.setVisible(False)
        self._result_label.setText("识别结果")
        self.statusBar().showMessage(
            f"已切换到{'PC' if platform == 'pc' else '手机'}模式", 3000)

    def _on_preset_changed(self, config: dict, preset_file: str = ""):
        """预设改变后重建 matcher 并刷新结果。"""
        from app.core.keywords import KeywordMatcher
        self._matcher = KeywordMatcher.from_dict(config)
        if preset_file:
            self._project.preset_file = preset_file
            settings = QSettings("GameVideoEdit", "PeaceEliteHighlights")
            settings.setValue(f"preset/{self._project.platform}", preset_file)
        else:
            self._project.preset_file = ""
        self._sync_toolbar_preset_to_file(preset_file)
        self._log.info("规则预设已更新 (%d 条规则)", len(config.get("rules", [])))

    def _refresh_toolbar_presets(self, platform: str):
        """按平台填充工具栏预设下拉框。"""
        from app.core.presets import PresetManager
        pm = PresetManager()
        presets = pm.list(platform)
        self._toolbar_preset_combo.blockSignals(True)
        self._toolbar_preset_combo.clear()
        self._toolbar_preset_combo.addItem("（当前配置）", None)
        for p in presets:
            display = f"{p['name']} ({p['language']})"
            self._toolbar_preset_combo.addItem(display, p["file"])
        self._toolbar_preset_combo.blockSignals(False)

    def _sync_toolbar_preset_to_file(self, preset_file: str):
        """同步工具栏下拉框选中项到指定预设文件名。"""
        self._toolbar_preset_combo.blockSignals(True)
        if not preset_file:
            self._toolbar_preset_combo.setCurrentIndex(0)
        else:
            for i in range(self._toolbar_preset_combo.count()):
                if self._toolbar_preset_combo.itemData(i) == preset_file:
                    self._toolbar_preset_combo.setCurrentIndex(i)
                    break
            else:
                self._toolbar_preset_combo.setCurrentIndex(0)
        self._toolbar_preset_combo.blockSignals(False)

    def _on_toolbar_preset_changed(self, index: int):
        """工具栏预设下拉框选择即加载。"""
        if index < 0:
            return
        file_name = self._toolbar_preset_combo.itemData(index)
        platform = self._project.platform
        settings = QSettings("GameVideoEdit", "PeaceEliteHighlights")
        if not file_name:
            settings.setValue(f"preset/{platform}", "")
            return
        from app.core.presets import PresetManager
        from app.core.keywords import KeywordMatcher
        try:
            pm = PresetManager()
            config = pm.load(file_name.replace(".yaml", ""))
            self._matcher = KeywordMatcher.from_dict(config)
            self._project.preset_file = file_name
            settings.setValue(f"preset/{platform}", file_name)
            self._log.info("已加载预设: %s", file_name)
        except FileNotFoundError:
            self._log.warning("预设文件不存在: %s", file_name)
            settings.setValue(f"preset/{platform}", "")
            self._sync_toolbar_preset_to_file("")

    def _open_settings(self):
        old_pb = self._project.detection.padding_before
        old_pa = self._project.detection.padding_after
        dlg = SettingsDialog(self._project.detection, self._dark, self,
                             matcher=self._matcher,
                             preset_callback=self._on_preset_changed,
                             platform=self._project.platform,
                             active_preset=self._project.preset_file)
        if dlg.exec() == SettingsDialog.Accepted:
            new_pb = self._project.detection.padding_before
            new_pa = self._project.detection.padding_after
            if new_pb != old_pb or new_pa != old_pa:
                self._project.recompute_padding(new_pb, new_pa)
                self._show_results()
            self.statusBar().showMessage("设置已保存", 3000)

    # ---- OCR 识别 ----

    def _validate_roi_for_platform(self, ann) -> bool:
        """校验当前平台的 ROI 标签是否齐全。"""
        labels = {r.label for r in ann.regions}
        if self._project.platform == "pc":
            required = {"个人击杀", "队友击杀"}
            missing = required - labels
            if missing:
                QMessageBox.warning(self, "ROI 不完整",
                    f"PC 模式需要以下 ROI:\n{', '.join(required)}\n\n"
                    f"缺少: {', '.join(missing)}\n\n请切换到手机模式或框选全部 ROI 区域。")
                return False
        else:
            if "击杀信息" not in labels:
                QMessageBox.warning(self, "ROI 不完整",
                    "手机模式需要 \"击杀信息\" ROI。\n\n请框选击杀信息区域或切换到 PC 模式。")
                return False
        return True

    # ---- OCR 识别 ----

    def _on_detect_btn(self):
        if self._ocr_detecting:
            self._cancel_detection()
        else:
            self._start_detection()

    def _start_detection(self):
        self._log.debug("_start_detection 入口")
        if not self._project.source.path or not self._matcher:
            self._log.warning("_start_detection 退出: source.path=%s, matcher=%s",
                             self._project.source.path, self._matcher)
            return
        ann = self._project.annotations
        if ann.region_count == 0:
            self._project._auto_load_roi()
        if ann.region_count == 0:
            QMessageBox.warning(self, "缺少 ROI", "请先在侧边栏设置 ROI 检测区域（击杀信息等）")
            self._log.warning("_start_detection 退出: 无 ROI")
            return
        # 平台校验: PC 需要 "个人击杀" + "队友击杀", Mobile 需要 "击杀信息"
        if not self._validate_roi_for_platform(ann):
            return
        self._project.auto_save_roi()
        self._log.info("检测开始: %d 个 ROI 区域", ann.region_count)
        self._ocr_detecting = True
        self._detect_btn.setText("取消识别")
        self._export_selected_btn.setEnabled(False)
        self._export_all_btn.setEnabled(False)
        self._result_list.clear(); self._thread_results.clear(); self._thread_reports.clear(); self._completed = 0
        self._ocr_threads.clear()
        self._detect_hit_count = 0
        self._result_label.setText("识别进度")
        self._progress_widget.setVisible(True)
        self._total_bar.setValue(0)
        for b in self._thread_bars:
            self._thread_container.removeWidget(b)
            b.deleteLater()
        self._thread_bars.clear()

        from app.core.detector import OCRDetector, DetectionPipeline
        try:
            self._log.debug("加载 OCRDetector...")
            detector = OCRDetector(gpu=True,
                                   engine=self._project.detection.ocr_engine)
            import numpy as np
            detector.detect(np.zeros((64, 64, 3), dtype=np.uint8))
            self._log.debug("OCRDetector 加载完成")
        except Exception as e:
            self._log.error("模型加载失败: %s", e)
            QMessageBox.critical(self, "模型加载失败", str(e))
            self._reset_detect_state()
            return

        from app.core.player import VideoPlayer
        player = VideoPlayer()
        try:
            info = player.open(self._project.source.path)
        except Exception as e:
            self._log.error("打开视频失败: %s", e)
            QMessageBox.critical(self, "错误", str(e))
            self._reset_detect_state()
            return
        finally:
            player.close()

        total = info.total_frames; n_threads = self._project.detection.num_threads
        fps = info.fps
        pipeline_mode = getattr(self._project.detection, 'pipeline_mode', 'legacy')
        self._log.debug("视频: %s, %dx%d, %.2ffps, %d frames, 模式=%s, 线程=%d",
                        self._project.source.path, info.width, info.height,
                        fps, total, pipeline_mode, n_threads)

        bar = QProgressBar(); bar.setRange(0, 1000); bar.setValue(0); bar.setFixedHeight(18)
        self._thread_bars.append(bar)
        self._thread_container.addWidget(bar)

        if pipeline_mode == "pool":
            self._log.debug("进入 pool 模式")
            bar.setFormat("扫描进度: %v/%m")
            self._run_detection_pipeline(detector, info, bar)
        else:
            bar.setFormat("线程 1: %p%")
            from app.workers.ocr_worker import OCRWorker
            frames_per = total // n_threads
            for i in range(n_threads):
                sf = i * frames_per
                ef = (i + 1) * frames_per - 1 if i < n_threads - 1 else total - 1
                worker = OCRWorker(
                    i, self._project.source.path, self._project.annotations,
                    self._matcher, **self._project.detection.to_worker_kwargs(),
                    start_frame=sf, end_frame=ef,
                )
                worker.progress.connect(self._on_ocr_progress)
                worker.detected.connect(self._on_ocr_detected)
                worker.raw_text.connect(self._on_raw_ocr_text)
                worker.error.connect(self._on_ocr_error)
                worker.finished.connect(self._on_ocr_thread_done)
                self._ocr_threads.append(worker)
                if i > 0:
                    b2 = QProgressBar(); b2.setRange(0, 100); b2.setValue(0); b2.setFixedHeight(18)
                    b2.setFormat(f"线程 {i + 1}: %p%")
                    self._thread_bars.append(b2)
                    self._thread_container.addWidget(b2)
            self.statusBar().showMessage(f"启动 {n_threads} 个识别线程...")
            for w in self._ocr_threads:
                w.start()

    class _PoolDetectWorker(QThread):
        """后台线程执行 DetectionPipeline，避免阻塞 GUI。"""
        progress = Signal(float)
        gap_progress = Signal(float)
        detected = Signal(float, str)
        raw_text = Signal(float, str, str)
        finished = Signal(list, object)
        error = Signal(str)

        def __init__(self, matcher, detector, video_path, annotations, total_frames,
                     config, parent=None):
            super().__init__(parent)
            self._matcher = matcher
            self._detector = detector
            self._video_path = video_path
            self._annotations = annotations
            self._total_frames = total_frames
            self._config = config
            self._cancel_flag = False

        def run(self):
            from app.core.detector import DetectionPipeline
            try:
                pipeline = DetectionPipeline(
                    self._matcher, self._detector,
                    cpu_workers=getattr(self._config, 'cpu_workers', 3),
                    gpu_workers=getattr(self._config, 'gpu_workers', 2),
                    padding_before=self._config.padding_before,
                    padding_after=self._config.padding_after,
                    allowed_actors=self._config.allowed_actors,
                    skip_frames=self._config.skip_frames,
                    mode=self._config.mode,
                    interval_sec=self._config.interval_sec,
                    gate_mode=self._config.gate_mode,
                    refine_boundaries=self._config.refine_boundaries,
                    refine_search_window=self._config.refine_search_window,
                    cell_divide=self._config.cell_divide,
                    cell_min_gap=self._config.cell_min_gap,
                )
                time_ranges, report = pipeline.run_full(
                    video_path=self._video_path,
                    annotations=self._annotations,
                    start_frame=0, end_frame=self._total_frames - 1,
                    progress_cb=lambda pct: self.progress.emit(pct),
                    detected_cb=lambda ts, t: self.detected.emit(ts, t),
                    raw_ocr_cb=lambda ts, t, l: self.raw_text.emit(ts, t, l),
                    cancel_check=lambda: self._cancel_flag,
                    gap_progress_cb=lambda pct: self.gap_progress.emit(pct),
                )
                self.finished.emit(time_ranges, report)
            except Exception as e:
                import traceback
                self.error.emit(f"{e}\n{traceback.format_exc()}")

        def cancel(self):
            self._cancel_flag = True

    def _run_detection_pipeline(self, detector, info, bar):
        """在后台线程运行 DetectionPipeline，通过 signal 更新进度。"""
        self._log.debug("_run_detection_pipeline 入口")
        self._cancel_flag = False
        self._pool_worker = self._PoolDetectWorker(
            self._matcher, detector,
            self._project.source.path, self._project.annotations,
            info.total_frames, self._project.detection, self,
        )
        self._pool_worker.progress.connect(lambda pct: bar.setValue(int(pct * 10)))
        if self._project.detection.cell_divide:
            self._gap_bar.setVisible(True)
            self._gap_bar.setValue(0)
            self._pool_worker.gap_progress.connect(lambda pct: self._gap_bar.setValue(int(pct)))
        self._pool_worker.detected.connect(self._on_ocr_detected)
        self._pool_worker.raw_text.connect(self._on_raw_ocr_text)
        self._pool_worker.finished.connect(self._on_pipeline_done)
        self._pool_worker.error.connect(self._on_pipeline_error)
        self.statusBar().showMessage("启动 Pool 模式识别...")
        self._pool_worker.start()

    def _on_pipeline_done(self, time_ranges, report):
        self._log.debug("pipeline 完成: %d 个 time_ranges", len(time_ranges))
        all_clips = []
        for r in time_ranges:
            if isinstance(r, tuple):
                all_clips.append(ClipResult(
                    start_sec=r[0], end_sec=r[1],
                    action=r[2] if len(r) > 2 else "",
                    actor=r[3] if len(r) > 3 else "",
                    pattern_id=r[4] if len(r) > 4 else "",
                    source=r[5] if len(r) > 5 else "text",
                    raw_start_sec=r[6] if len(r) > 6 else 0.0,
                    raw_end_sec=r[7] if len(r) > 7 else 0.0,
                    raw_text=r[8] if len(r) > 8 else "",
                    confidence=r[9] if len(r) > 9 else 1.0,
                    match_strategy=r[10] if len(r) > 10 else "exact"))
            else:
                all_clips.append(ClipResult(
                    start_sec=r.start_sec, end_sec=r.end_sec,
                    raw_start_sec=getattr(r, 'raw_start_sec', r.start_sec),
                    raw_end_sec=getattr(r, 'raw_end_sec', r.end_sec),
                    action=r.action, actor=r.actor,
                    pattern_id=r.pattern_id, source=r.source,
                    raw_text=getattr(r, 'raw_text', ''),
                    confidence=getattr(r, 'confidence', 1.0),
                    match_strategy=getattr(r, 'match_strategy', 'exact')))

        if all_clips:
            all_clips.sort(key=lambda c: c.start_sec)
            self._project.set_results(all_clips)

        if report:
            self._thread_reports[0] = report

        try:
            self._save_detection_report(all_clips)
        except Exception as e:
            self._log.error("保存检测报告异常: %s", e)

        self._ocr_detecting = False
        self._detect_btn.setText("开始识别")
        self._detect_btn.setEnabled(True)
        self._progress_widget.setVisible(False)
        self._show_results()
        self.statusBar().showMessage(f"识别完成! {len(all_clips)} 个高光片段")
        self._log.info("检测流程完成: %d 个片段", len(all_clips))

    def _on_pipeline_error(self, msg):
        self._log.error("检测失败: %s", msg)
        self._reset_detect_state()

    def _cancel_detection(self):
        self._cancel_flag = True
        if hasattr(self, '_pool_worker') and self._pool_worker:
            self._pool_worker.cancel()
        for w in self._ocr_threads:
            w.cancel()
        self._progress_widget.setVisible(False)
        self._result_label.setText("识别结果")
        self._ocr_detecting = False
        self._detect_btn.setText("开始识别")
        self.statusBar().showMessage("已取消识别")

    def _reset_detect_state(self):
        self._ocr_detecting = False
        self._detect_btn.setText("开始识别")
        self._detect_btn.setEnabled(True)
        self._gap_bar.setVisible(False)

    def _on_ocr_progress(self, tid: int, pct: float):
        if tid < len(self._thread_bars):
            self._thread_bars[tid].setValue(int(pct))
        total_work = sum(b.value() for b in self._thread_bars)
        total_count = len(self._thread_bars)
        self._total_bar.setValue(total_work // total_count if total_count else 0)
        done = sum(1 for _ in self._thread_results.values())
        self.statusBar().showMessage(
            f"识别中... {done}/{len(self._ocr_threads)} 个线程完成 | 发现 {self._detect_hit_count} 个片段")

    def _append_log(self, msg: str, level: int):
        colors = {0: "#aaa", 1: "#fa0", 2: "#f55", 3: "#4caf50"}
        color = colors.get(level, "#aaa")
        self._log_view.append(f"<span style='color:{color}'>{msg}</span>")

    def _on_ocr_detected(self, timestamp: float, text: str):
        self._detect_hit_count += 1

    def _on_raw_ocr_text(self, timestamp: float, text: str, label: str):
        if not text or not text.strip():
            return
        ts = int(timestamp)
        short = text[:30] + "..." if len(text) > 30 else text
        self._log.info("[%02d:%02d] %s: %s", ts // 60, ts % 60, label, short)

    def _refine_boundaries(self, clips: list) -> list:
        """后处理: 二分搜索精化每个 clip 的起止边界 (±1帧精度)。"""
        from app.core.coarse_to_fine import BoundaryRefiner
        from app.core.detector import DetectionResult
        from app.core.player import VideoPlayer

        player = VideoPlayer()
        info = player.open(self._project.source.path)
        fps = info.fps
        player.close()

        rois = self._project.annotations.to_pixel_rois(info.width, info.height)
        kill_idx = 0
        for i, roi in enumerate(rois):
            if roi.label == '击杀信息':
                kill_idx = i
                break

        refiner = BoundaryRefiner(
            self._project.source.path, rois, kill_idx)
        search_window = self._project.detection.refine_search_window

        refined = []
        for c in clips:
            r = DetectionResult(
                start_sec=c.start_sec, end_sec=c.end_sec,
                raw_start_sec=c.raw_start_sec, raw_end_sec=c.raw_end_sec,
                action=c.action, actor=c.actor,
                pattern_id=c.pattern_id, source=c.source,
                raw_text=c.raw_text, confidence=c.confidence,
                match_strategy=c.match_strategy)
            rr = refiner.refine(r, fps, search_window=search_window)
            refined.append(ClipResult(
                start_sec=rr.start_sec, end_sec=rr.end_sec,
                raw_start_sec=rr.raw_start_sec, raw_end_sec=rr.raw_end_sec,
                action=rr.action, actor=rr.actor,
                pattern_id=rr.pattern_id, source=rr.source,
                raw_text=rr.raw_text, confidence=rr.confidence,
                match_strategy=rr.match_strategy))
        return refined

    def _cell_divide_events(self, clips: list) -> list:
        """后处理: BinSeg 细胞分裂 — 在大事件中递归搜索更多子事件。"""
        from app.core.coarse_to_fine import binseg_event_search
        from app.core.detector import OCRDetector
        from app.core.player import VideoPlayer
        import numpy as np

        detector = OCRDetector(gpu=self._project.detection.gpu,
                               engine=self._project.detection.ocr_engine)
        detector.detect(np.zeros((64, 64, 3), dtype=np.uint8))

        player = VideoPlayer()
        info = player.open(self._project.source.path)
        fps = info.fps
        player.close()

        rois = self._project.annotations.to_pixel_rois(info.width, info.height)
        kill_idx = 0
        for i, roi in enumerate(rois):
            if roi.label == '击杀信息':
                kill_idx = i
                break

        min_gap = self._project.detection.cell_min_gap
        search_window = self._project.detection.refine_search_window

        all_results = []
        for c in clips:
            if c.end_sec - c.start_sec < min_gap:
                all_results.append(c)
                continue
            sub_events, merged_n, short_n, short_info = binseg_event_search(
                self._project.source.path, rois, kill_idx,
                c.start_sec, c.end_sec, fps,
                detector, self._matcher,
                min_segment=min_gap, search_window=search_window,
                allowed_actors=self._project.detection.allowed_actors,
            )
            if merged_n:
                self._log.info("细胞分裂: 合并了 %d 个重叠事件", merged_n)
            if short_n:
                self._log.warning("细胞分裂: 过滤了 %d 个短事件 (%s)", short_n,
                                  ', '.join(s['action'] for s in short_info[:5]))
            if sub_events:
                for se in sub_events:
                    all_results.append(ClipResult(
                        start_sec=se.start_sec, end_sec=se.end_sec,
                        raw_start_sec=se.raw_start_sec, raw_end_sec=se.raw_end_sec,
                        action=se.action, actor=se.actor,
                        pattern_id=se.pattern_id, source=se.source,
                        raw_text=se.raw_text, confidence=se.confidence,
                        match_strategy=se.match_strategy))
            else:
                all_results.append(c)
        return all_results

    def _on_ocr_error(self, msg: str):
        self._log.error("OCR线程错误: %s", msg)
        self.statusBar().showMessage(f"OCR线程出错: {msg}", 5000)

    def _on_ocr_thread_done(self, tid: int, ranges: list, report=None):
        self._log.debug("_on_ocr_thread_done: tid=%d, ranges=%d, report=%s",
                        tid, len(ranges), report is not None)
        self._thread_results[tid] = ranges; self._completed += 1
        if report is not None:
            self._thread_reports[tid] = report
        if tid < len(self._thread_bars):
            self._thread_bars[tid].setValue(100)
            self._thread_bars[tid].setFormat(f"线程 {tid + 1}: 完成")
        if self._completed >= len(self._ocr_threads):
            self._log.debug("所有线程完成, 构建结果...")
            self._progress_widget.setVisible(False)
            self._filter_bar.setVisible(True)
            self._result_label.setText("识别结果")
            self._ocr_detecting = False
            self._detect_btn.setText("开始识别")
            all_clips = []
            for rl in self._thread_results.values():
                for item in rl:
                    s, e = item[0], item[1]
                    action = item[2] if len(item) > 2 else ""
                    actor = item[3] if len(item) > 3 else ""
                    pid = item[4] if len(item) > 4 else ""
                    src = item[5] if len(item) > 5 else "text"
                    rss = item[6] if len(item) > 6 else 0.0
                    rse = item[7] if len(item) > 7 else 0.0
                    raw_txt = item[8] if len(item) > 8 else ""
                    conf = item[9] if len(item) > 9 else 1.0
                    strat = item[10] if len(item) > 10 else "exact"
                    all_clips.append(ClipResult(
                        start_sec=s, end_sec=e, raw_start_sec=rss, raw_end_sec=rse,
                        action=action, actor=actor,
                        pattern_id=pid, source=src,
                        raw_text=raw_txt, confidence=conf, match_strategy=strat))
            self._log.debug("构建 %d 个 ClipResult", len(all_clips))
            if self._project.detection.refine_boundaries and all_clips:
                try:
                    all_clips = self._refine_boundaries(all_clips)
                except Exception as e:
                    self._log.error("边界精化失败 (跳过): %s", e)
            if self._project.detection.cell_divide and all_clips:
                try:
                    all_clips = self._cell_divide_events(all_clips)
                except Exception as e:
                    self._log.error("细胞分裂失败 (跳过): %s", e)
            if all_clips:
                all_clips.sort(key=lambda c: c.start_sec)
                self._project.set_results(all_clips)
            self._save_detection_report(all_clips)
            self._show_results()
            self._log.info("检测流程完成(legacy): %d 个片段", len(all_clips))

    def _save_detection_report(self, results: list):
        from app.core.detector import DetectionReport
        try:
            self._do_save_detection_report(results)
        except Exception as e:
            self._log.error("保存检测报告异常: %s", e)

    def _do_save_detection_report(self, results: list):
        from app.core.detector import DetectionReport
        reports = [r for r in self._thread_reports.values() if r is not None]
        src = self._project.source
        self._log.debug("_do_save_detection_report: %d reports, %d results, src.path=%s",
                        len(reports), len(results), src.path)
        if reports:
            merged = reports[0]
            for other in reports[1:]:
                merged.frames_log.extend(other.frames_log)
                merged.dropped.extend(other.dropped)
                merged.counter_events.extend(other.counter_events)
                merged.results.extend(other.results)
            merged.frames_log.sort(key=lambda f: (f.timestamp, f.roi_id))
            merged.dropped.sort(key=lambda d: d.timestamp)
            merged.counter_events.sort(key=lambda e: e["timestamp"])
        else:
            merged = DetectionReport(
                video_path=src.path,
                video_width=src.width, video_height=src.height,
                video_fps=src.fps,
                video_duration_sec=src.total_frames / max(src.fps, 1),
                config={"pipeline_mode": self._project.detection.pipeline_mode},
                rois=[{"id": i, "label": r.label}
                      for i, r in enumerate(self._project.annotations.regions)],
            )
        merged.results = [
            {"start_sec": c.start_sec, "end_sec": c.end_sec,
             "action": c.action, "actor": c.actor,
             "pattern_id": c.pattern_id, "source": c.source,
             "raw_text": c.raw_text, "confidence": c.confidence,
             "match_strategy": c.match_strategy}
            for c in results
        ]
        merged.summary.detections_after_merge = len(results)
        log_path = src.dirname and os.path.join(
            src.dirname, f"{src.basename}.detection_log.json")
        if not log_path:
            return
        merged.save_json(log_path)
        self._log.info("检测报告已保存: %s", log_path)

    def _show_results(self):
        count = self._project.result_count
        self._log.debug("_show_results: result_count=%d", count)
        self.statusBar().showMessage(f"识别完成! {count} 个高光片段")
        self._detect_btn.setEnabled(True)
        self._result_list.clear()
        actor_counts: dict[str, int] = {}
        for i, r in enumerate(self._project.results):
            sm, ss = int(r.start_sec // 60), int(r.start_sec % 60)
            em, es = int(r.end_sec // 60), int(r.end_sec % 60)
            tag = f"[{r.actor}·{r.action}]" if r.actor else ""
            src_mark = {"both": " ★", "counter": " ○"}.get(r.source, "")
            conf_tag = f" ~{r.confidence:.0%}" if r.match_strategy == "fuzzy" else ""
            text = f"{tag} 片段 {i+1}: [{sm:02d}:{ss:02d} - {em:02d}:{es:02d}] 时长 {r.duration:.1f}秒{src_mark}{conf_tag}"
            item = QListWidgetItem(text)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked)
            self._result_list.addItem(item)
            if r.actor:
                actor_counts[r.actor] = actor_counts.get(r.actor, 0) + 1
        total = count
        self._filter_all_btn.setText(f"全部({total})")
        for actor, btn in self._filter_btns.items():
            n = actor_counts.get(actor, 0)
            btn.setText(f"{actor}({n})")
        self._filter_all_btn.setChecked(True)
        self._filter_bar.setVisible(total > 0)
        self._player.update_timeline_clips(self._project.results)
        self._update_export_buttons()
        self._update_undo_redo_buttons()

    def _on_timeline_clip_selected(self, idx: int):
        """时间轴点击片段 → 同步选中结果列表"""
        if 0 <= idx < self._result_list.count():
            self._result_list.setCurrentRow(idx)

    def _on_result_double_clicked(self, item):
        idx = self._result_list.row(item)
        if 0 <= idx < len(self._project.results):
            r = self._project.results[idx]
            ts = int(r.start_sec)
            strat = r.match_strategy if r.match_strategy != "exact" else ""
            ocr = r.raw_text[:40] + "..." if len(r.raw_text) > 40 else r.raw_text
            self._append_log(
                "[%02d:%02d] %s:%s %s OCR=\"%s\" conf=%.2f" % (
                ts // 60, ts % 60, r.action, r.actor,
                f"[{strat}]" if strat else "",
                ocr or "(无原文)", r.confidence,
            ), level=3)
            seek_to = max(0, r.start_sec + self._project.detection.padding_before - 1.5)
            self._player.seek_to_second(seek_to)

    # ---- 撤销/重做 ----

    def _undo_result(self):
        desc = self._project.undo()
        if desc:
            self.statusBar().showMessage(f"撤销: {desc}")
            self._show_results()

    def _redo_result(self):
        desc = self._project.redo()
        if desc:
            self.statusBar().showMessage(f"重做: {desc}")
            self._show_results()

    def _update_undo_redo_buttons(self):
        self._undo_btn.setEnabled(self._project.can_undo)
        self._redo_btn.setEnabled(self._project.can_redo)

    def _on_frame_changed(self, frame: int):
        pass  # 预留给时间线组件

    def _open_video(self):
        settings = QSettings("GameVideoEdit", "PeaceEliteHighlights")
        last_dir = settings.value("last_video_dir", "")
        from app.utils.paths import videos_dir
        default = str(last_dir) if last_dir else str(videos_dir())
        path, _ = QFileDialog.getOpenFileName(
            self, "打开视频", default, "视频文件 (*.mp4 *.avi *.mov *.mkv)")
        if path:
            self._player.open_video_file(path)
            self._detect_btn.setEnabled(True)
            settings.setValue("last_video_dir", os.path.dirname(path))
            self._on_video_opened(path)

    def _on_video_opened(self, path: str):
        self._side_panel.refresh_tag_list()
        self._side_panel.add_history(path)
        self._player.set_label_colors(self._side_panel.label_colors())
        self._player.set_current_label(self._side_panel.current_label)
        self.statusBar().showMessage(f"已打开: {os.path.basename(path)}")
        if self._project.results:
            self._show_results()
            ts = self._project.last_detection[:19] if self._project.last_detection else ""
            msg = f"已加载 {self._project.result_count} 个历史识别片段"
            if ts:
                msg += f" ({ts})"
            self.statusBar().showMessage(msg)

    def _save_annotations(self):
        """标注已自动保存到 .roi.json，此方法保留用于手动导出完整标注文件"""
        data = self._player.save_annotations()
        if not data: QMessageBox.warning(self, "警告", "没有可保存的标注区域"); return
        vp = data["video_path"]; bn = os.path.splitext(os.path.basename(vp))[0]
        default = os.path.join(os.path.dirname(vp), f"{bn}_labels.json")
        path, _ = QFileDialog.getSaveFileName(self, "保存标注", default, "JSON文件 (*.json)")
        if path:
            if not path.endswith(".json"): path += ".json"
            with open(path, "w", encoding="utf-8") as f: json.dump(data, f, ensure_ascii=False, indent=2)
            self.statusBar().showMessage(f"标注已保存: {path}")

    def _start_export(self, selected_only: bool = False):
        results = self._checked_results() if selected_only else [
            self._project.results[i] for i in range(self._result_list.count())
            if not self._result_list.item(i).isHidden() and i < len(self._project.results)
        ]
        if not results:
            QMessageBox.warning(self, "提示", "没有可导出的片段"); return
        config = ExportConfig(output_path=self._project.export.output_path)
        self._export_selected_btn.setEnabled(False)
        self._export_all_btn.setEnabled(False)
        self._worker = ExportWorker(
            self._project.source.path,
            [TimeRange(r.start_sec, r.end_sec) for r in results],
            config,
        )
        self._worker.progress.connect(
            lambda p, m: self.statusBar().showMessage(f"导出: {m} ({p}%)"))
        self._worker.finished.connect(self._on_export_done)
        self._worker.start()

    def _checked_results(self) -> list:
        selected = []
        for i in range(self._result_list.count()):
            item = self._result_list.item(i)
            if (not item.isHidden() and item.checkState() == Qt.Checked
                    and i < len(self._project.results)):
                selected.append(self._project.results[i])
        return selected

    def _on_export_done(self, success: bool, message: str):
        self._update_export_buttons()
        if success:
            QMessageBox.information(self, "完成", message)
        else:
            QMessageBox.warning(self, "失败", message)

    def _update_export_buttons(self):
        total = self._project.result_count
        visible = sum(1 for i in range(self._result_list.count())
                      if not self._result_list.item(i).isHidden())
        checked = sum(1 for i in range(self._result_list.count())
                      if not self._result_list.item(i).isHidden()
                      and self._result_list.item(i).checkState() == Qt.Checked)
        has_results = total > 0
        self._export_selected_btn.setText(f"导出选中({checked})")
        self._export_selected_btn.setEnabled(has_results and checked > 0)
        self._export_all_btn.setText(f"导出全部({visible})")
        self._export_all_btn.setEnabled(has_results and visible > 0)
        self._export_path_label.setText(
            os.path.basename(self._project.export.output_path)
            if self._project.export.output_path else "(未设置)")

    def _choose_export_path(self):
        default = self._project.export.output_path or ""
        path, _ = QFileDialog.getSaveFileName(
            self, "导出路径", default,
            "MP4视频 (*.mp4);;AVI视频 (*.avi);;MKV视频 (*.mkv)")
        if path:
            self._project.export.output_path = path
            self._update_export_buttons()

    def _load_matcher(self):
        platform = self._project.platform
        self._refresh_toolbar_presets(platform)
        self._load_matcher_for_platform(platform)
        self._sync_toolbar_preset_to_file(self._project.preset_file)

    def _load_matcher_for_platform(self, platform: str):
        """加载指定平台的 matcher。

        优先级: QSettings 全局偏好 > 项目预设 > 平台默认 > keywords.yaml
        """
        from app.core.presets import PresetManager
        from app.utils.paths import config_dir

        settings = QSettings("GameVideoEdit", "PeaceEliteHighlights")
        saved_preset = settings.value(f"preset/{platform}", "")

        # ── 1. QSettings 全局偏好 ──
        if saved_preset:
            try:
                pm = PresetManager()
                cfg = pm.load(saved_preset.replace(".yaml", ""))
                self._matcher = KeywordMatcher.from_dict(cfg)
                self._project.preset_file = saved_preset
                return
            except FileNotFoundError:
                self._log.debug("全局预设已删除: %s", saved_preset)
                settings.setValue(f"preset/{platform}", "")

        # ── 2. 项目级预设文件 ──
        pf = self._project.preset_file
        if pf:
            try:
                pm = PresetManager()
                cfg = pm.load(pf.replace(".yaml", ""))
                self._matcher = KeywordMatcher.from_dict(cfg)
                return
            except Exception:
                pass

        # ── 3. 平台第一个预设 (PC 优先简体中文) ──
        try:
            pm = PresetManager()
            presets = pm.list(platform)
            if presets:
                if platform == "pc":
                    for p in presets:
                        if p["file"] == "pubg_pc_zh.yaml":
                            cfg = pm.load("pubg_pc_zh")
                            self._matcher = KeywordMatcher.from_dict(cfg)
                            self._project.preset_file = "pubg_pc_zh.yaml"
                            return
                cfg = pm.load(presets[0]["file"].replace(".yaml", ""))
                self._matcher = KeywordMatcher.from_dict(cfg)
                self._project.preset_file = presets[0]["file"]
                return
        except Exception:
            pass

        # ── 4. keywords.yaml 回退 ──
        try:
            kw = config_dir() / "keywords.yaml"
            if kw.exists():
                self._matcher = KeywordMatcher.from_yaml(str(kw))
        except Exception:
            pass
