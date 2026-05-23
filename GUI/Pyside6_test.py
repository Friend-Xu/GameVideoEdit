import sys
from PySide6.QtWidgets import QApplication, QMainWindow, QPushButton,QWidget
from GUI.ui_main import Ui_Form
class MyWindow(QMainWindow,Ui_Form):
    def __init__(self):
        super().__init__()
        # self.ui = Ui_MainWindow()
        self.setupUi(self)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MyWindow()
    window.show()
    sys.exit(app.exec())
