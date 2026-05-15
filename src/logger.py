import logging
import sys
from pathlib import Path


LOG_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def get_logger(name: str, level: int = logging.INFO) -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    logger.setLevel(level)
    formatter = logging.Formatter(LOG_FORMAT, DATE_FORMAT)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    return logger


CUMULATIVE_LOG = Path("output/run.log")


def add_file_handler(
    logger: logging.Logger,
    log_dir: str = "output",
    level: int = logging.DEBUG,
) -> logging.Logger:
    """添加文件日志处理器（累计追加到 output/run.log）"""
    path = Path(log_dir)
    path.mkdir(parents=True, exist_ok=True)
    filename = path / "run.log"
    file_handler = logging.FileHandler(str(filename), encoding="utf-8", mode="a")
    file_handler.setLevel(level)
    file_handler.setFormatter(logging.Formatter(LOG_FORMAT, DATE_FORMAT))
    logger.addHandler(file_handler)
    return logger


class ProgressTracker:
    """进度追踪器 — 记录每个步骤的开始/结束/耗时"""

    def __init__(self, logger: logging.Logger):
        self.logger = logger
        self.steps: list[dict] = []

    def step_start(self, name: str) -> None:
        self.logger.info("START  | %s", name)

    def step_end(self, name: str, detail: str = "") -> None:
        self.logger.info("END    | %s%s", name, f" — {detail}" if detail else "")

    def step_error(self, name: str, error: str) -> None:
        self.logger.error("ERROR  | %s — %s", name, error)