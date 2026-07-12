#!/usr/bin/env python3
"""CLI 调试工具 — 复用 core 模块，无需 GUI。

用法:
    python cli.py detect <video> [--roi FILE] [-o result.json]
    python cli.py match "你使用 M416 击倒了 玩家"
    python cli.py info <video>
    python cli.py verify
    python cli.py export <video> <results.json> [-o output.mp4]
"""

import argparse
import json
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
_ENGINES = _ROOT / "engines"
if str(_ENGINES) not in sys.path:
    sys.path.insert(0, str(_ENGINES))


def _setup_env() -> None:
    os.environ["PYTHONIOENCODING"] = "utf-8"
    os.environ["PYTHONUTF8"] = "1"


def _load_matcher(config_path: str | None = None):
    from app.core.keywords import KeywordMatcher
    if config_path:
        return KeywordMatcher.from_yaml(config_path)
    from app.utils.paths import config_dir
    kw = config_dir() / "keywords.yaml"
    if kw.exists():
        return KeywordMatcher.from_yaml(str(kw))
    sys.exit("错误: 未找到 keywords.yaml，请用 --config 指定")


def _load_annotations(video_path: str, roi_file: str | None, roi_region: str | None):
    """加载或创建 AnnotationStore。优先级: --roi > --roi-region > 自动 .roi.json"""
    from app.core.annotator import AnnotationStore
    from app.core.player import VideoPlayer

    if roi_file:
        if os.path.exists(roi_file):
            return AnnotationStore.load_json(roi_file)
        sys.exit(f"错误: ROI 文件不存在: {roi_file}")

    if roi_region:
        parts = roi_region.split(",")
        if len(parts) < 4:
            sys.exit("错误: --roi-region 格式: X,Y,W,H[,LABEL]")
        x, y, w, h = int(parts[0]), int(parts[1]), int(parts[2]), int(parts[3])
        label = parts[4] if len(parts) > 4 else "ROI"
        player = VideoPlayer()
        info = player.open(video_path)
        player.close()
        store = AnnotationStore(
            video_path=video_path, width=info.width, height=info.height,
            fps=info.fps, total_frames=info.total_frames,
        )
        store.add_region(label, x, y, w, h, info.width, info.height)
        return store

    auto_roi = os.path.join(os.path.dirname(video_path),
                            f"{os.path.splitext(os.path.basename(video_path))[0]}.roi.json")
    if os.path.exists(auto_roi):
        return AnnotationStore.load_json(auto_roi)

    sys.exit("错误: 没有 ROI 数据。请用 --roi 或 --roi-region 指定，或先生成 .roi.json")


def cmd_verify(args):
    """验证运行环境"""
    from app.main import verify_environment
    _setup_env()
    print("=" * 40)
    print("  环境验证")
    print("=" * 40)
    issues = verify_environment()
    if not issues:
        print("全部通过")
        return 0
    print(f"发现 {len(issues)} 个问题:")
    for i in issues:
        print(f"  ! {i}")
    return 0


def cmd_info(args):
    """查看视频信息"""
    from app.core.player import VideoPlayer
    _setup_env()
    player = VideoPlayer()
    info = player.open(args.video)
    duration = info.total_frames / info.fps
    player.close()
    print(f"文件:     {args.video}")
    print(f"分辨率:   {info.width} x {info.height}")
    print(f"帧率:     {info.fps:.2f} fps")
    print(f"总帧数:   {info.total_frames}")
    print(f"时长:     {duration:.1f}s ({int(duration // 60)}分{int(duration % 60)}秒)")
    roi_file = os.path.splitext(args.video)[0] + ".roi.json"
    if os.path.exists(roi_file):
        from app.core.annotator import AnnotationStore
        store = AnnotationStore.load_json(roi_file)
        print(f"ROI 标注:  {roi_file} ({store.region_count} 个区域)")
    proj_file = os.path.splitext(args.video)[0] + ".project.json"
    if os.path.exists(proj_file):
        with open(proj_file, "r", encoding="utf-8") as f:
            proj = json.load(f)
        clips = proj.get("clips", [])
        last = proj.get("last_detection", "")[:19]
        print(f"识别结果:  {proj_file} ({len(clips)} 个片段)")
        if last:
            print(f"识别时间:  {last}")
    return 0


