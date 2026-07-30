"""
Logging 配置模块 - 文件滚动日志 + 控制台输出 + 异常捕获
"""
import logging
import os
import sys
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path

_NOISY_LOGGERS = ("httpx", "httpcore", "openai", "urllib3", "chromadb", "sqlalchemy", "uvicorn")
_CONFIGURED = False
_CONFIG_LOCK = threading.RLock()


def _make_formatter() -> logging.Formatter:
    return logging.Formatter(
        fmt="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def _build_file_handler(log_path: Path, max_bytes: int, backup_count: int) -> logging.Handler:
    return RotatingFileHandler(
        filename=str(log_path),
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def configure_logging(app_name: str = "painsmart", *, force: bool = False) -> logging.Logger:
    global _CONFIGURED

    with _CONFIG_LOCK:
        if _CONFIGURED and not force:
            return get_logger(app_name)

        log_level_str = os.getenv("PAINSMART_LOG_LEVEL", "INFO").upper()
        log_level = getattr(logging, log_level_str, logging.INFO)

        log_dir = Path(os.getenv("PAINSMART_LOG_DIR", str(Path(__file__).parent / "logs")))
        log_dir.mkdir(parents=True, exist_ok=True)

        log_file = os.getenv("PAINSMART_LOG_FILE", "painsmart.log")
        log_path = log_dir / log_file
        max_bytes = int(float(os.getenv("PAINSMART_LOG_MAX_MB", "10")) * 1024 * 1024)
        backup_count = int(os.getenv("PAINSMART_LOG_BACKUP_COUNT", "5"))
        formatter = _make_formatter()

        root_logger = logging.getLogger()
        root_logger.setLevel(log_level)

        # 清除已有 handler（避免重复）
        for handler in list(root_logger.handlers):
            handler.close()
            root_logger.removeHandler(handler)

        # 控制台输出
        console_handler = logging.StreamHandler(sys.stderr)
        console_handler.setLevel(log_level)
        console_handler.setFormatter(formatter)
        root_logger.addHandler(console_handler)

        # 文件滚动输出
        file_handler = _build_file_handler(log_path, max_bytes=max_bytes, backup_count=backup_count)
        file_handler.setLevel(log_level)
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)

        # 抑制三方库噪音
        for name in _NOISY_LOGGERS:
            logging.getLogger(name).setLevel(logging.WARNING)

        # 捕获未处理异常
        _install_exception_hooks(app_name)

        _CONFIGURED = True
        logger = get_logger(app_name)
        logger.info("日志就绪: %s (level=%s)", log_path, log_level_str)
        return logger


def _install_exception_hooks(app_name: str) -> None:
    logger = logging.getLogger(app_name)
    previous_hook = sys.excepthook

    def handle_exception(exc_type, exc_value, exc_traceback):
        if issubclass(exc_type, KeyboardInterrupt):
            previous_hook(exc_type, exc_value, exc_traceback)
            return
        logger.critical("未捕获异常", exc_info=(exc_type, exc_value, exc_traceback))

    sys.excepthook = handle_exception
