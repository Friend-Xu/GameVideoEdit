# -*- coding: utf-8 -*-

################################################################################
## Form generated from reading UI file 'main_win.ui'
##
## Created by: Qt User Interface Compiler version 6.4.2
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
    QSizePolicy, QSpacerItem, QVBoxLayout, QWidget)

class Ui_Form(object):
    def setupUi(self, Form):
        if not Form.objectName():
            Form.setObjectName(u"Form")
        Form.resize(761, 513)
        Form.setStyleSheet(u"\n"
"\n"
"*\n"
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
"}\n"
"\n"
"/*\u83dc\u5355\u6837\u5f0f*/ \n"
"QMenu\n"
"{\n"
"	background-color:#2F3648;\n"
"	color:white;\n"
"	border : 1px solid #000000\n"
"}\n"
"/*\u83dc\u5355\u9009\u9879\u6837\u5f0f*/ \n"
"QMenu::item\n"
"{\n"
"	height:20px;\n"
"	margin:5px 10px 5px 10px\n"
"}\n"
"/*\u83dc\u5355\u9009\u9879\u88ab\u9009\u4e2d\u6837\u5f0f*/ \n"
"QMenu::item:selected\n"
"{\n"
"	color:#3376c1;\n"
"	padding-bottom:-1px;\n"
"	padding-left:1px\n"
"}\n"
"")
        self.horizontalLayout = QHBoxLayout(Form)
        self.horizontalLayout.setObjectName(u"horizontalLayout")
        self.frame = QFrame(Form)
        self.frame.setObjectName(u"frame")
        self.frame.setFrameShape(QFrame.StyledPanel)
        self.frame.setFrameShadow(QFrame.Raised)
        self.verticalLayout = QVBoxLayout(self.frame)
        self.verticalLayout.setObjectName(u"verticalLayout")
        self.frame_2 = QFrame(self.frame)
        self.frame_2.setObjectName(u"frame_2")
        sizePolicy = QSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        sizePolicy.setHorizontalStretch(0)
        sizePolicy.setVerticalStretch(0)
        sizePolicy.setHeightForWidth(self.frame_2.sizePolicy().hasHeightForWidth())
        self.frame_2.setSizePolicy(sizePolicy)
        self.frame_2.setFrameShape(QFrame.StyledPanel)
        self.frame_2.setFrameShadow(QFrame.Raised)
        self.horizontalLayout_3 = QHBoxLayout(self.frame_2)
        self.horizontalLayout_3.setObjectName(u"horizontalLayout_3")
        self.horizontalLayout_3.setContentsMargins(0, 0, 0, 0)
        self.horizontalSpacer = QSpacerItem(40, 20, QSizePolicy.Expanding, QSizePolicy.Minimum)

        self.horizontalLayout_3.addItem(self.horizontalSpacer)

        self.pushButton_2 = QPushButton(self.frame_2)
        self.pushButton_2.setObjectName(u"pushButton_2")
        sizePolicy1 = QSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        sizePolicy1.setHorizontalStretch(0)
        sizePolicy1.setVerticalStretch(0)
        sizePolicy1.setHeightForWidth(self.pushButton_2.sizePolicy().hasHeightForWidth())
        self.pushButton_2.setSizePolicy(sizePolicy1)

        self.horizontalLayout_3.addWidget(self.pushButton_2)


        self.verticalLayout.addWidget(self.frame_2)

        self.frame_3 = QFrame(self.frame)
        self.frame_3.setObjectName(u"frame_3")
        self.frame_3.setFrameShape(QFrame.StyledPanel)
        self.frame_3.setFrameShadow(QFrame.Raised)
        self.horizontalLayout_2 = QHBoxLayout(self.frame_3)
        self.horizontalLayout_2.setObjectName(u"horizontalLayout_2")
        self.pushButton = QPushButton(self.frame_3)
        self.pushButton.setObjectName(u"pushButton")

        self.horizontalLayout_2.addWidget(self.pushButton)


        self.verticalLayout.addWidget(self.frame_3)


        self.horizontalLayout.addWidget(self.frame)


        self.retranslateUi(Form)

        QMetaObject.connectSlotsByName(Form)
    # setupUi

    def retranslateUi(self, Form):
        Form.setWindowTitle(QCoreApplication.translate("Form", u"Form", None))
        self.pushButton_2.setText(QCoreApplication.translate("Form", u"\u00d7", None))
        self.pushButton.setText(QCoreApplication.translate("Form", u"\u4e09\u3001\u8bbe\u7f6e\u7cfb\u7edf\u6258\u76d8", None))
    # retranslateUi

