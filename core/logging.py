"""
统一任务级日志工厂模块

本模块提供了一个"任务隔离"的日志系统。简单来说：
- 每个任务（task）都有自己独立的日志文件
- 日志文件存放在 data/logs/<task_id>.log
- 日志文件会自动滚动，单个文件最大 10MB，最多保留 2 个备份
- 避免多个任务混写同一个日志文件，方便问题排查

为什么需要这个模块？
------------------
在 PDF 处理管道中，用户可能同时处理多个 PDF 文件。如果把所有日志都写到一个文件里，
很难追踪某个 PDF 的处理过程。使用任务级日志，每个 PDF 处理任务都有独立的日志文件，
出问题时一目了然。

使用示例
--------
>>> from core.logging import get_task_logger, close_task_logger
>>>
>>> # 获取任务日志记录器
>>> logger = get_task_logger("pdf_20240101_abc123")
>>> logger.info("开始解析 PDF 文件")
>>> logger.debug("文件大小: 5.2MB")
>>> logger.error("解析失败: 文件损坏")
>>>
>>> # 任务完成后关闭日志记录器（释放文件句柄）
>>> close_task_logger("pdf_20240101_abc123")

日志输出格式
-----------
2024-01-01T10:30:45 [INFO] task.pdf_20240101_abc123 — 开始解析 PDF 文件
2024-01-01T10:30:45 [DEBUG] task.pdf_20240101_abc123 — 文件大小: 5.2MB
2024-01-01T10:30:46 [ERROR] task.pdf_20240101_abc123 — 解析失败: 文件损坏

文件结构
--------
data/
└── logs/
    ├── task_abc123.log      # 当前日志
    ├── task_abc123.log.1    # 第一个备份
    └── task_abc123.log.2    # 第二个备份（最老）
"""

from __future__ import annotations

import logging
import os
from logging import Logger
from logging.handlers import RotatingFileHandler

# 日志文件存放目录
# 所有任务的日志都放在 data/logs/ 下
_LOG_DIR = os.path.join("data", "logs")

# 日志格式化器
# 定义日志输出的格式：时间戳 + 日志级别 + 日志名称 + 日志内容
# 示例：2024-01-01T10:30:45 [INFO] task.abc123 — 消息内容
_FORMATTER = logging.Formatter(
    "%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)

# 内存中的日志记录器注册表
# 用于缓存已创建的日志记录器，避免重复创建和重复添加 handler
# key: task_id, value: Logger 实例
_loggers: dict[str, Logger] = {}


def get_task_logger(task_id: str) -> Logger:
    """
    获取指定任务的日志记录器。

    每个任务会获得一个独立的日志记录器，日志写入 data/logs/<task_id>.log 文件。
    如果该任务的日志记录器已经创建过，则直接返回缓存的实例。

    参数
    ----
    task_id : str
        任务的唯一标识符。通常使用时间戳 + 随机字符串，例如 "pdf_20240101_abc123"。
        这个 ID 会成为日志文件名的一部分。

    返回
    ----
    Logger
        配置好的日志记录器实例，可以直接调用 info()、debug()、error() 等方法。

    特性
    ----
    1. 日志滚动：单个日志文件最大 10MB，超过后自动滚动到 .log.1、.log.2
    2. 备份数量：最多保留 2 个备份文件（.log.1 和 .log.2）
    3. UTF-8 编码：支持中文日志内容
    4. 不向上传播：logger.propagate = False，避免日志重复输出到根日志器

    使用示例
    --------
    >>> logger = get_task_logger("my_task_001")
    >>> logger.info("任务开始")
    >>> logger.debug("处理中...")
    >>> logger.warning("发现异常数据")
    >>> logger.error("处理失败")

    注意事项
    --------
    - 日志目录 data/logs/ 会自动创建，无需手动创建
    - 任务结束后，建议调用 close_task_logger() 关闭日志记录器，释放文件句柄
    - 同一个 task_id 多次调用此函数，返回的是同一个 Logger 实例
    """
    # 如果日志记录器已存在，直接返回缓存的实例
    if task_id in _loggers:
        return _loggers[task_id]

    # 确保日志目录存在
    os.makedirs(_LOG_DIR, exist_ok=True)

    # 构建日志文件的完整路径
    log_path = os.path.join(_LOG_DIR, f"{task_id}.log")

    # 创建或获取日志记录器
    # 日志器名称格式：task.<task_id>，例如 task.pdf_20240101_abc123
    logger = logging.getLogger(f"task.{task_id}")

    # 设置日志级别为 DEBUG（最低级别，所有级别的日志都会被记录）
    logger.setLevel(logging.DEBUG)

    # 禁止日志向上传播到根日志器
    # 这样可以避免日志被重复输出（例如同时输出到控制台）
    logger.propagate = False

    # 只有当日志记录器还没有 handler 时才添加新的 handler
    # 这样可以避免重复添加 handler 导致日志重复输出
    if not logger.handlers:
        # 创建滚动文件处理器
        # - maxBytes: 单个日志文件最大 10MB（10 * 1024 * 1024 字节）
        # - backupCount: 最多保留 2 个备份文件
        # - encoding: 使用 UTF-8 编码，支持中文
        fh = RotatingFileHandler(
            log_path,
            maxBytes=10 * 1024 * 1024,  # 10MB
            backupCount=2,
            encoding="utf-8"
        )

        # 设置日志格式
        fh.setFormatter(_FORMATTER)

        # 将处理器添加到日志记录器
        logger.addHandler(fh)

    # 缓存日志记录器，下次直接返回
    _loggers[task_id] = logger
    return logger


def close_task_logger(task_id: str) -> None:
    """
    关闭并清理指定任务的日志记录器。

    当任务完成或不再需要记录日志时，调用此函数可以：
    1. 刷新（flush）日志缓冲区，确保所有日志都写入文件
    2. 关闭文件句柄，释放系统资源
    3. 从内存缓存中移除日志记录器

    参数
    ----
    task_id : str
        任务的唯一标识符，与 get_task_logger() 中使用的 task_id 相同。

    使用示例
    --------
    >>> logger = get_task_logger("my_task_001")
    >>> logger.info("任务开始")
    >>> # ... 任务执行 ...
    >>> logger.info("任务完成")
    >>> close_task_logger("my_task_001")  # 关闭日志记录器

    注意事项
    --------
    - 调用此函数后，如果再次调用 get_task_logger(task_id)，会创建新的日志记录器
    - 如果 task_id 对应的日志记录器不存在，此函数不会做任何事情（静默忽略）
    - 建议在任务完成或服务关闭时调用此函数，以释放资源
    """
    # 从缓存中移除日志记录器
    # pop() 方法在 key 不存在时返回 None，不会抛出异常
    logger = _loggers.pop(task_id, None)

    if logger:
        # 遍历日志记录器的所有处理器
        # 使用 [:] 创建副本，避免在遍历时修改列表
        for handler in logger.handlers[:]:
            # 刷新缓冲区，确保所有日志都写入文件
            handler.flush()

            # 关闭处理器，释放文件句柄等资源
            handler.close()

            # 从日志记录器中移除处理器
            logger.removeHandler(handler)
