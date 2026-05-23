# -*- coding: utf-8 -*-

################################################################################
## Form generated from reading UI file 'main_winYmeVmV.ui'
##
## Created by: Qt User Interface Compiler version 6.4.1
##
## WARNING! All changes made in this file will be lost when recompiling UI file!
################################################################################

from PySide6.QtCore import (QCoreApplication, QDate, QDateTime, QLocale,
    QMetaObject, QObject, QPoint, QRect,
    QSize, QTime, QUrl, Qt)
from PySide6.QtGui import (QBrush, QColor, QConicalGradient, QCursor,
    QFont, QFontDatabase, QGradient, QIcon,
    QImage, QKeySequence, QLinearGradient, QPainter,
    QPalette, QPixmap, QRadialGradient, QTransform)
from PySide6.QtWidgets import (QApplication, QFrame, QHBoxLayout, QPushButton,
    QSizePolicy, QWidget)

class Ui_Form(object):
    def setupUi(self, Form):
        if not Form.objectName():
            Form.setObjectName(u"Form")
        Form.resize(761, 513)
        Form.setStyleSheet(u"*\n"
"{\n"
"	font: 10pt \"\u6977\u4f53\";\n"
"	font-size:14pt;\n"
"	color:white;\n"
"}\n"
"\n"
"QFrame#frame\n"
"{\n"
"	background-color:#2F3648;\n"
"	border-radius: 5px;\n"
"}\n"
"\n"
"/*\u539f\u59cb*/ \n"
"QPushButton\n"
"{\n"
"	background-color:#2F3648;\n"
"	border-radius: 0px;\n"
"}\n"
"\n"
"/*\u60ac\u505c*/ \n"
"QPushButton:hover\n"
"{\n"
"	color:#3376c1;\n"
"}\n"
"\n"
"/*\u6309\u4e0b*/ \n"
"QPushButton:pressed\n"
"{\n"
"	padding-bottom:-2px;\n"
"	padding-left:1px\n"
"}")
        self.horizontalLayout = QHBoxLayout(Form)
        self.horizontalLayout.setObjectName(u"horizontalLayout")
        self.frame = QFrame(Form)
        self.frame.setObjectName(u"frame")
        self.frame.setFrameShape(QFrame.StyledPanel)
        self.frame.setFrameShadow(QFrame.Raised)
        self.pushButton = QPushButton(self.frame)
        self.pushButton.setObjectName(u"pushButton")
        self.pushButton.setGeometry(QRect(274, 220, 200, 40))

        self.horizontalLayout.addWidget(self.frame)


        self.retranslateUi(Form)

        QMetaObject.connectSlotsByName(Form)
    # setupUi

    def retranslateUi(self, Form):
        Form.setWindowTitle(QCoreApplication.translate("Form", u"Form", None))
        self.pushButton.setText(QCoreApplication.translate("Form", u"\u4e00\u3001\u65e0\u8fb9\u6846\u79fb\u52a8\u7a97\u53e3", None))
    # retranslateUi

