import cv2
import numpy as np
from EasyOCR_1_7_2.easyocr import Reader
import os


class InteractiveOCR:
    def __init__(self, image_path, languages=None):
        if languages is None:
            languages = ['ch_sim', 'en']

        self.image_path = image_path
        self.image = cv2.imread(image_path)
        if self.image is None:
            raise ValueError(f"无法加载图像: {image_path}")
        OCRmodel_path = "/EasyOCR_1_7_2/models"
        self.clone = self.image.copy()
        self.rectangles = []
        self.current_rect = None
        self.start_point = None
        self.drawing = False


        # 创建窗口并设置鼠标回调
        cv2.namedWindow(winname="Image",flags=cv2.WINDOW_NORMAL)
        # cv2.imshow(image_path)
        cv2.setMouseCallback("Image", self.mouse_callback)

        self.ocr_reader = Reader(lang_list=languages,
                             model_storage_directory=OCRmodel_path,
                             download_enabled=False)  # 这只需要运行一次即可将模型加载到内存中
    def mouse_callback(self, event, x, y, flags, param):
        """处理鼠标事件"""
        if event == cv2.EVENT_LBUTTONDOWN:
            # 开始绘制新矩形
            self.drawing = True
            self.start_point = (x, y)
            self.current_rect = [x, y, x, y]

        elif event == cv2.EVENT_MOUSEMOVE:
            # 更新当前矩形
            if self.drawing:
                self.current_rect[2] = x
                self.current_rect[3] = y
                # 实时显示绘制中的矩形
                self.update_display()

        elif event == cv2.EVENT_LBUTTONUP:
            # 完成矩形绘制
            self.drawing = False
            end_point = (x, y)

            # 确保矩形有有效尺寸
            min_size = 10
            if abs(self.start_point[0] - end_point[0]) > min_size and \
                    abs(self.start_point[1] - end_point[1]) > min_size:
                # 确保坐标正确顺序 (左上角, 右下角)
                x1 = min(self.start_point[0], end_point[0])
                y1 = min(self.start_point[1], end_point[1])
                x2 = max(self.start_point[0], end_point[0])
                y2 = max(self.start_point[1], end_point[1])

                self.rectangles.append((x1, y1, x2, y2))

            self.current_rect = None
            self.update_display()

    def update_display(self):
        """更新显示图像和所有矩形"""
        display_image = self.clone.copy()

        # 绘制所有已完成的矩形
        for i, (x1, y1, x2, y2) in enumerate(self.rectangles):
            cv2.rectangle(display_image, (x1, y1), (x2, y2), (0, 255, 0), 2)
            cv2.putText(display_image, f'R{i + 1}', (x1, y1 - 5),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1)

        # 绘制当前正在绘制的矩形
        if self.current_rect is not None:
            x1, y1, x2, y2 = self.current_rect
            cv2.rectangle(display_image, (x1, y1), (x2, y2), (0, 0, 255), 2)

        cv2.imshow("Image", display_image)

    def run(self):
        """主运行循环"""
        print("使用说明:")
        print("1. 按住鼠标左键并拖动绘制矩形")
        print("2. 按 'r' 识别所有框选区域的文本")
        print("3. 按 'c' 清除所有框选")
        print("4. 按 's' 保存所有裁剪区域")
        print("5. 按 'q' 退出")

        self.update_display()

        while True:
            key = cv2.waitKey(1) & 0xFF

            # 识别文本
            if key == ord('r'):
                self.recognize_text()

            # 清除所有矩形
            elif key == ord('c'):
                self.rectangles = []
                self.clone = self.image.copy()
                self.update_display()

            # 保存所有裁剪区域
            elif key == ord('s'):
                self.save_cropped_regions()

            # 退出
            elif key == ord('q'):
                break

        cv2.destroyAllWindows()

    def recognize_text(self):
        """识别所有框选区域的文本"""
        if not self.rectangles:
            print("没有框选的区域！")
            return

        print("\n=== 识别结果 ===")
        for i, rect in enumerate(self.rectangles):
            x1, y1, x2, y2 = rect
            cropped = self.image[y1:y2, x1:x2]

            # 识别文本
            results = self.ocr_reader.readtext(cropped)

            # 处理识别结果
            if results:
                print(f"区域 {i + 1} 识别结果:")
                for result in results:
                    text = result[1]
                    confidence = result[2]
                    print(f"  - {text} (置信度: {confidence:.2f})")
            else:
                print(f"区域 {i + 1} 未识别到文本")

            # 显示裁剪区域
            cv2.imshow(f"Region {i + 1}", cropped)

        print("================")

    def save_cropped_regions(self):
        """保存所有裁剪区域到文件"""
        if not self.rectangles:
            print("没有框选的区域！")
            return

        # 创建保存目录
        save_dir = "cropped_regions"
        os.makedirs(save_dir, exist_ok=True)

        for i, rect in enumerate(self.rectangles):
            x1, y1, x2, y2 = rect
            cropped = self.image[y1:y2, x1:x2]

            # 保存图像
            filename = os.path.join(save_dir, f"region_{i + 1}.png")
            cv2.imwrite(filename, cropped)
            print(f"已保存区域 {i + 1} 到 {filename}")

        print(f"所有区域已保存到 {save_dir} 目录")


if __name__ == "__main__":
    # 使用说明
    print("=" * 50)
    print("交互式区域选择OCR工具")
    print("=" * 50)

    # 设置图像路径
    image_path = "/Video/chinese.jpg"

    if not image_path:
        print("使用默认示例图片...")
        image_path = "example.jpg"  # 替换为你自己的默认图片路径
    elif not os.path.exists(image_path):
        print(f"文件不存在: {image_path}")
        print("使用默认示例图片...")
        image_path = "example.jpg"  # 替换为你自己的默认图片路径

    # 初始化并运行
    try:
        app = InteractiveOCR(image_path)
        app.run()
    except Exception as e:
        print(f"发生错误: {e}")