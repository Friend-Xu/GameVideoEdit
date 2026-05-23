import json
import cv2
import os
import numpy as np


def apply_rotation(image, rotation):
    """
    根据旋转角度旋转图像

    参数:
        image: 输入图像
        rotation: 旋转角度 (0, 90, 180, 270)

    返回:
        旋转后的图像
    """
    if rotation == 90:
        return cv2.rotate(image, cv2.ROTATE_90_CLOCKWISE)
    elif rotation == 180:
        return cv2.rotate(image, cv2.ROTATE_180)
    elif rotation == 270:
        return cv2.rotate(image, cv2.ROTATE_90_COUNTERCLOCKWISE)
    else:
        return image.copy()


def draw_regions_on_image(image, video_data, rotation=0):
    """
    在图像上绘制标记区域

    参数:
        image: 要绘制的OpenCV图像
        video_data: save_regions方法返回的数据结构
        rotation: 图像旋转角度 (0, 90, 180, 270)
    """
    # 获取视频尺寸信息
    width = video_data["width"]
    height = video_data["height"]

    # 应用旋转
    rotated_image = apply_rotation(image, rotation)

    # 如果需要旋转，调整尺寸
    if rotation in [90, 270]:
        width, height = height, width

    # 为不同标签设置不同颜色
    label_colors = {
        "击杀提示": (0, 255, 0),  # 绿色
        "生命值": (0, 0, 255),  # 红色
        "技能状态": (255, 0, 0),  # 蓝色
        "小地图": (255, 255, 0),  # 青色
        "默认": (0, 255, 255)  # 黄色
    }

    # 遍历所有区域并绘制
    for region in video_data["regions"]:
        # 获取区域数据
        region_id = region["id"]
        label = region["label"]
        cx = region["center_x"]
        cy = region["center_y"]
        w = region["width"]
        h = region["height"]

        # 计算绝对像素坐标
        abs_w = int(w * width)
        abs_h = int(h * height)
        center_x = int(cx * width)
        center_y = int(cy * height)

        # 计算边界框坐标
        x1 = max(0, center_x - abs_w // 2)
        y1 = max(0, center_y - abs_h // 2)
        x2 = min(width - 1, center_x + abs_w // 2)
        y2 = min(height - 1, center_y + abs_h // 2)

        # 获取标签颜色
        color = label_colors.get(label, label_colors["默认"])

        # 绘制边界框
        cv2.rectangle(
            img=rotated_image,
            pt1=(x1, y1),
            pt2=(x2, y2),
            color=color,
            thickness=2
        )

        # 添加标签文本
        label_text = f"{label} ID:{region_id}"
        # 智能调整文本位置（防止超出图像边界）
        text_y = y1 - 10 if y1 > 30 else y1 + 20
        cv2.putText(
            img=rotated_image,
            text=label_text,
            org=(x1, text_y),
            fontFace=cv2.FONT_HERSHEY_SIMPLEX,
            fontScale=0.7,
            color=color,
            thickness=2
        )

    return rotated_image


def draw_on_video_frame(video_path, video_data, frame_index=0, output_path=None):
    """
    在视频的指定帧上绘制区域并保存

    参数:
        video_path: 视频文件路径
        video_data: save_regions方法返回的数据结构
        frame_index: 要绘制的帧索引 (默认为第一帧)
        output_path: 输出图像路径 (如果为None则不保存)

    返回:
        绘制后的图像和原始图像（都应用了旋转）
    """
    # 打开视频文件
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        print(f"错误: 无法打开视频 {video_path}")
        return None, None

    # 设置帧位置
    cap.set(cv2.CAP_PROP_POS_FRAMES, frame_index)

    # 读取指定帧
    ret, frame = cap.read()
    if not ret:
        print(f"错误: 无法读取第 {frame_index} 帧")
        cap.release()
        return None, None

    # 获取旋转角度
    rotation = video_data.get("rotation", 0)

    # 创建旋转后的原始图像
    original_rotated = apply_rotation(frame, rotation)

    # 在帧上绘制区域（返回的图像已应用旋转）
    annotated_rotated = draw_regions_on_image(frame, video_data, rotation)

    # 保存结果
    if output_path:
        os.makedirs(os.path.dirname(output_path), exist_ok=True)

        # 保存标注图像（已旋转）
        cv2.imwrite(output_path, annotated_rotated)
        print(f"已保存标注图像到: {output_path}")

        # 保存原始图像（已旋转）
        base_name, ext = os.path.splitext(output_path)
        original_output_path = f"{base_name}_original{ext}"
        cv2.imwrite(original_output_path, original_rotated)
        print(f"已保存旋转后的原始图像到: {original_output_path}")

    # 释放资源
    cap.release()
    return annotated_rotated, original_rotated


def draw_on_entire_video(video_path, video_data, output_video_path):
    """
    在整个视频上绘制区域并保存新视频

    参数:
        video_path: 输入视频路径
        video_data: save_regions方法返回的数据结构
        output_video_path: 输出视频路径
    """
    # 打开输入视频
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        print(f"错误: 无法打开视频 {video_path}")
        return

    # 获取视频属性
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = cap.get(cv2.CAP_PROP_FPS)
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

    # 获取旋转角度
    rotation = video_data.get("rotation", 0)

    # 如果旋转角度是90或270，需要交换宽高
    if rotation in [90, 270]:
        width, height = height, width

    # 创建输出视频
    fourcc = cv2.VideoWriter_fourcc(*'mp4v')
    out = cv2.VideoWriter(output_video_path, fourcc, fps, (width, height))

    frame_count = 0
    print(f"开始处理视频: {video_path}")
    print(f"总帧数: {total_frames}, 分辨率: {width}x{height}, FPS: {fps:.2f}")

    while True:
        ret, frame = cap.read()
        if not ret:
            break

        # 在帧上绘制区域
        annotated_frame = draw_regions_on_image(frame, video_data, rotation)

        # 写入输出视频
        out.write(annotated_frame)

        # 显示进度
        frame_count += 1
        if frame_count % 100 == 0:
            print(f"处理进度: {frame_count}/{total_frames} 帧 ({frame_count / total_frames * 100:.1f}%)")

    # 释放资源
    cap.release()
    out.release()
    print(f"视频处理完成! 已保存到: {output_video_path}")


# 使用示例
if __name__ == "__main__":
    # 假设这是从save_regions方法获取的数据
    with open(r'/Video/123eplay_Final1735458759_annotations.json', 'r', encoding='utf-8') as f:
        video_data = json.load(f)

    # 选项1: 在指定帧上绘制并保存
    output_image_path = "/Output/annotated_frame.jpg"
    annotated_image, original_image = draw_on_video_frame(
        video_path=video_data["video_path"],
        video_data=video_data,
        frame_index=1000,
        output_path=output_image_path
    )

    # 显示结果
    if annotated_image is not None and original_image is not None:
        # 缩放以便显示
        display_height = 800
        scale = display_height / annotated_image.shape[0]
        display_width = int(annotated_image.shape[1] * scale)

        # 创建并排对比图像
        annotated_resized = cv2.resize(annotated_image, (display_width, display_height))
        original_resized = cv2.resize(original_image, (display_width, display_height))
        comparison_image = np.hstack((original_resized, annotated_resized))

        # 添加标题
        title_height = 50
        comparison_image = cv2.copyMakeBorder(comparison_image, title_height, 0, 0, 0, cv2.BORDER_CONSTANT,
                                              value=(0, 0, 0))
        cv2.putText(comparison_image, "旋转后的原始图像", (50, 30), cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 255), 2)
        cv2.putText(comparison_image, "标注图像", (display_width + 50, 30), cv2.FONT_HERSHEY_SIMPLEX, 1,
                    (255, 255, 255), 2)

        # 显示对比图像
        cv2.imshow("旋转后的原始图像 vs 标注图像", comparison_image)
        cv2.waitKey(0)
        cv2.destroyAllWindows()

        # 保存对比图像
        base_name, ext = os.path.splitext(output_image_path)
        comparison_path = f"{base_name}_comparison{ext}"
        cv2.imwrite(comparison_path, comparison_image)
        print(f"已保存对比图像到: {comparison_path}")

    # 选项2: 在整个视频上绘制并保存
    output_video_path = "D:/Github/GameVideoEdit/Output/annotated_video.mp4"
    # 取消下面一行的注释以处理整个视频
    # draw_on_entire_video(video_data["video_path"], video_data, output_video_path)