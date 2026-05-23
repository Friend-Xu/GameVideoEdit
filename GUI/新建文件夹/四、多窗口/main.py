import sys
from PySide6.QtWidgets import *
from PySide6.QtGui import *
from PySide6.QtCore import *
from ui import ui_main_win ,ui_sub_win

# 如果需要从子窗口去更改调用主窗口，别忘了 if __name__ == '__main__':
# 里面已经获取过了哦，而且他是全局变量

# 自定义信号
class MySignal(QObject):
    # object 可以接收任何类型的值 ，也可以设置为指定类型 如 int str 等
    Signal = Signal(object)

    # 发送消息
    def emitSignal(self, value):
        self.Signal.emit(value)

# 自定义槽
class MySlot(QObject):

    # 接收消息
    def receiveSignal(self, value):
        branch = {
            '打开子窗口' : self.sub_win_show(value)
        }
        branch.get(value['action'])

    # 子窗口
    def sub_win_show(self ,value):
        self.sub_win = sub_win()
        # 可编辑置顶
        self.sub_win.setWindowFlags(self.sub_win.windowFlags() | Qt.WindowStaysOnTopHint)
        # 不可编辑置顶
        # self.sub_win.setWindowFlags(self.sub_win.windowFlags() | Qt.WindowDoesNotAcceptFocus | Qt.WindowStaysOnTopHint)
        self.sub_win.show()
        self.sub_win.ui.label.setText(value['info'])

# 父级窗口
class super_window(QWidget):
    # 如果一个程序有很多的窗口，不妨给他们创建一个父级

    def init_config(self):
        self.setWindowFlag(Qt.FramelessWindowHint)		#将界面设置为无框
        self.setAttribute(Qt.WA_TranslucentBackground)	#将界面属性设置为半透明
        self.shadow = QGraphicsDropShadowEffect()		#设定一个阴影,半径为 4,颜色为 2, 10, 25,偏移为 0,0
        self.shadow.setBlurRadius(4)
        self.shadow.setColor(QColor(2, 10, 25))
        self.shadow.setOffset(0, 0)
        self.ui.frame.setGraphicsEffect(self.shadow)	#为frame设定阴影效果


    #   --------------------------------------------------移动功能-------------------------------------------------

    def mousePressEvent(self, event):		#鼠标左键按下时获取鼠标坐标,按下右键取消
        if event.button() == Qt.LeftButton:
            self._move_drag = True
            self.m_Position = event.globalPosition() - self.pos()
            event.accept()

    def mouseMoveEvent(self, event):	#鼠标在按下左键的情况下移动时,根据坐标移动界面
        # 移动事件
        if Qt.LeftButton and self._move_drag:
            m_Point = event.globalPosition() - self.m_Position
            self.move(m_Point.x() ,m_Point.y())
            event.accept()

    def mouseReleaseEvent(self, event):	#鼠标按键释放时,取消移动
        self._move_drag = False

# 子窗口
class sub_win(super_window):

    def __init__(self,parent = None):

        # 从文件中加载UI定义
        super(sub_win, self).__init__(parent)
        self.ui = ui_sub_win.Ui_Form()
        self.ui.setupUi(self)
        self.init_config()

        # 按钮绑定关闭窗口事件
        self.ui.pushButton_2.clicked.connect(self.close)

        # 为 lanel 控件设置自动换行
        self.ui.label.setWordWrap(True)

# 主窗口
class main_win(super_window):

    def __init__(self,parent = None):

        # 从文件中加载UI定义
        super(main_win, self).__init__(parent)
        self.ui = ui_main_win.Ui_Form()
        self.ui.setupUi(self)
        self.init_config()

        # 按钮为绑定关闭窗口信号
        self.ui.pushButton_2.clicked.connect(self.close)

        # 按钮连接槽
        self.ui.pushButton_3.clicked.connect(self.sub_win_show)

        # 开启鼠标跟踪后，鼠标离开窗口或进入窗口会触发 mouseMoveEvent 事件
        self.setMouseTracking(True)
        # 初始化各扳机的状态
        self.initDrag()
        # 主窗口绑定事件过滤器
        self.ui.frame.installEventFilter(self)  # 初始化事件过滤器

        # 托盘菜单初始化
        self.tray_icon()


#   -------------------------------------------------托盘菜单功能-----------------------------------------------

    # 托盘菜单初始化
    def tray_icon(self):
        # 创建菜单的项目，并连接对应信号
        self.create_actions()
        # 把项目添加到菜单中( QMenu(self) )
        self.create_tray_icon()

    # 创建菜单的项目，并连接对应信号
    def create_actions(self):

        self._restore_action = QAction("显示主界面")
        self._restore_action.triggered.connect(self.showNormal)

        self._quit_action = QAction("退出")
        self._quit_action.triggered.connect(self.app_quit)

    # 把项目添加到菜单中( QMenu(self) )
    def create_tray_icon(self):
        self._tray_icon_menu = QMenu(self)
        self._tray_icon_menu.addAction(self._restore_action)
        # 添加分隔符
        self._tray_icon_menu.addSeparator()
        self._tray_icon_menu.addAction(self._quit_action)
        # 设置托盘图标，图片必须为正方形
        self._tray_icon = QSystemTrayIcon()
        self._tray_icon.setIcon(QIcon(r'img\logo.png'))
        self._tray_icon.setContextMenu(self._tray_icon_menu)

        # 在系统托盘显示此对象
        self._tray_icon.show()

        # 动作信号
        self._tray_icon.activated.connect(self.iconActivated)

    # 动作信号
    def iconActivated(self,reason):
        # 输出在鼠标在托盘图标上的动作
        print(reason)

        # 双击
        if reason == QSystemTrayIcon.DoubleClick:

            if self._tray_icon.isVisible():
                self.showNormal()

        # 右击
        if reason == QSystemTrayIcon.Context:

            if self._tray_icon.isVisible():
                # 菜单跟随鼠标
                self._tray_icon_menu.exec(QPoint(QCursor.pos().x() - 55 ,QCursor.pos().y() - 90))
            else:
                self.hide()

    # 退出
    def app_quit(self):
        # 先释放资源再退出，用于解决退出后图标不消失的问题
        self._restore_action = None
        self._quit_action = None
        self._tray_icon = None
        self._tray_icon_menu = None
        app.quit()


#   -------------------------------------------------事件过滤器-------------------------------------------------

    def eventFilter(self, obj, event):
        # 事件过滤器,用于解决鼠标进入其它控件后还原为标准鼠标样式
        if isinstance(event, QEnterEvent):
            self.setCursor(Qt.ArrowCursor)
        return super().eventFilter(obj, event)


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


#   --------------------------------------------------控件功能---------------------------------------------------

    def sub_win_show(self):
        info = {
            'action' : '打开子窗口',
            'info' : '这是从主窗口打开的子窗口' + '\n' +'文字也是从主窗口传递过来修改的'
        }
        MySignal.emitSignal(info)


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