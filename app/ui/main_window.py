"""主窗口 —— 集成视频播放器、OCR、导出。"""

import json
import os
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (
    QFileDialog, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QMainWindow, QMessageBox, QProgressBar,
    QPushButton, QSpinBox, QSplitter, QTextEdit, QVBoxLayout,
    QWidget, QDialog, QDialogButtonBox, QComboBox,
)

from app.core.annotator import AnnotationStore
from app.core.detector import TimeRange, DetectionEngine, OCRDetector
from app.core.exporter import ExportConfig, VideoExporter
from app.core.keywords import KeywordMatcher
from app.ui.video_player import VideoPlayerWidget
from app.workers.ocr_worker import OCRWorker, LOG_INFO, LOG_WARNING, LOG_ERROR
from app.workers.export_worker import ExportWorker


class MainWindow(QMainWindow):
    """应用主窗口 —— 药药的剪辑工具"""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("药药的剪辑工具"); self.resize(1200, 800)
        self._dark = False
        self._matcher: KeywordMatcher | None = None
        self._ocr_threads: list[OCRWorker] = []
        self._thread_results: dict[int, list] = {}
        self._completed_threads = 0
        self._setup_ui(); self._apply_theme()
        QTimer.singleShot(100, self._load_matcher)

    def _setup_ui(self):
        central = QWidget(); self.setCentralWidget(central)
        ml = QVBoxLayout(central); ml.setContentsMargins(10, 10, 10, 10); ml.setSpacing(8)
        tl = QHBoxLayout()
        title = QLabel("药药的剪辑工具"); title.setObjectName("titleLabel")
        tl.addWidget(title); tl.addStretch()
        self._theme_btn = QPushButton("深色模式"); self._theme_btn.setProperty("cssClass", "primary")
        self._theme_btn.clicked.connect(self._toggle_theme); tl.addWidget(self._theme_btn)
        ml.addLayout(tl)
        self._player = VideoPlayerWidget(); ml.addWidget(self._player, 1)
        bl = QHBoxLayout(); bl.setSpacing(10)
        btn_open = QPushButton("打开视频"); btn_open.setProperty("cssClass", "primary")
        btn_open.clicked.connect(self._open_video); bl.addWidget(btn_open)
        btn_save = QPushButton("保存标注"); btn_save.setProperty("cssClass", "success")
        btn_save.clicked.connect(self._save_annotations); bl.addWidget(btn_save)
        btn_ocr = QPushButton("OCR识别"); btn_ocr.setStyleSheet(
            "QPushButton { background-color: #9C27B0; color: white; font-weight: bold; "
            "border-radius: 5px; padding: 8px 18px; } QPushButton:hover { background-color: #7B1FA2; }")
        btn_ocr.clicked.connect(self._start_ocr); bl.addWidget(btn_ocr)
        btn_export = QPushButton("导出剪辑"); btn_export.setStyleSheet(
            "QPushButton { background-color: #FF5722; color: white; font-weight: bold; "
            "border-radius: 5px; padding: 8px 18px; } QPushButton:hover { background-color: #E64A19; }")
        btn_export.clicked.connect(self._start_export); bl.addWidget(btn_export)
        ml.addLayout(bl); self.statusBar().showMessage("就绪")

    def _toggle_theme(self):
        self._dark = not self._dark; self._apply_theme()

    def _apply_theme(self):
        qss_file = Path(__file__).parent / "styles" / ("dark.qss" if self._dark else "light.qss")
        if qss_file.exists():
            with open(qss_file, "r", encoding="utf-8") as f: self.setStyleSheet(f.read())
        self._theme_btn.setText("浅色模式" if self._dark else "深色模式")

    def _open_video(self):
        path, _ = QFileDialog.getOpenFileName(self, "打开视频", "", "视频文件 (*.mp4 *.avi *.mov *.mkv)")
        if path: self._player.open_video_file(path)

    def _save_annotations(self):
        data = self._player.save_annotations()
        if not data: QMessageBox.warning(self, "警告", "没有可保存的标注区域"); return
        vp = data["video_path"]; bn = os.path.splitext(os.path.basename(vp))[0]
        default = os.path.join(os.path.dirname(vp), f"{bn}_labels.json")
        path, _ = QFileDialog.getSaveFileName(self, "保存标注", default, "JSON文件 (*.json)")
        if path:
            if not path.endswith(".json"): path += ".json"
            with open(path, "w", encoding="utf-8") as f: json.dump(data, f, ensure_ascii=False, indent=2)
            self.statusBar().showMessage(f"标注已保存: {path}")

    def _start_ocr(self):
        vp = self._player.video_path
        if not vp: QMessageBox.warning(self, "提示", "请先打开视频"); return
        vd = os.path.dirname(vp); vn = os.path.splitext(os.path.basename(vp))[0]
        auto = os.path.join(vd, f"{vn}_labels.json")
        dlg = OCRDialog(vp, auto if os.path.exists(auto) else "", self._player.rotation,
                        self._matcher, self); dlg.exec()

    def _start_export(self):
        vp = self._player.video_path
        if not vp: QMessageBox.warning(self, "提示", "请先打开视频"); return
        vd = os.path.dirname(vp); vn = os.path.splitext(os.path.basename(vp))[0]
        auto = os.path.join(vd, f"{vn}_clips.json")
        dlg = ExportDialog(vp, auto if os.path.exists(auto) else "", self); dlg.exec()

    def _load_matcher(self):
        try:
            from app.utils.paths import config_dir
            kw = config_dir() / "keywords.yaml"
            if kw.exists(): self._matcher = KeywordMatcher.from_yaml(str(kw))
        except Exception: pass


