"""
LLM 使用统计模块

这个模块的作用：帮你"记账"——记录每次调用大模型（LLM）花了多少 token。

为什么要统计 token？
━━━━━━━━━━━━━━━━━━━
1. 💰 费用控制：大模型按 token 收费，输入 token 和输出 token 价格不同
   - 输入 token（prompt_tokens）：你发给模型的问题/文档
   - 输出 token（completion_tokens）：模型给你的回答
   - 总 token（total_tokens）：输入 + 输出

2. 📊 用量监控：知道跑了多少次、用了多少量，方便分析成本

3. 🔒 线程安全：多个地方同时调用时，统计数据不会乱

主要类：
- TokenUsage：一个简单的"记账本"，记录一次或多次调用的 token 消耗
- ThreadSafeTokenUsage：线程安全的记账本，多线程环境下也能正确统计

典型用法：
    usage = ThreadSafeTokenUsage()
    response = llm.chat(...)  # 调用大模型
    usage.add_response(response)  # 记录这次调用
    metrics = usage.to_metrics("cleaner")  # 导出为指标字典
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any


def _usage_value(usage: Any, name: str) -> int:
    """
    从 usage 对象中安全提取一个数值字段

    大模型 API 返回的 usage 对象格式不统一：
    - 有的返回字典 dict: {"prompt_tokens": 100, ...}
    - 有的返回对象 object: usage.prompt_tokens

    这个函数统一处理这两种情况，安全提取数值。

    Args:
        usage: usage 对象，可能是 dict 或任意对象，也可能为 None
        name: 字段名，如 "prompt_tokens"、"completion_tokens"、"total_tokens"

    Returns:
        提取到的整数值，如果提取失败或为空则返回 0

    Examples:
        >>> _usage_value({"prompt_tokens": 100}, "prompt_tokens")
        100
        >>> _usage_value(obj, "completion_tokens")  # obj.completion_tokens
        50
        >>> _usage_value(None, "total_tokens")
        0
    """
    if usage is None:
        return 0
    if isinstance(usage, dict):
        # 如果是字典，用 dict.get() 提取，没有就默认 0
        value = usage.get(name, 0)
    else:
        # 如果是对象，用 getattr() 提取属性，没有就默认 0
        value = getattr(usage, name, 0)
    try:
        # 转换为整数，并确保不出现负数
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        # 转换失败就返回 0（比如字段值是 None 或其他奇怪类型）
        return 0


def extract_response_usage(response: Any) -> dict[str, int]:
    """
    从大模型 API 的响应中提取 token 使用统计

    大多数大模型 API（OpenAI、Anthropic、阿里云等）返回格式类似：
        response = {
            "choices": [...],
            "usage": {
                "prompt_tokens": 100,      # 输入 token 数
                "completion_tokens": 50,    # 输出 token 数
                "total_tokens": 150         # 总 token 数
            }
        }

    这个函数把这些信息提取出来，变成一个标准的字典格式。

    Args:
        response: 大模型 API 的响应对象，通常包含 usage 字段

    Returns:
        字典格式的 token 使用统计：
        {
            "prompt_tokens": 输入 token 数,
            "completion_tokens": 输出 token 数,
            "total_tokens": 总 token 数（如果 API 没返回，就自动算）
        }

    Note:
        如果响应中没有 usage 或某些字段缺失，对应值会是 0
        如果 total_tokens 为 0 或负数，会自动计算为 prompt + completion
    """
    # 从响应中提取 usage 对象（可能为 None）
    usage = getattr(response, "usage", None)
    # 逐个提取各个 token 字段
    prompt_tokens = _usage_value(usage, "prompt_tokens")
    completion_tokens = _usage_value(usage, "completion_tokens")
    total_tokens = _usage_value(usage, "total_tokens")
    # 如果 API 没返回 total_tokens，或者返回了 0/负数，就自己算
    if total_tokens <= 0:
        total_tokens = prompt_tokens + completion_tokens
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
    }


@dataclass
class TokenUsage:
    """
    Token 使用统计的"记账本"

    这就是一个简单的数据容器，用来记录和累加 token 使用情况。
    每次调用大模型后，可以把这次的使用量"记"在这个本子上。

    Attributes:
        requests: 调用次数（调了多少次大模型）
        prompt_tokens: 累计输入 token 数（发出去的内容）
        completion_tokens: 累计输出 token 数（收到的回复）
        total_tokens: 累计总 token 数（输入 + 输出）

    Example:
        >>> usage = TokenUsage()
        >>> usage.add_response(response)  # 记录一次调用
        >>> print(f"用了 {usage.total_tokens} 个 token")
    """

    requests: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

    def add_response(self, response: Any) -> None:
        """
        从 LLM 响应中提取使用量并累加

        相当于记账：把这次调用的 token 消耗记到账本上。

        Args:
            response: 大模型 API 的响应对象
        """
        self.add_usage(extract_response_usage(response))

    def add_usage(self, usage: dict[str, int]) -> None:
        """
        累加一次 token 使用量

        这是实际的"记账"操作：把字典里的各项数值加到对应字段上。

        Args:
            usage: 字典格式的 token 使用统计，格式同 extract_response_usage 的返回值
        """
        self.requests += 1  # 调用次数 +1
        self.prompt_tokens += int(usage.get("prompt_tokens", 0) or 0)
        self.completion_tokens += int(usage.get("completion_tokens", 0) or 0)
        self.total_tokens += int(usage.get("total_tokens", 0) or 0)

    def add(self, other: "TokenUsage") -> None:
        """
        合并另一个 TokenUsage 的统计

        比如你有多个模块各自统计，最后合并到一起：
        cleaner_usage.add(chunker_usage)  # 把 chunker 的统计合并到 cleaner

        Args:
            other: 另一个 TokenUsage 实例
        """
        self.requests += other.requests
        self.prompt_tokens += other.prompt_tokens
        self.completion_tokens += other.completion_tokens
        self.total_tokens += other.total_tokens

    def to_metrics(self, prefix: str) -> dict[str, int]:
        """
        转换为指标字典格式，方便导出或上报

        给字段名加个前缀，避免和其他模块的指标重名。

        Args:
            prefix: 字段名前缀，如 "cleaner"、"chunker"

        Returns:
            带前缀的指标字典：
            {
                "cleanerRequests": 10,
                "cleanerPromptTokens": 5000,
                "cleanerCompletionTokens": 2000,
                "cleanerTotalTokens": 7000
            }
        """
        return {
            f"{prefix}Requests": self.requests,
            f"{prefix}PromptTokens": self.prompt_tokens,
            f"{prefix}CompletionTokens": self.completion_tokens,
            f"{prefix}TotalTokens": self.total_tokens,
        }


class ThreadSafeTokenUsage:
    """
    线程安全的 Token 使用统计

    为什么需要线程安全？
    ━━━━━━━━━━━━━━━━━━
    想象一个场景：你的程序同时跑多个任务，都在调用大模型：
    - 任务 A 在第 1 行记录 token
    - 任务 B 在第 2 行记录 token
    - 如果不加锁，两个任务可能同时改同一个变量，导致统计不准

    这个类用"锁"（threading.Lock）来保证：
    同一时刻只有一个线程能修改统计数据，避免冲突。

    用法和 TokenUsage 一样，只是多线程环境下也能安全使用。

    Example:
        >>> usage = ThreadSafeTokenUsage()
        >>> # 多个线程可以同时调用，不会乱
        >>> usage.add_response(response)
        >>> snapshot = usage.snapshot()  # 获取当前统计快照
    """

    def __init__(self) -> None:
        """初始化线程安全的 token 使用统计"""
        self._usage = TokenUsage()  # 内部的记账本
        self._lock = threading.Lock()  # 锁，保证线程安全

    def add_response(self, response: Any) -> None:
        """
        线程安全地记录一次 LLM 响应

        Args:
            response: 大模型 API 的响应对象
        """
        usage = extract_response_usage(response)
        self.add_usage(usage)

    def add_usage(self, usage: dict[str, int]) -> None:
        """
        线程安全地累加 token 使用量

        使用 with self._lock 确保同一时刻只有一个线程能修改数据。

        Args:
            usage: 字典格式的 token 使用统计
        """
        with self._lock:  # 加锁，防止并发冲突
            self._usage.add_usage(usage)

    def snapshot(self) -> TokenUsage:
        """
        获取当前统计的快照

        快照是什么意思？
        ━━━━━━━━━━━━━━━━
        就像拍照一样，记录这一刻的统计数据。
        即使之后有人继续 add()，快照的数据也不会变。

        Returns:
            TokenUsage 实例，包含当前时刻的统计值
        """
        with self._lock:  # 加锁，保证读取时数据不会变
            return TokenUsage(
                requests=self._usage.requests,
                prompt_tokens=self._usage.prompt_tokens,
                completion_tokens=self._usage.completion_tokens,
                total_tokens=self._usage.total_tokens,
            )

    def to_metrics(self, prefix: str) -> dict[str, int]:
        """
        线程安全地转换为指标字典

        Args:
            prefix: 字段名前缀

        Returns:
            带前缀的指标字典
        """
        return self.snapshot().to_metrics(prefix)
