import sys
from PySide6.QtWidgets import *
from PySide6.QtGui import *
from PySide6.QtCore import *
from ui import ui_main_win ,ui_sub_win


# 自定义信号
class MySignal(QObject):
    # object 可以接收任何类型的值 ，也可以设置为指定类型 如 int str 等
    Signal = Signal(object ,object)

    # 发送消息
    def emitSignal(self, value1 ,value2):
        # 参数1 为原文 ，参数2 为动作
        self.Signal.emit(value1 ,value2)

# 自定义槽
class MySlot(QObject):

    # 接收消息，参数1 为原文，参数2 为动作
    def receiveSignal(self, value1 ,value2):

        if value2 == '打开子窗口':
            self.sub_win_show(value1)

    # 子窗口
    def sub_win_show(self ,value1):
        self.sub_win = sub_win()
        # 设置窗口置顶
        self.sub_win.setWindowFlags(self.sub_win.windowFlags() | Qt.WindowDoesNotAcceptFocus | Qt.WindowStaysOnTopHint)
        self.sub_win.show()
        self.sub_win.ui.label.setText(value1)

# 子窗口
class sub_win(QWidget):

    def __init__(self,parent = None):

        # 从文件中加载UI定义
        super(sub_win, self).__init__(parent)
        self.ui = ui_sub_win.Ui_Form()
        self.ui.setupUi(self)

        # 按钮绑定关闭窗口事件
        self.ui.pushButton_2.clicked.connect(self.close)
        
        # 为 lanel 控件设置自动换行
        self.ui.label.setWordWrap(True)


# 主窗口
class main_win(QWidget):

    def __init__(self,parent = None):

        # 从文件中加载UI定义
        super(main_win, self).__init__(parent)
        self.ui = ui_main_win.Ui_Form()
        self.ui.setupUi(self)

        # 按钮为绑定关闭窗口信号
        self.ui.pushButton_2.clicked.connect(self.close)

        # 按钮连接槽
        self.ui.pushButton_3.clicked.connect(self.sub_win_show)


#   --------------------------------------------------控件功能---------------------------------------------------

    def sub_win_show(self):
        txt = '这是从主窗口打开的子窗口' + '\n' +'文字也是从主窗口传递过来修改的'
        MySignal.emitSignal(txt ,'打开子窗口')


if __name__ == '__main__':

    # 连接自定义信号和槽
    MySignal = MySignal()
    MySlot = MySlot()
    MySignal.Signal.connect(MySlot.receiveSignal)

    # 每一个 PySide6 应用都必须创建一个应用对象
    app = QApplication([])

    # 设置窗口图标：按下 Alt + Tab 能够看到的图标，图片必须为正方形
    app.setWindowIcon(QIcon(r'img\logo.png'))

    # 检测当前系统是否支持托盘功能
    if not QSystemTrayIcon.isSystemTrayAvailable():
        QMessageBox.critical(None, "系统托盘", "本系统检测不出系统托盘")
        sys.exit(1)

    # 使得程序能在后台运行，关闭最后一个窗口不退出程序
    QApplication.setQuitOnLastWindowClosed(False)

    main_win = main_win()
    main_win.show()
    sys.exit(app.exec())