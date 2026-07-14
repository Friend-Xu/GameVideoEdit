"""流水线诊断: 测量 CPU 产出 vs OCR 消费的真实吞吐。

回答: CPU 喂饱 OCR 了吗? 增加 OCR 实例有意义吗?

用法: D:/Workspace/GameVideoEdit/runtime/python.exe tests/diagnose_pipeline.py
"""

import sys, time, json, random
from pathlib import Path
from collections import defaultdict

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

import numpy as np
import torch

from app.core.detector import OCRDetector, FramePrefetcher, TextPresenceGate
from app.core.player import VideoPlayer

VIDEO = str(PROJECT / "data" / "videos" / "NQLS5621.MP4")


def stats(values, unit="ms"):
    if not values: return "no data"
    a = np.array(values)
    return f"{np.mean(a):7.1f}{unit} ±{np.std(a):5.1f} (n={len(a)}, [{np.min(a):.1f}, {np.max(a):.1f}])"


def load_rois(img_w=1920, img_h=1334):
    """从模板加载 ROI，返回 list[{x,y,w,h,label}]。"""
    tp = PROJECT / "config" / "roi_templates.json"
    with open(tp) as f:
        tmpl = json.load(f)
    did = tmpl.get("default", "Ipad")
    regions = tmpl.get("templates", {}).get(did, [])
    rois = []
    for r in regions:
        w = int(r["width"] * img_w)
        h = int(r["height"] * img_h)
        x = int(r["center_x"] * img_w - w // 2)
        y = int(r["center_y"] * img_h - h // 2)
        rois.append({"id": r["id"], "label": r["label"], "x": x, "y": y, "w": w, "h": h})
    return rois


def measure_decode():
    """阶段 1: 测量真实帧解码耗时。"""
    print("── 阶段 1: 帧解码耗时 ──")
    p = VideoPlayer()
    info = p.open(VIDEO)
    fps, total = info.fps, info.total_frames
    print(f"  {info.width}x{info.height}  fps={fps:.1f}  frames={total}")

    rng = np.random.default_rng(42)
    samples = sorted(rng.integers(0, total - 1, 100))

    times = []
    for fn in samples:
        t0 = time.perf_counter()
        _ = p.seek(fn)
        times.append((time.perf_counter() - t0) * 1000)
    p.close()
    print(f"  seek+decode: {stats(times)}")
    return {"mean_decode_ms": float(np.mean(times)), "n": len(times)}


def measure_ocr():
    """阶段 2: 测量 GPU OCR 单帧推理耗时。"""
    print("\n── 阶段 2: GPU OCR 推理耗时 ──")
    detector = OCRDetector(gpu=True)

    p = VideoPlayer(); info = p.open(VIDEO); p.close()
    rois = load_rois(info.width, info.height)

    # 找有文字帧
    p2 = VideoPlayer(); p2.open(VIDEO)
    gate = TextPresenceGate()
    found = []
    for _ in range(300):
        fn = random.randint(0, info.total_frames - 1)
        frame = p2.seek(fn)
        for roi in rois:
            if roi['label'] != '击杀信息': continue
            x, y, w, h = roi['x'], roi['y'], roi['w'], roi['h']
            has_t, _ = gate.check(frame[y:y+h, x:x+w])
            if has_t:
                found.append((fn, frame))
                break
        if len(found) >= 8: break
    p2.close()

    if not found:
        print("  警告: 没找到有击杀文字的画面")
        return

    print(f"  300 次采样找到 {len(found)} 帧有击杀文字")

    for roi in rois:
        x, y, w, h = roi['x'], roi['y'], roi['w'], roi['h']
        ts_full, ts_raw = [], []
        for fn, frame in found:
            img = frame[y:y+h, x:x+w]
            t0 = time.perf_counter()
            _ = detector.detect(img)
            if torch.cuda.is_available(): torch.cuda.synchronize()
            ts_full.append((time.perf_counter() - t0) * 1000)

            prepped = detector._preprocess(img)
            t0 = time.perf_counter()
            _ = detector.detect_raw(prepped)
            if torch.cuda.is_available(): torch.cuda.synchronize()
            ts_raw.append((time.perf_counter() - t0) * 1000)

        if not ts_full: continue
        print(f"  {roi['label']} ({w}x{h}):")
        print(f"    完整(含CLAHE): {stats(ts_full)}")
        print(f"    纯OCR(不含预处理): {stats(ts_raw)}")


def measure_queue():
    """阶段 3: 模拟真实流水线，跟踪队列深度。"""
    print("\n── 阶段 3: FramePrefetcher → OCR 队列动态 ──")
    from app.core.annotator import AnnotationStore, PixelROI

    p = VideoPlayer(); info = p.open(VIDEO); p.close()
    fps, total = info.fps, info.total_frames
    dict_rois = load_rois(info.width, info.height)
    # FramePrefetcher 需要 PixelROI 对象
    rois = [PixelROI(x=r['x'], y=r['y'], w=r['w'], h=r['h'], label=r['label'])
            for r in dict_rois]
    detector = OCRDetector(gpu=True)

    n_frames = 150
    flist = [int(i * 1.0 * fps) for i in range(n_frames)
             if int(i * 1.0 * fps) < total]

    for nw in [2, 4, 8]:
        print(f"\n  --- CPU workers={nw} ---")
        prefetcher = FramePrefetcher(VIDEO, detector, rois, fps, num_workers=nw)
        prefetcher.start()

        t0 = time.perf_counter()
        for fn in flist:
            prefetcher.submit(fn)

        depths, ocr_n, skip_n, ocr_times = [], 0, 0, []
        for i in range(len(flist)):
            item = prefetcher.next_result()
            if item is None: break
            fn, prepped, ts, has_text, _ = item
            depths.append(prefetcher.pending)

            if has_text and prepped:
                ocr_n += 1
                t_ocr = time.perf_counter()
                for roi_img, roi_obj in prepped:
                    _ = detector.detect_raw(roi_img)
                if torch.cuda.is_available(): torch.cuda.synchronize()
                ocr_times.append((time.perf_counter() - t_ocr) * 1000)
            else:
                skip_n += 1

        total_ms = (time.perf_counter() - t0) * 1000
        prefetcher.stop(); del prefetcher

        throughput = len(flist) / (total_ms / 1000) if total_ms > 0 else 0
        print(f"    帧数: {len(flist)}  有文字: {ocr_n}  跳过: {skip_n}")
        print(f"    总耗时: {total_ms:.0f}ms  吞吐: {throughput:.1f} fps")
        print(f"    队列深度: mean={np.mean(depths):.1f}  max={max(depths)}  min={min(depths)}")
        if ocr_times:
            print(f"    OCR 耗时: {stats(ocr_times)}")


def main():
    print("=" * 60)
    print("  流水线诊断: CPU vs OCR 吞吐")
    print("=" * 60)
    if not Path(VIDEO).exists():
        print(f"  [错误] 找不到: {VIDEO}"); return 1

    print(f"  CUDA: {torch.cuda.is_available()}")
    if torch.cuda.is_available():
        print(f"  GPU: {torch.cuda.get_device_name(0)}")

    measure_decode()
    measure_ocr()
    measure_queue()

    print("\n" + "=" * 60)
    print("  解读方法")
    print("=" * 60)
    print("""
  解码时间 vs OCR 时间:
    - 解码 > OCR → CPU 瓶颈, 增加 OCR 没用
    - OCR > 解码 → OCR 瓶颈, 增加 OCR 实例有用

  队列深度:
    - 始终为 0 → OCR 在等 CPU 产出 (CPU 瓶颈)
    - 持续增长 → CPU 产出过量 (OCR 瓶颈, 可加 OCR)
    - 在 1-3 波动 → 基本平衡

  OCR 占比 (有文字帧/总帧):
    - 低 (<5%) → 大部分帧被门控跳过, CPU 压力小
    - 高 (>20%) → 密集事件区域, CPU 忙
    """)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