def cmd_match(args):
    """测试关键词匹配"""
    _setup_env()
    matcher = _load_matcher(args.config)
    if args.interactive:
        print("交互式关键词匹配 (输入 'q' 退出)")
        print(f"已加载 {len(matcher._patterns)} 条规则")
        while True:
            try:
                text = input("\n> ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if text.lower() == "q":
                break
            result = matcher.match(text)
            if result:
                print(f"  命中: {result.pattern_id}")
                print(f"  Action: {result.action}  Actor: {result.actor}")
                print(f"  Extract: {result.extract}")
            else:
                print("  未匹配")
    else:
        if not args.text:
            sys.exit("错误: 请指定 --text 或 --interactive")
        result = matcher.match(args.text)
        if result:
            print(json.dumps({
                "matched": True,
                "pattern_id": result.pattern_id,
                "action": result.action,
                "actor": result.actor,
                "raw_text": result.raw_text,
                "extract": result.extract,
            }, ensure_ascii=False, indent=2))
        else:
            print(json.dumps({"matched": False, "raw_text": args.text}, ensure_ascii=False))
    return 0


def cmd_detect(args):
    """视频 OCR 识别"""
    _setup_env()
    matcher = _load_matcher(args.config)
    annotations = _load_annotations(args.video, args.roi, args.roi_region)

    print(f"视频: {args.video}")
    print(f"ROI:  {annotations.region_count} 个区域")
    print(f"模式: {args.mode}  "
          f"间隔: {args.interval if args.mode == 'time' else args.skip_frames}")

    from app.core.detector import OCRDetector, DetectionEngine
    import numpy as np

    print("加载 OCR 模型...")
    detector = OCRDetector(gpu=not args.cpu)

    if not args.cpu:
        detector.detect(np.zeros((64, 64, 3), dtype=np.uint8))
        print("模型预热完成")

    actors = None
    if args.actors:
        actors = set(a.strip() for a in args.actors.split(","))

    pipeline_mode = args.pipeline

    print(f"流水线模式: {pipeline_mode}")

    if pipeline_mode == "pool":
        from app.core.detector import DetectionPipeline
        from app.core.player import VideoPlayer
        player = VideoPlayer()
        info = player.open(args.video)
        player.close()
        pipeline = DetectionPipeline(
            matcher, detector,
            padding_before=args.padding_before,
            padding_after=args.padding_after,
            skip_frames=args.skip_frames,
            mode=args.mode,
            interval_sec=args.interval,
            allowed_actors=actors,
            cpu_workers=args.cpu_workers,
            gpu_workers=args.gpu_workers,
        )
        print("开始检测 (Pool 模式)...")
        time_ranges, report = pipeline.run_full(
            video_path=args.video,
            annotations=annotations,
            start_frame=args.start_frame,
            end_frame=args.end_frame if args.end_frame >= 0 else None,
            progress_cb=lambda pct: print(f"\r  进度: {pct:.1f}%", end="", flush=True),
            detected_cb=lambda ts, text: print(f"\n  检测 [{ts:.1f}s]: {text[:60]}"),
        )
    else:
        engine = DetectionEngine(
            matcher, detector,
            padding_before=args.padding_before,
            padding_after=args.padding_after,
            skip_frames=args.skip_frames,
            merge_gap=args.merge_gap,
            mode=args.mode,
            interval_sec=args.interval,
            post_detect_skip_sec=args.post_detect_skip,
            allowed_actors=actors,
        )
        print("开始检测 (Legacy 模式)...")
        time_ranges, report = engine.run_full(
            video_path=args.video,
            annotations=annotations,
            start_frame=args.start_frame,
            end_frame=args.end_frame if args.end_frame >= 0 else None,
            progress_cb=lambda pct: print(f"\r  进度: {pct:.1f}%", end="", flush=True),
            detected_cb=lambda ts, text: print(f"\n  检测 [{ts:.1f}s]: {text[:60]}"),
        )

    print(f"\r  进度: 100.0%")
    print(f"完成: 找到 {len(time_ranges)} 个片段")

    # ── 后处理: 边界精化 + 细胞分裂 ──
    if args.refine or args.cell_divide:
        from app.core.coarse_to_fine import BoundaryRefiner, binseg_event_search
        from app.core.player import VideoPlayer
        player = VideoPlayer()
        info = player.open(args.video)
        fps = info.fps
        player.close()
        rois = annotations.to_pixel_rois(info.width, info.height)
        kill_idx = 0
        for i, roi in enumerate(rois):
            if roi.label == '击杀信息':
                kill_idx = i
                break

    if args.refine:
        refiner = BoundaryRefiner(args.video, rois, kill_idx)
        refined = []
        for r in time_ranges:
            rr = refiner.refine(r, fps, search_window=args.refine_search_window)
            refined.append(rr)
        time_ranges = refined
        print(f"边界精化完成: {len(time_ranges)} 个片段")

    if args.cell_divide:
        all_results = []
        for r in time_ranges:
            if r.end_sec - r.start_sec < args.cell_min_gap:
                all_results.append(r)
                continue
            sub, merged_n, short_n, short_info = binseg_event_search(
                args.video, rois, kill_idx,
                r.start_sec, r.end_sec, fps,
                detector, matcher,
                min_segment=args.cell_min_gap,
                search_window=args.refine_search_window,
                allowed_actors=actors,
            )
            if merged_n:
                print(f"  [合并] {merged_n} 个重叠事件")
            if short_n:
                print(f"  [过滤] {short_n} 个短事件: {', '.join(s['action'][:4] for s in short_info)}")
            if sub:
                all_results.extend(sub)
            else:
                all_results.append(r)
        time_ranges = all_results
        print(f"细胞分裂完成: {len(time_ranges)} 个片段")

    from app.core.player import VideoPlayer as _VP
    from app.core.detector import DetectionReport
    _vp = _VP(); _vinfo = _vp.open(args.video); _vp.close()
    output = {
        "version": "2.0",
        "last_detection": "",
        "source": {
            "path": args.video,
            "width": _vinfo.width,
            "height": _vinfo.height,
            "fps": _vinfo.fps,
            "total_frames": _vinfo.total_frames,
        },
        "detection": {
            "mode": args.mode,
            "interval_sec": args.interval,
            "skip_frames": args.skip_frames,
            "post_detect_skip_sec": args.post_detect_skip,
            "padding_before": args.padding_before,
            "padding_after": args.padding_after,
            "merge_gap": args.merge_gap,
            "num_threads": 1,
            "rotation": 0,
        },
        "results": [
            {
                "start_sec": r.start_sec,
                "end_sec": r.end_sec,
                "pattern_id": r.pattern_id,
                "action": r.action,
                "actor": r.actor,
                "source": getattr(r, 'source', 'text'),
            }
            for r in time_ranges
        ],
        "export": {},
    }

    if not args.output:
        args.output = os.path.splitext(args.video)[0] + ".project.json"

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(output, f, ensure_ascii=False, indent=2)
        print(f"结果已保存: {args.output}")
    else:
        print(json.dumps(output, ensure_ascii=False, indent=2))

    log_path = (args.output or "").replace(".project.json", ".detection_log.json")
    if log_path and log_path != args.output:
        report_rois = []
        for i, r in enumerate(annotations.regions):
            report_rois.append({"id": i, "label": r.label})
        pipeline = "pool" if pipeline_mode == "pool" else "legacy"
        report = DetectionReport(
            video_path=os.path.abspath(args.video),
            video_width=_vinfo.width, video_height=_vinfo.height,
            video_fps=_vinfo.fps,
            video_duration_sec=_vinfo.total_frames / max(_vinfo.fps, 1),
            config={
                "pipeline": pipeline,
                "mode": args.mode,
                "interval_sec": args.interval,
                "skip_frames": args.skip_frames,
                "padding_before": args.padding_before,
                "padding_after": args.padding_after,
            },
            rois=report_rois,
        )
        report.results = [
            {"start_sec": r.start_sec, "end_sec": r.end_sec,
             "action": r.action, "actor": r.actor,
             "pattern_id": r.pattern_id,
             "source": getattr(r, 'source', 'text')}
            for r in time_ranges
        ]
        report.summary.detections_after_merge = len(time_ranges)
        try:
            report.save_json(log_path)
            print(f"检测报告已保存: {log_path}")
        except Exception as e:
            print(f"保存检测报告失败: {e}")

    return 0


def cmd_export(args):
    """导出视频片段"""
    _setup_env()
    with open(args.results, "r", encoding="utf-8") as f:
        data = json.load(f)

    clips = data.get("clips", [])
    if not clips:
        sys.exit("错误: 结果文件中没有片段")

    from app.core.detector import TimeRange
    from app.core.exporter import ExportConfig, VideoExporter

    ranges = [TimeRange(c["start_sec"], c["end_sec"]) for c in clips]
    output = args.output or os.path.join(
        os.path.dirname(args.video),
        f"{os.path.splitext(os.path.basename(args.video))[0]}_highlights.mp4",
    )

    print(f"导出 {len(ranges)} 个片段 -> {output}")
    exporter = VideoExporter()
    config = ExportConfig(output_path=output)
    result = exporter.combine_clips(args.video, ranges, config)

    if result.success:
        print(f"导出成功: {output}")
    else:
        sys.exit(f"导出失败: {result.message}")
    return 0


def main() -> int:
    _setup_env()
    parser = argparse.ArgumentParser(
        description="GameVideoEdit CLI — 视频 OCR 检测与导出工具")
    sub = parser.add_subparsers(dest="command", help="子命令")

    # ---- detect ----
    p = sub.add_parser("detect", help="视频 OCR 识别")
    p.add_argument("video", help="视频文件路径")
    p.add_argument("--roi", help="ROI 标注文件 (.roi.json)")
    p.add_argument("--roi-region", help="手动指定 ROI: X,Y,W,H[,LABEL]")
    p.add_argument("--start-frame", type=int, default=0, help="起始帧 (默认 0)")
    p.add_argument("--end-frame", type=int, default=-1, help="结束帧 (默认视频末)")
    p.add_argument("--config", help="keywords.yaml 路径")
    p.add_argument("-o", "--output", help="结果输出 JSON 文件")
    p.add_argument("--mode", choices=["time", "frame"], default="time", help="OCR 模式")
    p.add_argument("--interval", type=float, default=1.0, help="time 模式采样间隔(秒)")
    p.add_argument("--skip-frames", type=int, default=3, help="frame 模式跳过帧数")
    p.add_argument("--post-detect-skip", type=float, default=0.3, help="命中后跳过秒数")
    p.add_argument("--padding-before", type=float, default=10.0, help="片段前置时间(秒)")
    p.add_argument("--padding-after", type=float, default=10.0, help="片段后置时间(秒)")
    p.add_argument("--merge-gap", type=float, default=30.0, help="合并相邻片段的最大间隔(秒)")
    p.add_argument("--actors", help="过滤角色: 自己,队友 (逗号分隔，默认不过滤)")
    p.add_argument("--cpu", action="store_true", help="使用 CPU 模式")
    p.add_argument("--pipeline", choices=["legacy", "pool"],
                   default="legacy", help="流水线模式 (默认 legacy)")
    p.add_argument("--cpu-workers", type=int, default=3, help="Pool 模式 CPU 线程数")
    p.add_argument("--gpu-workers", type=int, default=2, help="Pool 模式 GPU 线程数")
    p.add_argument("--refine", action="store_true",
                   help="后处理: 二分搜索精化事件边界至 ±1 帧")
    p.add_argument("--refine-search-window", type=float, default=2.0,
                   help="二分搜索范围(秒)")
    p.add_argument("--cell-divide", action="store_true",
                   help="后处理: BinSeg 细胞分裂递归搜索子事件")
    p.add_argument("--cell-min-gap", type=float, default=2.0,
                   help="细胞分裂最小间隔(秒)")

    # ---- match ----
    p = sub.add_parser("match", help="关键词匹配测试")
    p.add_argument("text", nargs="?", help="要匹配的文本")
    p.add_argument("-i", "--interactive", action="store_true", help="交互式模式")
    p.add_argument("--config", help="keywords.yaml 路径")

    # ---- info ----
    p = sub.add_parser("info", help="查看视频信息")
    p.add_argument("video", help="视频文件路径")

    # ---- verify ----
    sub.add_parser("verify", help="验证运行环境")

    # ---- export ----
    p = sub.add_parser("export", help="导出视频片段")
    p.add_argument("video", help="视频文件路径")
    p.add_argument("results", help="detect 命令输出的 JSON 结果文件")
    p.add_argument("-o", "--output", help="输出视频路径")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return 0

    commands = {
        "detect": cmd_detect,
        "match": cmd_match,
        "info": cmd_info,
        "verify": cmd_verify,
        "export": cmd_export,
    }
    cmd_fn = commands.get(args.command)
    if cmd_fn:
        return cmd_fn(args)
    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
