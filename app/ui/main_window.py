"""主窗口 —— 集成视频播放器、OCR、导出。"""

import json
import os
from pathlib import Path

from PySide6.QtCore import QEvent, Qt, QSettings, QTimer
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
from app.workers.ocr_worker import OCRWorker
from app.workers.export_worker import ExportWorker


class MainWindow(QMainWindow):
    """应用主窗口 —— 药药的剪辑工具"""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("药药的剪辑工具"); self.resize(1200, 800)
        self._dark = False
        self._matcher: KeywordMatcher | None = None
        self._project = Project()
        self._ocr_threads: list[OCRWorker] = []
        self._thread_results: dict[int, list] = {}
        self._completed = 0
        self._ocr_detecting = False
        self._detect_hit_count = 0
        self._worker: ExportWorker | None = None
        self._setup_ui(); self._apply_theme()
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
        tl.addWidget(title); tl.addStretch()
        self._params_btn = QPushButton("识别参数 ▾"); self._params_btn.clicked.connect(self._toggle_params)
        tl.addWidget(self._params_btn)
        self._detect_btn = QPushButton("开始识别"); self._detect_btn.setStyleSheet(
            "QPushButton { background-color: #9C27B0; color: white; font-weight: bold; "
            "border-radius: 5px; padding: 8px 18px; } QPushButton:hover { background-color: #7B1FA2; }"
            "QPushButton:disabled { background-color: #666; }")
        self._detect_btn.clicked.connect(self._on_detect_btn)
        self._detect_btn.setEnabled(False); tl.addWidget(self._detect_btn)
        self._theme_btn = QPushButton("深色模式"); self._theme_btn.setProperty("cssClass", "primary")
        self._theme_btn.clicked.connect(self._toggle_theme); tl.addWidget(self._theme_btn)
        ml.addLayout(tl)

        # ---- 可折叠参数面板（弹出浮层） ----
        self._params_panel = self._create_params_panel()
        self._params_panel.setVisible(False)

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
        splitter.addWidget(right); splitter.setSizes([340, 640, 320])
        for i in range(3):
            splitter.setCollapsible(i, False)
        ml.addWidget(splitter, 1)
        self.statusBar().showMessage("就绪")
        self._wire_side_panel()

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
        self._thread_bars: list[QProgressBar] = []
        self._thread_container = QVBoxLayout(); l.addLayout(self._thread_container)
        self._detect_count_label = QLabel("已检测到: 0")
        self._detect_count_label.setStyleSheet("color: #4CAF50; font-weight: bold;")
        self._detect_count_label.setFixedHeight(22)
        self._detect_count_label.setWordWrap(True)
        l.addWidget(self._detect_count_label)
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
        self._update_params_popup_style()

    def _update_params_popup_style(self):
        if self._dark:
            self._params_panel.setStyleSheet(
                "#paramsPopup { background: #2D2D2D; border: 2px solid #555; "
                "border-radius: 8px; padding: 10px; } "
                "#paramsPopup QLabel { color: #DDD; } "
                "#paramsPopup QComboBox { background: #3D3D3D; color: #DDD; "
                "border: 1px solid #555; border-radius: 3px; padding: 3px; } "
                "#paramsPopup QSpinBox, #paramsPopup QDoubleSpinBox { "
                "background: #3D3D3D; color: #DDD; border: 1px solid #555; "
                "border-radius: 3px; padding: 3px; }")
        else:
            self._params_panel.setStyleSheet(
                "#paramsPopup { background: #FFFFFF; border: 2px solid #3a7ca5; "
                "border-radius: 8px; padding: 10px; }")

    # ---- 参数面板 ----

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

    def _create_params_panel(self) -> QWidget:
        d = self._load_detection_defaults()

        # 直接设置 Project 默认值
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

        w = QWidget()
        w.setWindowFlags(Qt.Popup)
        w.setObjectName("paramsPopup")
        gv = QVBoxLayout(w); gv.setContentsMargins(10, 10, 10, 10)

        pl = QHBoxLayout()
        pl.addWidget(QLabel("预留时间(秒):"))
        self._padding = QSpinBox(); self._padding.setRange(1, 30); self._padding.setValue(int(det.padding_before))
        self._padding.valueChanged.connect(
            lambda v: setattr(self._project.detection, 'padding_before', v) or
            setattr(self._project.detection, 'padding_after', v))
        pl.addWidget(self._padding)
        pl.addWidget(QLabel("线程数:"))
        self._threads_spin = QSpinBox(); self._threads_spin.setRange(1, 16)
        self._threads_spin.setValue(det.num_threads)
        self._threads_spin.valueChanged.connect(
            lambda v: setattr(self._project.detection, 'num_threads', v))
        pl.addWidget(self._threads_spin)
        pl.addWidget(QLabel("旋转:"))
        self._rot_combo = QComboBox()
        self._rot_combo.addItems(["0°", "90°", "180°", "270°"])
        self._rot_combo.setCurrentIndex(det.rotation // 90)
        self._rot_combo.currentIndexChanged.connect(
            lambda i: setattr(self._project.detection, 'rotation', i * 90))
        pl.addWidget(self._rot_combo); pl.addStretch(); gv.addLayout(pl)

        pl2 = QHBoxLayout()
        pl2.addWidget(QLabel("OCR模式:"))
        is_time = det.mode == "time"
        self._mode_combo = QComboBox(); self._mode_combo.addItems(["时间间隔", "帧间隔"])
        self._mode_combo.setCurrentIndex(0 if is_time else 1)
        self._mode_combo.currentIndexChanged.connect(self._on_ocr_mode_changed)
        pl2.addWidget(self._mode_combo)
        pl2.addWidget(QLabel("采样间隔:"))
        self._interval_spin = QDoubleSpinBox(); self._interval_spin.setRange(0.1, 10.0)
        self._interval_spin.setValue(det.interval_sec)
        self._interval_spin.setSingleStep(0.1); self._interval_spin.setDecimals(1)
        self._interval_spin.valueChanged.connect(
            lambda v: setattr(self._project.detection, 'interval_sec', v))
        pl2.addWidget(self._interval_spin); self._interval_unit = QLabel("秒"); pl2.addWidget(self._interval_unit)
        self._skip_spin = QSpinBox(); self._skip_spin.setRange(1, 30)
        self._skip_spin.setValue(det.skip_frames)
        self._skip_spin.setVisible(not is_time)
        self._skip_spin.valueChanged.connect(
            lambda v: setattr(self._project.detection, 'skip_frames', v))
        pl2.addWidget(self._skip_spin)
        pl2.addWidget(QLabel("命中跳秒:"))
        self._post_detect = QDoubleSpinBox()
        self._post_detect.setRange(0.1, 5.0)
        self._post_detect.setValue(det.post_detect_skip_sec)
        self._post_detect.setSingleStep(0.1); self._post_detect.setDecimals(1)
        self._post_detect.valueChanged.connect(
            lambda v: setattr(self._project.detection, 'post_detect_skip_sec', v))
        pl2.addWidget(self._post_detect); pl2.addWidget(QLabel("秒"))
        pl2.addStretch()
        gv.addLayout(pl2)

        save_btn = QPushButton("保存为默认值")
        save_btn.clicked.connect(self._save_detection_defaults)
        gv.addWidget(save_btn)

        return w

    def _save_detection_defaults(self):
        import yaml
        from app.utils.paths import config_dir
        yaml_path = config_dir() / "default.yaml"
        cfg = {}
        if yaml_path.exists():
            try:
                with open(yaml_path, "r", encoding="utf-8") as f:
                    cfg = yaml.safe_load(f) or {}
            except Exception:
                pass
        cfg["detection"] = {
            "mode": self._project.detection.mode,
            "interval_sec": self._project.detection.interval_sec,
            "skip_frames": self._project.detection.skip_frames,
            "post_detect_skip_sec": self._project.detection.post_detect_skip_sec,
            "padding_before": self._project.detection.padding_before,
            "padding_after": self._project.detection.padding_after,
            "merge_gap": self._project.detection.merge_gap,
            "num_threads": self._project.detection.num_threads,
            "rotation": self._project.detection.rotation,
        }
        with open(yaml_path, "w", encoding="utf-8") as f:
            yaml.dump(cfg, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
        self.statusBar().showMessage("检测参数已保存为默认值", 3000)

    def _toggle_params(self):
        if self._params_panel.isVisible():
            self._params_panel.hide()
            self._params_btn.setText("识别参数 ▾")
        else:
            btn = self._params_btn
            pos = btn.mapToGlobal(btn.rect().bottomLeft())
            self._params_panel.move(pos)
            self._params_panel.show()
            self._params_btn.setText("识别参数 ▴")

    def _on_ocr_mode_changed(self, index):
        is_time = index == 0
        self._project.detection.mode = "time" if is_time else "frame"
        self._interval_spin.setVisible(is_time)
        self._interval_unit.setVisible(is_time)
        self._skip_spin.setVisible(not is_time)

    # ---- OCR 识别 ----

    def _on_detect_btn(self):
        if self._ocr_detecting:
            self._cancel_detection()
        else:
            self._start_detection()

    def _start_detection(self):
        if not self._project.source.path or not self._matcher:
            return
        self._ocr_detecting = True
        self._detect_btn.setText("取消识别")
        self._export_selected_btn.setEnabled(False)
        self._export_all_btn.setEnabled(False)
        self._result_list.clear(); self._thread_results.clear(); self._completed = 0
        self._ocr_threads.clear()
        self._detect_hit_count = 0
        self._result_label.setText("识别进度")
        self._progress_widget.setVisible(True)
        self._total_bar.setValue(0)
        self._detect_count_label.setText("已检测到: 0")
        for b in self._thread_bars:
            self._thread_container.removeWidget(b)
            b.deleteLater()
        self._thread_bars.clear()

        from app.core.detector import OCRDetector
        try:
            detector = OCRDetector(gpu=True)
            import numpy as np
            detector.detect(np.zeros((64, 64, 3), dtype=np.uint8))
        except Exception as e:
            QMessageBox.critical(self, "模型加载失败", str(e))
            self._reset_detect_state()
            return

        from app.core.player import VideoPlayer
        player = VideoPlayer()
        try:
            info = player.open(self._project.source.path)
        except Exception as e:
            QMessageBox.critical(self, "错误", str(e))
            self._reset_detect_state()
            return
        finally:
            player.close()

        total = info.total_frames; n_threads = self._project.detection.num_threads
        fps = info.fps; frames_per = total // n_threads

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
            worker.finished.connect(self._on_ocr_thread_done)
            self._ocr_threads.append(worker)

            bar = QProgressBar(); bar.setRange(0, 100); bar.setValue(0); bar.setFixedHeight(18)
            bar.setFormat(f"线程 {i + 1}: %p%")
            self._thread_bars.append(bar)
            self._thread_container.addWidget(bar)

        self.statusBar().showMessage(f"启动 {n_threads} 个识别线程...")
        for w in self._ocr_threads:
            w.start()

    def _cancel_detection(self):
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

    def _on_ocr_progress(self, tid: int, pct: float):
        if tid < len(self._thread_bars):
            self._thread_bars[tid].setValue(int(pct))
        total_work = sum(b.value() for b in self._thread_bars)
        total_count = len(self._thread_bars)
        self._total_bar.setValue(total_work // total_count if total_count else 0)
        done = sum(1 for _ in self._thread_results.values())
        self.statusBar().showMessage(
            f"识别中... {done}/{len(self._ocr_threads)} 个线程完成 | 发现 {self._detect_hit_count} 个片段")

    def _on_ocr_detected(self, timestamp: float, text: str):
        self._detect_hit_count += 1
        short = text[:25] + "..." if len(text) > 25 else text
        self._detect_count_label.setText(
            f"已检测到: {self._detect_hit_count} | [{timestamp:.1f}s] {short}")

    def _on_ocr_thread_done(self, tid: int, ranges: list):
        self._thread_results[tid] = ranges; self._completed += 1
        if tid < len(self._thread_bars):
            self._thread_bars[tid].setValue(100)
            self._thread_bars[tid].setFormat(f"线程 {tid + 1}: 完成")
        if self._completed >= len(self._ocr_threads):
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
                    all_clips.append(ClipResult(
                        start_sec=s, end_sec=e, action=action, actor=actor))
            if all_clips:
                all_clips.sort(key=lambda c: c.start_sec)
                self._project.set_results(all_clips)
            self._show_results()

    def _show_results(self):
        count = self._project.result_count
        self.statusBar().showMessage(f"识别完成! {count} 个高光片段")
        self._detect_btn.setEnabled(True)
        self._result_list.clear()
        actor_counts: dict[str, int] = {}
        for i, r in enumerate(self._project.results):
            sm, ss = int(r.start_sec // 60), int(r.start_sec % 60)
            em, es = int(r.end_sec // 60), int(r.end_sec % 60)
            tag = f"[{r.actor}·{r.action}]" if r.actor else ""
            text = f"{tag} 片段 {i+1}: [{sm:02d}:{ss:02d} - {em:02d}:{es:02d}] 时长 {r.duration:.1f}秒"
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
        self._update_export_buttons()
        self._update_undo_redo_buttons()

    def _on_result_double_clicked(self, item):
        idx = self._result_list.row(item)
        if 0 <= idx < len(self._project.results):
            r = self._project.results[idx]
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
        try:
            from app.utils.paths import config_dir
            kw = config_dir() / "keywords.yaml"
            if kw.exists(): self._matcher = KeywordMatcher.from_yaml(str(kw))
        except Exception: pass
