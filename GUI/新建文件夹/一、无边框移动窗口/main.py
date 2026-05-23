import sys
from PySide6.QtWidgets import *
from PySide6.QtGui import *
from PySide6.QtCore import *
from ui import ui_main_win

# 主窗口
class main_win(QWidget):

    def __init__(self,parent = None):

        # 从文件中加载UI定义
        super(main_win, self).__init__(parent)
        self.ui = ui_main_win.Ui_Form()
        self.ui.setupUi(self)

        self.setWindowFlag(Qt.FramelessWindowHint)		#将界面设置为无框
        self.setAttribute(Qt.WA_TranslucentBackground)	#将界面属性设置为半透明
        self.shadow = QGraphicsDropShadowEffect()		#设定一个阴影,半径为 4,颜色为 2, 10, 25,偏移为 0,0
        self.shadow.setBlurRadius(4)
        self.shadow.setColor(QColor(2, 10, 25))
        self.shadow.setOffset(0, 0)
        self.ui.frame.setGraphicsEffect(self.shadow)	#为frame设定阴影效果


#   --------------------------------------------------移动功能-------------------------------------------------

    def mousePressEvent(self, event):		#鼠标左键按下时获取鼠标坐标
        if event.button() == Qt.LeftButton:
            self._move_drag = True
            self.cursor_win_pos = event.globalPosition() - self.pos()
            event.accept()

    def mouseMoveEvent(self, event):	#鼠标在按下左键的情况下移动时,根据坐标移动界面
        # 移动事件
        if Qt.LeftButton and self._move_drag:
            m_Point = event.globalPosition() - self.cursor_win_pos
            self.move(m_Point.x() ,m_Point.y())
            event.accept()

    def mouseReleaseEvent(self, event):	#鼠标按键释放时,取消移动
        self._move_drag = False


# 每一个 PySide6 应用都必须创建一个应用对象
app = QApplication([])
main_win = main_win()
main_win.show()
sys.exit(app.exec())