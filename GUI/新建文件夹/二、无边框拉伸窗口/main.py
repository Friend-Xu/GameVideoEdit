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

        # 绑定退出按钮信号
        self.ui.pushButton_2.clicked.connect(app.quit)

        # 开启鼠标跟踪后，鼠标离开窗口或进入窗口会触发 mouseMoveEvent 事件
        self.setMouseTracking(True)
        # 初始化各扳机的状态
        self.initDrag()
        # 主窗口绑定事件过滤器
        self.ui.frame.installEventFilter(self)  # 初始化事件过滤器


#   -------------------------------------------------事件过滤器-------------------------------------------------

    def eventFilter(self, obj, event):
        # 事件过滤器,用于解决鼠标进入其它控件后还原为标准鼠标样式
        if isinstance(event, QEnterEvent):
            self.setCursor(Qt.ArrowCursor)
        return super().eventFilter(obj, event)


#   -----------------------------------------------移动与拉伸功能------------------------------------------------

#   -----------------------------------------------移动与拉伸功能------------------------------------------------

    # 初始化各扳机的状态
    def initDrag(self):
        self._move_drag = False
        self._corner_drag = False
        self._bottom_drag = False
        self._right_drag = False

    # 鼠标按下所执行的功能
    def mousePressEvent(self, event):
        # globalPosition为鼠标位置 , pos,position 为窗口的位置 , cursor_win_pos 为鼠标在窗口中的位置

        if event.button() == Qt.LeftButton:
            self.cursor_win_pos = event.globalPosition() - self.pos()
            # 移动事件
            if self.cursor_win_pos.x() < self.ui.frame.size().width() and self.cursor_win_pos.y() < self.ui.frame.size().height():
                self._move_drag = True
                event.accept()

            # 右下角边界拉伸事件
            elif self.cursor_win_pos.x() > self.ui.frame.size().width() and self.cursor_win_pos.y() > self.ui.frame.size().height():
                self._corner_drag = True
                event.accept()

            # 下边界拉伸事件
            elif self.cursor_win_pos.x() < self.ui.frame.size().width() and self.cursor_win_pos.y() > self.ui.frame.size().height():
                self._bottom_drag = True
                event.accept()

            # 右边界拉伸事件
            elif self.cursor_win_pos.x() > self.ui.frame.size().width() and self.cursor_win_pos.y() < self.ui.frame.size().height():
                self._right_drag = True
                event.accept()

    # 鼠标移动所执行的功能
    def mouseMoveEvent(self, event):
        # 移动事件
        if Qt.LeftButton and self._move_drag:
            m_Point = event.globalPosition() - self.cursor_win_pos
            self.move(m_Point.x() ,m_Point.y())
            event.accept()

        # 右下角边界拉伸事件
        elif Qt.LeftButton and self._corner_drag:
            self.resize(event.position().x()+10 , event.position().y()+10)
            event.accept()

        # 下边界拉伸事件
        elif Qt.LeftButton and self._bottom_drag:
            self.resize(self.width() , event.position().y()+10)
            event.accept()

        # 右边界拉伸事件
        elif Qt.LeftButton and self._right_drag:
            self.resize(event.position().x()+10 , self.height())
            event.accept()

        # 获取鼠标在窗口中的位置来改变鼠标的图标
        # 右下角边界光标事件
        self.cursor_win_pos = event.globalPosition() - self.pos()
        if self.cursor_win_pos.x() > self.ui.frame.size().width() and self.cursor_win_pos.y() > self.ui.frame.size().height():
            self.setCursor(Qt.SizeFDiagCursor)
        # 下边界光标事件
        elif self.cursor_win_pos.x() < self.ui.frame.size().width() and self.cursor_win_pos.y() > self.ui.frame.size().height():
            self.setCursor(Qt.SizeVerCursor)
        # 右边界光标事件
        elif self.cursor_win_pos.x() > self.ui.frame.size().width() and self.cursor_win_pos.y() < self.ui.frame.size().height():
            self.setCursor(Qt.SizeHorCursor)
        # 正常光标事件
        else:
            self.setCursor(Qt.ArrowCursor)

    # 鼠标弹起后，恢复各扳机的状态
    def mouseReleaseEvent(self, event):
        self._move_drag = False
        self._corner_drag = False
        self._bottom_drag = False
        self._right_drag = False


if __name__ == '__main__':
    # 每一个 PySide6 应用都必须创建一个应用对象
    app = QApplication([])
    main_win = main_win()
    main_win.show()
    sys.exit(app.exec())