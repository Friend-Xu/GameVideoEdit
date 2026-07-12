"""日志系统 —— 文件轮转 + 控制台 + Qt 桥接。

用法:
    from app.utils.logger import setup, get_logger
    setup(root_dir)  # app/main.py 最早期调用一次
    _log = get_logger(__name__)  # 各模块获取 logger

特性:
    - FileHandler: app.log, 5MB×3 轮转, DEBUG 级别
    - StderrHandler: INFO 级别, 开发调试
    - QtLogBridge: 可选, QApplication 之后 attach, 桥接 logging → Qt Signal
    - 未处理异常自动捕获到 CRITICAL
"""

import logging
import logging.handlers
import sys
import traceback
from pathlib import Path

LEVEL_MAP = {
    0: logging.INFO,
    1: logging.WARNING,
    2: logging.ERROR,
}

_qt_bridge = None


def setup(root_dir: str | Path, *, file_level: int = logging.DEBUG,
          console_level: int = logging.INFO) -> None:
    """配置根 logger 的 handlers。幂等调用。"""
    root = logging.getLogger()
    if root.handlers:
        return

    root.setLevel(logging.DEBUG)
    fmt = logging.Formatter(
        "[%(asctime)s.%(msecs)03d] [%(levelname)-5s] [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    log_dir = Path(root_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    fh = logging.handlers.RotatingFileHandler(
        log_dir / "app.log", maxBytes=5 * 1024 * 1024, backupCount=3,
        encoding="utf-8",
    )
    fh.setLevel(file_level)
    fh.setFormatter(fmt)
    root.addHandler(fh)

    ch = logging.StreamHandler(sys.stderr)
    ch.setLevel(console_level)
    ch.setFormatter(fmt)
    root.addHandler(ch)

    _install_excepthook()
    root.info("日志系统初始化: %s", log_dir / "app.log")


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def attach_qt_bridge(qt_signal) -> None:
    """将 Qt Signal 桥接到 logging 系统。

    qt_signal: 带 str, int 两个参数的 Signal (同旧 log Signal 签名)
    调用后所有 INFO+ 级别日志会同时发射到 GUI。
    """
    global _qt_bridge
    root = logging.getLogger()
    if _qt_bridge:
        root.removeHandler(_qt_bridge)
    _qt_bridge = _QtLogHandler(qt_signal)
    _qt_bridge.setLevel(logging.INFO)
    _qt_bridge.setFormatter(logging.Formatter("%(message)s"))
    root.addHandler(_qt_bridge)


class _QtLogHandler(logging.Handler):
    """将 logging 记录转发到 Qt Signal。"""

    def __init__(self, qt_signal):
        super().__init__()
        self._signal = qt_signal

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            lv = LEVEL_MAP.get(record.levelno, 0)
            self._signal.emit(msg, lv)
        except Exception:
            self.handleError(record)


def _install_excepthook() -> None:
    """捕获未处理异常写入 CRITICAL。"""
    _orig = sys.excepthook

    def _hook(etype, value, tb):
        msg = "".join(traceback.format_exception(etype, value, tb))
        logging.getLogger("unhandled").critical("未处理异常:\n%s", msg)
        _orig(etype, value, tb)

    sys.excepthook = _hook
