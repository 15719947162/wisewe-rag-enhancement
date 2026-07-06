"""
清洗规则基础模块

本模块定义了内容清洗管道的核心抽象和数据结构。采用策略模式（Strategy Pattern），
允许通过实现 CleanerRule 抽象基类来创建可插拔的清洗规则。

设计模式说明：
- 抽象基类（ABC）：定义清洗规则的统一接口 apply()，所有具体规则必须实现此方法
- 策略模式：不同的清洗规则可以独立实现，在运行时动态组合和执行
- 数据类（dataclass）：用于承载清洗结果和被移除块的信息，保证数据结构清晰

清洗流程：
1. 解析器输出 ContentBlock 列表
2. 按顺序应用多个清洗规则（RemoveEmptyBlocks → RemoveShortBlocks → ...）
3. 每个规则返回 CleanResult，包含保留的块、移除统计和详细信息
4. 最终输出清洗后的 ContentBlock 列表供切片器使用
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from core.models.content_block import ContentBlock


@dataclass
class RemovedBlock:
    """
    被移除的内容块记录

    当清洗规则移除一个 ContentBlock 时，创建此对象记录被移除块的信息，
    用于审计追踪和质量分析。

    Attributes:
        rule: 移除此块的规则名称（如 "remove_empty", "remove_short_blocks"）
        text: 被移除块的文本内容（用于后续审查）
        page_idx: 块在原文中的页码索引
        block_type: 块的原始类型（如 "text", "table", "image"）
    """
    rule: str
    text: str
    page_idx: int
    block_type: str


@dataclass
class CleanResult:
    """
    清洗操作的返回结果

    封装清洗规则执行后的所有结果数据，包括保留的内容块、统计信息和详细记录。
    各规则返回此对象，管道可以据此追踪清洗效果。

    Attributes:
        blocks: 清洗后保留的 ContentBlock 列表（可能已修改）
        removed_count: 被移除的块数量
        modified_count: 被修改的块数量（如文本规范化）
        details: 人类可读的操作详情列表，用于日志和调试
        removed_blocks: 被移除块的详细记录，用于审计和质量分析
        metrics: 自定义指标字典，规则可记录任意键值对（如字符数阈值、匹配次数等）

    示例:
        >>> result = CleanResult(
        ...     blocks=cleaned_blocks,
        ...     removed_count=5,
        ...     details=["移除了 3 个空块", "移除了 2 个过短块"]
        ... )
    """
    blocks: list[ContentBlock]
    removed_count: int = 0
    modified_count: int = 0
    details: list[str] = field(default_factory=list)
    removed_blocks: list[RemovedBlock] = field(default_factory=list)
    metrics: dict[str, int] = field(default_factory=dict)


class CleanerRule(ABC):
    """
    清洗规则抽象基类

    所有清洗规则必须继承此类并实现 apply() 方法。清洗规则采用策略模式，
    每个 Rule 封装一种特定的清洗逻辑，可以独立测试和组合。

    规则类型示例：
        - 过滤型：移除空块、过短块、版权广告等
        - 修改型：规范化文本、修复编码问题等
        - 分析型：统计块属性、标记可疑内容等

    规则豁免机制：
        部分规则需要对特定类型的内容块豁免（如图片块、表格块不应被误删）。
        各规则应在 apply() 中自行检查 ContentBlock.type 进行豁免处理。

    Attributes:
        name: 规则名称，用于日志输出和结果追踪

    实现要求：
        - 必须设置唯一的 name 属性
        - apply() 方法必须返回 CleanResult 对象
        - 不应抛出异常，错误应记录在 details 中
        - 应保持幂等性（多次应用结果一致）

    示例:
        >>> class RemoveEmptyBlocks(CleanerRule):
        ...     name = "remove_empty"
        ...     def apply(self, blocks):
        ...         cleaned = [b for b in blocks if b.text and b.text.strip()]
        ...         return CleanResult(
        ...             blocks=cleaned,
        ...             removed_count=len(blocks) - len(cleaned)
        ...         )
    """
    name: str = "base"

    @abstractmethod
    def apply(self, blocks: list[ContentBlock]) -> CleanResult:
        """
        应用清洗规则到内容块列表

        Args:
            blocks: 待清洗的 ContentBlock 列表（来自解析器或上一规则）

        Returns:
            CleanResult: 包含清洗后块列表和统计信息的结果对象

        实现注意事项：
            - 输入的 blocks 列表不应被原地修改
            - 应处理空列表情况
            - 图片块（type="image"）在所有内置规则中均被豁免
            - 返回的 blocks 列表顺序应保持一致
        """
        ...
