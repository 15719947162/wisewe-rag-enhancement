"""
清洗管道模块（Cleaner Pipeline）

本模块提供了对解析器输出的 ContentBlock 进行清洗的核心功能。清洗管道采用
规则引擎 + LLM 辅助的双模式设计，支持灵活配置和扩展。

## 整体设计

清洗管道的主要职责：
1. 移除无效或低质量的内容块（如空块、纯标点块、版权声明等）
2. 通过 LLM 进行智能内容优化（可选）
3. 记录清洗过程的所有细节和统计信息

## 工作流程

```
输入：ContentBlock 列表（来自 Parser）
  ↓
规则清洗阶段（可选，默认启用）
  ├─ 移除空块（RemoveEmptyBlocks）
  ├─ 移除过短块（RemoveShortBlocks）
  ├─ 移除高标点密度块（RemovePunctuation）
  └─ 移除版权广告（RemoveCopyrightAds）
  ↓
LLM 清洗阶段（可选，默认禁用）
  └─ 使用大语言模型智能清洗内容
  ↓
输出：CleanResult（清洗后的内容块 + 统计信息）
```

## 规则清洗 vs LLM 清洗

**规则清洗（Rule-based Cleaning）：**
- 执行速度快，无 API 成本
- 基于预定义规则，可精确控制
- 适合结构性问题（如空内容、长度、特殊格式）
- 规则可组合、可扩展
- 适用于所有 PDF 文档

**LLM 清洗（LLM-based Cleaning）：**
- 需要 API 调用，有成本和延迟
- 理解语义，能处理复杂场景
- 可自定义 system prompt 指导清洗逻辑
- 适合需要语义理解的内容优化
- 应谨慎使用，仅在必要时启用

## 扩展性

可通过继承 CleanerRule 基类自定义清洗规则：
```python
from core.cleaner import CleanerRule, CleanResult

class MyCustomRule(CleanerRule):
    def apply(self, blocks: list[ContentBlock]) -> CleanResult:
        # 实现自定义清洗逻辑
        pass
```

## 相关模块

- rules/：预定义清洗规则实现
- llm_cleaner.py：LLM 清洗器实现
- quality_gate.py：切片后质量门控
- base.py：基础类型定义（CleanResult, CleanerRule, RemovedBlock）
"""

from __future__ import annotations

from core.models.content_block import ContentBlock

from .base import CleanResult, CleanerRule, RemovedBlock
from .llm_cleaner import LLMCleaner
from .quality_gate import QualityGateResult, apply_quality_gate
from .rules import DEFAULT_RULES