class OCRDialog(QDialog):
    """OCR 处理对话框"""

    def __init__(self, video_path: str, annotation_path: str, rotation: int = 0,
                 matcher: KeywordMatcher | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("OCR处理"); self.setMinimumSize(900, 700)
        self._video_path = video_path
        self._annotation_path = annotation_path
        self._rotation = rotation
        self._matcher = matcher
        self._annotation: AnnotationStore | None = None
        self._time_ranges: list[TimeRange] = []
        self._ocr_threads: list[OCRWorker] = []
        self._thread_results: dict[int, list] = {}
        self._completed = 0

        layout = QVBoxLayout(self)
        # 标注文件选择
        g1 = QGroupBox("标注文件"); gl = QVBoxLayout(g1)
        al = QHBoxLayout(); al.addWidget(QLabel("路径:"))
        self._anno_edit = QLineEdit(); self._anno_edit.setReadOnly(True); al.addWidget(self._anno_edit)
        btn_browse = QPushButton("浏览..."); btn_browse.clicked.connect(self._browse_anno); al.addWidget(btn_browse)
        gl.addLayout(al); layout.addWidget(g1)

        # 参数
        g2 = QGroupBox("参数"); pl = QHBoxLayout(g2)
        pl.addWidget(QLabel("预留时间(秒):")); self._padding = QSpinBox(); self._padding.setRange(1, 30); self._padding.setValue(10); pl.addWidget(self._padding)
        pl.addWidget(QLabel("线程数:")); self._threads_spin = QSpinBox(); self._threads_spin.setRange(1, 16); self._threads_spin.setValue(4); pl.addWidget(self._threads_spin)
        pl.addWidget(QLabel("旋转:")); self._rot_combo = QComboBox()
        self._rot_combo.addItems(["0°", "90°", "180°", "270°"]); self._rot_combo.setCurrentIndex({0: 0, 90: 1, 180: 2, 270: 3}.get(rotation, 0))
        pl.addWidget(self._rot_combo); pl.addStretch(); layout.addWidget(g2)

        # 进度
        self._status = QLabel("准备就绪..."); layout.addWidget(self._status)
        self._progress = QProgressBar(); layout.addWidget(self._progress)

        # 日志+结果
        splitter = QSplitter(Qt.Horizontal)
        self._ocr_log = QTextEdit(); self._ocr_log.setReadOnly(True); splitter.addWidget(self._ocr_log)
        self._sys_log = QTextEdit(); self._sys_log.setReadOnly(True); splitter.addWidget(self._sys_log)
        layout.addWidget(splitter, 1)

        # 结果列表
        self._result_list = QListWidget(); layout.addWidget(self._result_list)

        # 按钮
        bl = QHBoxLayout()
        self._start_btn = QPushButton("开始处理"); self._start_btn.clicked.connect(self._start); self._start_btn.setEnabled(False)
        bl.addWidget(self._start_btn)
        btn_cancel = QPushButton("取消"); btn_cancel.clicked.connect(self._cancel); bl.addWidget(btn_cancel)
        self._save_btn = QPushButton("保存结果"); self._save_btn.clicked.connect(self._save); self._save_btn.setEnabled(False)
        bl.addWidget(self._save_btn); layout.addLayout(bl)

        # 自动加载
        if annotation_path and os.path.exists(annotation_path):
            self._load_annotation(annotation_path)

    def _browse_anno(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择标注文件", "", "JSON文件 (*.json)")
        if path: self._load_annotation(path)

    def _load_annotation(self, path: str):
        try:
            self._annotation = AnnotationStore.load_json(path)
            self._annotation_path = path; self._anno_edit.setText(path)
            self._video_path = self._annotation.video_path
            self._start_btn.setEnabled(True)
            self._log_sys(f"已加载标注: {path}, {self._annotation.region_count} 个区域")
        except Exception as e:
            QMessageBox.critical(self, "加载失败", str(e))

    def _start(self):
        if not self._annotation or not self._matcher: return
        self._start_btn.setEnabled(False); self._save_btn.setEnabled(False)
        self._result_list.clear(); self._ocr_log.clear(); self._sys_log.clear()
        self._thread_results.clear(); self._completed = 0

        # 预加载模型 (避免在线程中首次加载阻塞)
        self._status.setText("正在加载OCR模型...")
        self._log_sys("加载 EasyOCR 模型中...")
        from app.core.detector import OCRDetector
        try:
            detector = OCRDetector(gpu=True)
            # 预热: 跑一次空推理确保模型加载到GPU
            import numpy as np
            detector.detect(np.zeros((64, 64, 3), dtype=np.uint8))
        except Exception as e:
            QMessageBox.critical(self, "模型加载失败", str(e))
            self._start_btn.setEnabled(True)
            return

        # 验证GPU
        import torch
        if torch.cuda.is_available():
            mem = torch.cuda.memory_allocated() / (1024**3)
            self._log_sys(f"GPU 显存已分配: {mem:.2f} GB")
        self._status.setText("模型加载完成, 正在启动处理...")

        from app.core.player import VideoPlayer
        player = VideoPlayer()
        try: info = player.open(self._video_path)
        except Exception as e: QMessageBox.critical(self, "错误", str(e)); return
        finally: player.close()

        total = info.total_frames; n_threads = self._threads_spin.value()
        fps = info.fps
        frames_per = total // n_threads
        self._ocr_threads.clear()

        for i in range(n_threads):
            sf = i * frames_per; ef = (i + 1) * frames_per - 1 if i < n_threads - 1 else total - 1
            worker = OCRWorker(i, self._video_path, self._annotation, self._matcher,
                               gpu=True, skip_frames=3, start_frame=sf, end_frame=ef,
                               padding_before=self._padding.value(),
                               padding_after=self._padding.value())
            worker.log.connect(self._log_sys)
            worker.detected.connect(lambda ts, txt: self._log_ocr(f"[{int(ts//60):02d}:{int(ts%60):02d}] {txt}"))
            worker.finished.connect(self._on_thread_done)
            self._ocr_threads.append(worker)

        self._status.setText(f"启动 {n_threads} 个线程...")
        for w in self._ocr_threads: w.start()

    def _on_thread_done(self, tid: int, ranges: list):
        self._thread_results[tid] = ranges; self._completed += 1
        self._log_sys(f"线程 {tid} 完成: {len(ranges)} 个片段")
        if self._completed >= len(self._ocr_threads):
            all_ranges = []
            for rl in self._thread_results.values():
                for s, e in rl: all_ranges.append(TimeRange(s, e))
            self._time_ranges = DetectionEngine.merge_time_ranges(all_ranges)
            self._show_results()

    def _show_results(self):
        self._status.setText(f"完成! {len(self._time_ranges)} 个高光片段")
        self._start_btn.setEnabled(True); self._save_btn.setEnabled(True)
        self._progress.setValue(100)
        for i, tr in enumerate(self._time_ranges):
            sm, ss = int(tr.start_sec // 60), int(tr.start_sec % 60)
            em, es = int(tr.end_sec // 60), int(tr.end_sec % 60)
            self._result_list.addItem(
                f"片段 {i+1}: [{sm:02d}:{ss:02d} - {em:02d}:{es:02d}] 时长 {tr.duration:.1f}秒")

    def _save(self):
        if not self._time_ranges: QMessageBox.warning(self, "警告", "没有可保存的结果"); return
        vd = os.path.dirname(self._video_path); vn = os.path.splitext(os.path.basename(self._video_path))[0]
        default = os.path.join(vd, f"{vn}_clips.json")
        path, _ = QFileDialog.getSaveFileName(self, "保存结果", default, "JSON文件 (*.json)")
        if path:
            if not path.endswith(".json"): path += ".json"
            data = {"video_path": self._video_path, "clip_ranges": [[r.start_sec, r.end_sec] for r in self._time_ranges]}
            with open(path, "w", encoding="utf-8") as f: json.dump(data, f, ensure_ascii=False, indent=2)
            QMessageBox.information(self, "保存成功", f"已保存 {len(self._time_ranges)} 个片段")

    def _cancel(self):
        for w in self._ocr_threads: w.cancel()
        self._status.setText("已取消")

    def _log_sys(self, msg: str, level: int = 0):
        self._sys_log.append(msg)
        c = self._sys_log.textCursor(); c.movePosition(QTextCursor.End); self._sys_log.setTextCursor(c)

    def _log_ocr(self, text: str):
        self._ocr_log.append(text)
        c = self._ocr_log.textCursor(); c.movePosition(QTextCursor.End); self._ocr_log.setTextCursor(c)


class ExportDialog(QDialog):
    """视频导出对话框"""

    def __init__(self, video_path: str, clips_path: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle("导出剪辑"); self.setMinimumSize(800, 600)
        self._video_path = video_path
        self._clips_path = clips_path
        self._clip_ranges: list[TimeRange] = []
        self._output_path = ""
        self._worker: ExportWorker | None = None

        layout = QVBoxLayout(self)

        g1 = QGroupBox("视频信息"); gl = QVBoxLayout(g1)
        gl.addWidget(QLabel(f"视频: {os.path.basename(video_path)}" if video_path else "未打开视频"))
        clips_label = QLabel("片段: 未加载"); gl.addWidget(clips_label); self._clips_label = clips_label
        btn_load = QPushButton("加载剪辑数据(JSON)"); btn_load.clicked.connect(self._load_clips); gl.addWidget(btn_load)
        layout.addWidget(g1)

        g2 = QGroupBox("输出设置"); ol = QVBoxLayout(g2)
        oph = QHBoxLayout(); oph.addWidget(QLabel("输出路径:"))
        self._out_edit = QLineEdit(); self._out_edit.setReadOnly(True); oph.addWidget(self._out_edit)
        btn_out = QPushButton("浏览..."); btn_out.clicked.connect(self._choose_output); oph.addWidget(btn_out)
        ol.addLayout(oph); layout.addWidget(g2)

        self._status = QLabel("准备导出..."); layout.addWidget(self._status)
        self._progress = QProgressBar(); layout.addWidget(self._progress)

        self._log = QTextEdit(); self._log.setReadOnly(True); layout.addWidget(self._log, 1)

        bl = QHBoxLayout()
        self._export_btn = QPushButton("开始导出"); self._export_btn.setProperty("cssClass", "primary")
        self._export_btn.clicked.connect(self._start_export); bl.addWidget(self._export_btn)
        btn_cancel = QPushButton("取消"); btn_cancel.clicked.connect(self._cancel); bl.addWidget(btn_cancel)
        layout.addLayout(bl)

        self._set_default_output()
        if clips_path and os.path.exists(clips_path): self._load_clips_file(clips_path)

    def _set_default_output(self):
        if not self._video_path: return
        vd = os.path.dirname(self._video_path); vn = os.path.splitext(os.path.basename(self._video_path))[0]
        self._output_path = os.path.join(vd, f"{vn}_highlights.mp4"); self._out_edit.setText(self._output_path)

    def _choose_output(self):
        vd = os.path.dirname(self._video_path) if self._video_path else ""
        vn = os.path.splitext(os.path.basename(self._video_path))[0] if self._video_path else "highlights"
        path, _ = QFileDialog.getSaveFileName(self, "保存剪辑", os.path.join(vd, f"{vn}_highlights.mp4"), "MP4 (*.mp4)")
        if path:
            if not path.endswith(".mp4"): path += ".mp4"
            self._output_path = path; self._out_edit.setText(path)

    def _load_clips(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择剪辑数据", "", "JSON文件 (*.json)")
        if path: self._load_clips_file(path)

    def _load_clips_file(self, path: str):
        try:
            with open(path, "r", encoding="utf-8") as f: data = json.load(f)
            self._clip_ranges = [TimeRange(s, e) for s, e in data.get("clip_ranges", [])]
            self._clips_path = path; self._clips_label.setText(f"片段: {len(self._clip_ranges)} 个")
            self._log_msg(f"已加载: {len(self._clip_ranges)} 个片段")
        except Exception as e: QMessageBox.critical(self, "错误", str(e))

    def _start_export(self):
        if not self._output_path: QMessageBox.warning(self, "错误", "请选择输出路径"); return
        if not self._clip_ranges: QMessageBox.warning(self, "错误", "没有可导出的片段"); return

        self._export_btn.setEnabled(False); self._progress.setValue(0)
        config = ExportConfig(output_path=self._output_path)
        self._worker = ExportWorker(self._video_path, self._clip_ranges, config)
        self._worker.progress.connect(lambda p, m: (self._progress.setValue(p), self._status.setText(m)))
        self._worker.log.connect(lambda m, t: self._log_msg(m))
        self._worker.finished.connect(self._on_export_done)
        self._worker.start()

    def _on_export_done(self, success: bool, message: str):
        self._export_btn.setEnabled(True)
        if success: QMessageBox.information(self, "完成", message)
        else: QMessageBox.warning(self, "失败", message)

    def _cancel(self):
        if self._worker: self._worker.cancel(); self._export_btn.setEnabled(True)

    def _log_msg(self, msg: str):
        self._log.append(msg)
        c = self._log.textCursor(); c.movePosition(QTextCursor.End); self._log.setTextCursor(c)
