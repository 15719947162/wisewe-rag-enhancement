"""
任务存储模块

本模块负责管理文档入库任务的数据存储，主要包括：
1. 任务创建与保存
2. 任务查询（单个/全部）
3. 任务删除
4. 任务状态更新

存储方式：
- 优先使用 Redis（如果可用），支持分布式部署
- 如果 Redis 不可用，回退到内存存储（单机部署）

数据结构：
- 任务数据以 JSON 格式存储
- 包含任务 ID、状态、进度、错误信息等
- 设置 TTL（7 天），过期自动清理

关键概念：
- task: 文档入库任务，包含多个阶段的状态信息
- stage: 任务阶段，如 upload、parse、clean、chunk 等
- watermark: 水位线，用于增量同步的上次同步时间
"""

from __future__ import annotations

import json
import os
from typing import Any

# ========== 常量定义 ==========

# Redis 中任务数据的 TTL：7 天
_TASK_TTL = 7 * 24 * 3600
# Redis key 前缀，用于区分不同类型的任务
_KEY_PREFIX = "wisewe:task:"
# Redis 中存储所有任务 ID 的集合 key
_ALL_KEY = "wisewe:task_ids"

# ========== 全局状态 ==========

# Redis 客户端（延迟初始化）
_redis_client: Any = None
# Redis 是否可用（None = 未检查，True = 可用，False = 不可用）
_redis_available: bool | None = None
# 内存存储（Redis 不可用时的后备）
_mem_tasks: dict[str, dict] = {}


def _clone_task(task: dict) -> dict:
    """
    深拷贝任务数据

    防止外部修改影响内部数据。

    参数：
        task: 任务字典

    返回：
        dict: 深拷贝后的任务字典
    """
    return json.loads(json.dumps(task, ensure_ascii=False, default=str))


def _get_redis():
    """
    获取 Redis 客户端（延迟初始化）

    首次调用时尝试连接 Redis，如果失败则标记为不可用。

    返回：
        Redis client | None: Redis 客户端，不可用时返回 None

    说明：
        - 使用 REDIS_URL 环境变量配置连接地址
        - 默认连接 redis://localhost:6379/0
        - 连接超时 2 秒
    """
    global _redis_client, _redis_available
    if _redis_available is False:
        return None
    if _redis_client is not None:
        return _redis_client

    url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    try:
        import redis as _redis

        client = _redis.from_url(url, decode_responses=True, socket_connect_timeout=2)
        client.ping()
        _redis_client = client
        _redis_available = True
        return _redis_client
    except Exception:
        _redis_available = False
        _redis_client = None
        return None


def _serialize(task: dict) -> str:
    """
    序列化任务数据为 JSON 字符串

    过滤掉不可序列化的字段（如 file_bytes）。

    参数：
        task: 任务字典

    返回：
        str: JSON 字符串
    """
    # file_bytes is not JSON-serializable; strip before storing
    safe = {k: v for k, v in task.items() if k != "file_bytes"}
    return json.dumps(safe, ensure_ascii=False, default=str)


def _deserialize(raw: str) -> dict:
    """
    反序列化 JSON 字符串为任务字典

    恢复默认字段值。

    参数：
        raw: JSON 字符串

    返回：
        dict: 任务字典
    """
    task = json.loads(raw)
    task.setdefault("file_bytes", None)
    return task


def _task_time_sort_key(task: dict) -> tuple[str, str]:
    """
    任务排序键

    用于按时间倒序排列任务。

    参数：
        task: 任务字典

    返回：
        tuple: (时间字符串, 任务 ID)
    """
    return (str(task.get("created_at") or task.get("updated_at") or ""), str(task.get("id", "")))


def _sort_tasks_newest_first(tasks: list[dict]) -> list[dict]:
    """
    按时间倒序排列任务列表

    参数：
        tasks: 任务列表

    返回：
        list[dict]: 排序后的任务列表
    """
    return sorted(tasks, key=_task_time_sort_key, reverse=True)


def save_task(task: dict) -> None:
    """
    保存任务数据

    将任务数据保存到内存和 Redis（如果可用）。

    参数：
        task: 任务字典，必须包含 "id" 字段

    说明：
        - 内存存储是实时的
        - Redis 存储有 TTL（7 天）
        - 同时更新任务 ID 集合
    """
    task_id = task["id"]
    _mem_tasks[task_id] = _clone_task(task)

    r = _get_redis()
    if r is None:
        return

    r.setex(f"{_KEY_PREFIX}{task_id}", _TASK_TTL, _serialize(task))
    r.sadd(_ALL_KEY, task_id)
    r.expire(_ALL_KEY, _TASK_TTL)


def load_task(task_id: str) -> dict | None:
    """
    加载单个任务

    从 Redis 或内存中加载任务数据。

    参数：
        task_id: 任务 ID

    返回：
        dict | None: 任务字典，不存在则返回 None

    说明：
        - 优先从 Redis 读取
        - 如果 Redis 没有，清理内存中的旧数据
    """
    r = _get_redis()
    if r is None:
        task = _mem_tasks.get(task_id)
        return _clone_task(task) if task else None

    raw = r.get(f"{_KEY_PREFIX}{task_id}")
    if raw:
        return _deserialize(raw)
    _mem_tasks.pop(task_id, None)
    return None


def load_all_tasks() -> list[dict]:
    """
    加载所有任务

    从 Redis 或内存中加载所有任务数据。

    返回：
        list[dict]: 任务列表，按时间倒序排列

    说明：
        - 优先从 Redis 读取
        - 如果 Redis 不可用，从内存读取
        - 清理内存中 Redis 已删除的任务
    """
    r = _get_redis()
    if r is None:
        return _sort_tasks_newest_first([_clone_task(task) for task in _mem_tasks.values()])

    ids = r.smembers(_ALL_KEY)
    tasks_by_id: dict[str, dict] = {}
    for tid in ids:
        raw = r.get(f"{_KEY_PREFIX}{tid}")
        if raw:
            tasks_by_id[tid] = _deserialize(raw)

    for task_id in list(_mem_tasks):
        if task_id not in tasks_by_id:
            _mem_tasks.pop(task_id, None)

    return _sort_tasks_newest_first(list(tasks_by_id.values()))


def delete_task(task_id: str) -> bool:
    """
    删除任务

    从内存和 Redis 中删除任务数据。

    参数：
        task_id: 任务 ID

    返回：
        bool: 是否删除成功

    说明：
        - 同时删除内存和 Redis 中的数据
        - 从任务 ID 集合中移除
    """
    deleted = _mem_tasks.pop(task_id, None) is not None

    r = _get_redis()
    if r is None:
        return deleted

    removed = r.delete(f"{_KEY_PREFIX}{task_id}")
    r.srem(_ALL_KEY, task_id)
    return deleted or bool(removed)


def update_task_field(task_id: str, **fields) -> None:
    """
    部分更新任务字段

    加载任务，更新指定字段，然后保存。

    参数：
        task_id: 任务 ID
        **fields: 要更新的字段

    示例：
        update_task_field("task-123", status="running", error=None)
    """
    """Partial update: load -> merge -> save."""
    task = load_task(task_id)
    if task is None:
        return
    task.update(fields)
    save_task(task)


def is_redis_available() -> bool:
    """
    检查 Redis 是否可用

    返回：
        bool: Redis 是否可用
    """
    return _get_redis() is not None