def clean_blocks(
    blocks: list[ContentBlock],
    use_rules: bool = True,
    use_llm: bool = False,
    rules: list[CleanerRule] | None = None,
    llm_system_prompt: str = "",
    llm_model: str = "",
    llm_base_url: str = "",
    llm_api_key: str = "",
) -> CleanResult:
    """
    对内容块执行清洗管道。

    此函数是清洗模块的主入口，按照配置的顺序执行清洗操作：
    1. 先执行规则清洗（如果启用）
    2. 再执行 LLM 清洗（如果启用）

    清洗过程会累积所有规则的统计信息，包括移除数量、修改数量、
    详细日志等，便于后续分析和调试。

    ## 参数

    **blocks**: `list[ContentBlock]`
        - 待清洗的内容块列表
        - 通常来自 Parser 的输出
        - 清洗过程不会修改原始列表

    **use_rules**: `bool` (默认: True)
        - 是否启用规则清洗
        - True: 使用预定义或自定义规则进行清洗
        - False: 跳过规则清洗阶段

    **use_llm**: `bool` (默认: False)
        - 是否启用 LLM 清洗
        - True: 调用大语言模型进行智能清洗
        - False: 跳过 LLM 清洗阶段
        - 注意：启用后会产生 API 调用费用

    **rules**: `list[CleanerRule] | None` (默认: None)
        - 自定义规则列表
        - None: 使用默认规则集
          - RemoveEmptyBlocks()：移除空内容块
          - RemoveShortBlocks(min_chars=2)：移除少于 2 字符的块
          - RemovePunctuation(threshold=0.8)：移除标点占比 >80% 的块
          - RemoveCopyrightAds()：移除版权声明和广告
        - 传入自定义列表：完全替换默认规则

    **llm_system_prompt**: `str` (默认: "")
        - LLM 清洗的系统提示词
        - 空字符串：使用 LLMCleaner 默认提示词
        - 自定义：指导 LLM 如何清洗内容

    **llm_model**: `str` (默认: "")
        - LLM 模型名称（如 "gpt-4", "claude-3-opus"）
        - 空字符串：使用环境变量中的模型配置

    **llm_base_url**: `str` (默认: "")
        - LLM API 的 base URL
        - 空字符串：使用环境变量中的 URL 配置

    **llm_api_key**: `str` (默认: "")
        - LLM API 密钥
        - 空字符串：使用环境变量中的密钥

    ## 返回值

    **CleanResult** - 包含以下字段：

    - `blocks`: `list[ContentBlock]` - 清洗后的内容块列表
    - `removed_count`: `int` - 累计移除的内容块数量
    - `modified_count`: `int` - 累计修改的内容块数量
    - `details`: `list[str]` - 清洗过程的详细日志
    - `removed_blocks`: `list[RemovedBlock]` - 被移除块的详细信息
    - `metrics`: `dict[str, int]` - 各规则的统计指标

    ## 使用示例

    ### 基础用法（使用默认规则）
    ```python
    from core.parser import parse_pdf_from_url
    from core.cleaner import clean_blocks

    # 解析 PDF 获取内容块
    blocks = parse_pdf_from_url(pdf_url, pdf_path, output_dir)

    # 使用默认规则清洗
    result = clean_blocks(blocks)

    print(f"移除了 {result.removed_count} 个内容块")
    print(f"保留了 {len(result.blocks)} 个内容块")

    # 查看清洗详情
    for detail in result.details:
        print(detail)
    ```

    ### 使用自定义规则
    ```python
    from core.cleaner import clean_blocks, RemoveEmptyBlocks, RemoveShortBlocks

    # 只使用部分规则
    custom_rules = [
        RemoveEmptyBlocks(),
        RemoveShortBlocks(min_chars=10),  # 更严格的长度阈值
    ]

    result = clean_blocks(blocks, rules=custom_rules)
    ```

    ### 启用 LLM 清洗
    ```python
    from core.cleaner import clean_blocks
    import os

    result = clean_blocks(
        blocks,
        use_rules=True,
        use_llm=True,
        llm_model="gpt-4",
        llm_api_key=os.getenv("OPENAI_API_KEY"),
        llm_system_prompt="请移除文档中的重复内容和无关描述",
    )
    ```

    ### 禁用规则清洗，仅使用 LLM
    ```python
    result = clean_blocks(
        blocks,
        use_rules=False,
        use_llm=True,
        llm_api_key=os.getenv("LLM_API_KEY"),
    )
    ```

    ### 完整管道（解析 → 清洗 → 切片）
    ```python
    from core.parser import parse_pdf
    from core.cleaner import clean_blocks
    from core.chunker import get_strategy

    # 1. 解析
    blocks = parse_pdf(pdf_path, output_dir)

    # 2. 清洗
    clean_result = clean_blocks(blocks, use_rules=True)

    # 3. 切片
    chunker = get_strategy("paragraph")
    chunks = chunker.chunk(clean_result.blocks)
    ```

    ## 注意事项

    1. **性能考虑**：规则清洗速度快且无成本，建议始终启用
    2. **LLM 成本**：启用 use_llm 会产生 API 调用费用，按 token 计费
    3. **执行顺序**：规则清洗先于 LLM 清洗执行
    4. **统计累加**：多个清洗阶段的统计信息会累积
    5. **原始数据保护**：函数不会修改输入的 blocks 列表

    ## 相关函数

    - `apply_quality_gate()`: 切片后的质量门控检查
    - `CleanerRule.apply()`: 单个规则的清洗逻辑
    """
    # 初始化清洗状态
    current = list(blocks)  # 复制输入列表，避免修改原数据
    total_removed = 0
    total_modified = 0
    all_details: list[str] = []
    all_removed_blocks: list[RemovedBlock] = []
    metrics: dict[str, int] = {}

    def merge_metrics(values: dict[str, int]) -> None:
        """合并规则返回的统计指标。"""
        for key, value in values.items():
            metrics[key] = int(metrics.get(key, 0) or 0) + int(value or 0)

    # ========================================
    # 阶段 1: 规则清洗（Rule-based Cleaning）
    # ========================================
    if use_rules:
        # 确定使用的规则集
        active_rules = rules
        if active_rules is None:
            # 使用默认规则集（如果未提供自定义规则）
            from .rules import (
                RemoveCopyrightAds,
                RemoveEmptyBlocks,
                RemovePunctuation,
                RemoveShortBlocks,
            )
            active_rules = [
                RemoveEmptyBlocks(),           # 移除空内容块
                RemoveShortBlocks(min_chars=2), # 移除少于 2 字符的块
                RemovePunctuation(threshold=0.8), # 移除标点占比 >80% 的块
                RemoveCopyrightAds(),          # 移除版权声明和广告
            ]

        # 依次执行每条规则
        for rule in active_rules:
            result = rule.apply(current)
            current = result.blocks  # 更新当前内容块列表
            total_removed += result.removed_count
            total_modified += result.modified_count
            all_details.extend(result.details)
            all_removed_blocks.extend(result.removed_blocks)
            merge_metrics(result.metrics)

    # ========================================
    # 阶段 2: LLM 清洗（LLM-based Cleaning）
    # ========================================
    if use_llm:
        # 初始化 LLM 清洗器
        llm = LLMCleaner(system_prompt=llm_system_prompt, model=llm_model, base_url=llm_base_url, api_key=llm_api_key)
        result = llm.apply(current)
        current = result.blocks
        total_removed += result.removed_count
        total_modified += result.modified_count
        all_details.extend(result.details)
        all_removed_blocks.extend(result.removed_blocks)
        merge_metrics(result.metrics)

    # ========================================
    # 返回清洗结果
    # ========================================
    return CleanResult(
        blocks=current,               # 清洗后的内容块
        removed_count=total_removed,  # 总移除数
        modified_count=total_modified, # 总修改数
        details=all_details,          # 详细日志
        removed_blocks=all_removed_blocks, # 被移除块列表
        metrics=metrics,              # 统计指标
    )


# ========================================
# 模块公共接口
# ========================================
__all__ = [
    # 核心类型
    "CleanResult",      # 清洗结果数据类
    "CleanerRule",      # 清洗规则抽象基类
    "RemovedBlock",     # 被移除块的详细信息
    # 清洗器
    "LLMCleaner",       # LLM 智能清洗器
    # 质量门控
    "QualityGateResult", # 质量门控结果
    "apply_quality_gate", # 质量门控函数
    # 主入口函数
    "clean_blocks",     # 清洗管道主函数
]
