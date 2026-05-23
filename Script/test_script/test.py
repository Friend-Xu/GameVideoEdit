import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
import numpy as np
from EasyOCR_1_7_2.easyocr import Reader
import os
from PIL import Image
import matplotlib

matplotlib.use('TkAgg')  # 使用 Tkinter 作为后端


class InteractiveOCR:
    def __init__(self, image_path, languages=None):
        if languages is None:
            languages = ['ch_sim', 'en']
        self.image_path = image_path
        self.image = plt.imread(image_path)
        if self.image is None:
            raise ValueError(f"无法加载图像: {image_path}")

        self.fig, self.ax = plt.subplots(figsize=(12, 8))
        self.ax.imshow(self.image)
        self.rectangles = []  # 存储所有矩形 [(x1, y1, x2, y2)]
        self.current_rect = None  # 当前正在绘制的矩形
        self.start_point = None  # 矩形起始点

        # 连接事件处理器
        self.fig.canvas.mpl_connect('button_press_event', self.on_press)
        self.fig.canvas.mpl_connect('motion_notify_event', self.on_motion)
        self.fig.canvas.mpl_connect('button_release_event', self.on_release)
        self.fig.canvas.mpl_connect('key_press_event', self.on_key)

        OCRmodel_path = "/EasyOCR_1_7_2/models"
        self.ocr_reader = Reader(lang_list=languages,
                                 model_storage_directory=OCRmodel_path,
                                 download_enabled=False)  # 这只需要运行一次即可将模型加载到内存中
        self.title = plt.title("交互式OCR工具 - 按 'h' 查看帮助", fontsize=12)
        plt.axis('off')
        plt.tight_layout()

        # 帮助文本
        self.help_text = "使用说明:\n" \
                         "1. 按住鼠标左键并拖动绘制矩形\n" \
                         "2. 按 'r' 识别所有框选区域的文本\n" \
                         "3. 按 'c' 清除所有框选\n" \
                         "4. 按 's' 保存所有裁剪区域\n" \
                         "5. 按 'q' 退出"

    def on_press(self, event):
        """鼠标按下事件"""
        if event.inaxes != self.ax or event.button != 1:
            return
        self.start_point = (event.xdata, event.ydata)
        self.current_rect = Rectangle((0, 0), 0, 0,
                                      edgecolor='red',
                                      facecolor='none',
                                      linewidth=2,
                                      linestyle='--')
        self.ax.add_patch(self.current_rect)
        self.fig.canvas.draw()

    def on_motion(self, event):
        """鼠标移动事件"""
        if self.start_point is None or event.inaxes != self.ax:
            return
        x0, y0 = self.start_point
        x1, y1 = event.xdata, event.ydata

        # 更新矩形位置和大小
        self.current_rect.set_width(x1 - x0)
        self.current_rect.set_height(y1 - y0)
        self.current_rect.set_xy((x0, y0))
        self.fig.canvas.draw()

    def on_release(self, event):
        """鼠标释放事件"""
        if self.start_point is None or event.button != 1:
            return

        x0, y0 = self.start_point
        x1, y1 = event.xdata, event.ydata

        # 确保矩形有有效尺寸
        min_size = 10
        if abs(x1 - x0) > min_size and abs(y1 - y0) > min_size:
            # 确保坐标正确顺序 (左上角, 右下角)
            x1, x2 = sorted([x0, x1])
            y1, y2 = sorted([y0, y1])

            # 添加完成的矩形（绿色实线）
            rect = Rectangle((x1, y1), x2 - x1, y2 - y1,
                             edgecolor='green',
                             facecolor='none',
                             linewidth=2)
            self.ax.add_patch(rect)
            self.rectangles.append((x1, y1, x2, y2))

            # 添加编号
            self.ax.text(x1, y1 - 5, f'R{len(self.rectangles)}',
                         color='green', fontsize=12, weight='bold')

        # 重置当前矩形
        self.current_rect.remove()
        self.current_rect = None
        self.start_point = None
        self.fig.canvas.draw()

    def on_key(self, event):
        """键盘事件处理"""
        if event.key == 'r':  # 识别文本
            self.recognize_text()
        elif event.key == 'c':  # 清除所有框选
            self.clear_rectangles()
        elif event.key == 's':  # 保存裁剪区域
            self.save_cropped_regions()
        elif event.key == 'q':  # 退出
            plt.close(self.fig)
        elif event.key == 'h':  # 显示帮助
            self.show_help()

    def show_help(self):
        """显示帮助信息"""
        plt.figtext(0.5, 0.01, self.help_text,
                    ha="center", fontsize=10,
                    bbox={"facecolor": "orange", "alpha": 0.8, "pad": 5})
        self.fig.canvas.draw()

    def clear_rectangles(self):
        """清除所有矩形"""
        # 删除所有矩形
        for patch in self.ax.patches:
            patch.remove()

        # 删除所有文本
        for text in self.ax.texts:
            text.remove()

        # 清除矩形列表
        self.rectangles = []
        self.fig.canvas.draw()

    def recognize_text(self):
        """识别所有框选区域的文本"""
        if not self.rectangles:
            print("没有框选的区域！")
            return

        print("\n=== 识别结果 ===")
        for i, rect in enumerate(self.rectangles):
            x1, y1, x2, y2 = rect

            # 转换为整数坐标
            x1, y1, x2, y2 = int(x1), int(y1), int(x2), int(y2)

            # 裁剪图像
            img_height, img_width = self.image.shape[:2]
            # 确保坐标在图像范围内
            x1 = max(0, min(x1, img_width - 1))
            y1 = max(0, min(y1, img_height - 1))
            x2 = max(0, min(x2, img_width - 1))
            y2 = max(0, min(y2, img_height - 1))

            cropped = self.image[y1:y2, x1:x2]

            # 识别文本
            if cropped.size > 0:  # 确保裁剪区域有效
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
                plt.figure(figsize=(6, 3))
                plt.imshow(cropped)
                plt.title(f"区域 {i + 1} 裁剪")
                plt.axis('off')
                plt.tight_layout()
                plt.show(block=False)
            else:
                print(f"区域 {i + 1} 裁剪无效")

        print("================")

    def save_cropped_regions(self):
        """保存所有裁剪区域到文件"""
        if not self.rectangles:
            print("没有框选的区域！")
            return

        # 创建保存目录
        save_dir = "cropped_regions"
        os.makedirs(save_dir, exist_ok=True)

        img_height, img_width = self.image.shape[:2]

        for i, rect in enumerate(self.rectangles):
            x1, y1, x2, y2 = rect
            # 转换为整数坐标
            x1, y1, x2, y2 = int(x1), int(y1), int(x2), int(y2)

            # 确保坐标在图像范围内
            x1 = max(0, min(x1, img_width - 1))
            y1 = max(0, min(y1, img_height - 1))
            x2 = max(0, min(x2, img_width - 1))
            y2 = max(0, min(y2, img_height - 1))

            cropped = self.image[y1:y2, x1:x2]

            if cropped.size > 0:
                # 保存图像
                filename = os.path.join(save_dir, f"region_{i + 1}.png")
                Image.fromarray(cropped).save(filename)
                print(f"已保存区域 {i + 1} 到 {filename}")
            else:
                print(f"区域 {i + 1} 裁剪无效，跳过保存")

        print(f"所有区域已保存到 {save_dir} 目录")

    def run(self):
        """运行程序"""
        plt.show()


if __name__ == "__main__":
    # 使用说明
    print("=" * 50)
    print("交互式区域选择OCR工具 (Matplotlib版)")
    print("=" * 50)
    print("注意: 使用Matplotlib替代OpenCV的窗口功能")
    print("=" * 50)

    # 设置图像路径
    image_path = "/Video/chinese.jpg"

    if not os.path.exists(image_path):
        print(f"文件不存在: {image_path}")
        print("使用默认示例图片...")
        # 创建默认图片
        from PIL import Image, ImageDraw, ImageFont

        img = Image.new('RGB', (800, 400), color='white')
        draw = ImageDraw.Draw(img)
        try:
            font = ImageFont.truetype("arial.ttf", 40)
        except:
            font = ImageFont.load_default()
        draw.text((100, 150), "OCR示例图片", fill="black", font=font)
        draw.text((100, 250), "请拖拽选择区域", fill="blue", font=font)
        image_path = "example.jpg"
        img.save(image_path)
        print(f"已创建示例图片: {image_path}")

    # 初始化并运行
    try:
        app = InteractiveOCR(image_path)
        app.run()
    except Exception as e:
        print(f"发生错误: {e}")