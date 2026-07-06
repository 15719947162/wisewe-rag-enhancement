"""
LLM 清洗模块 - 使用大语言模型优化文档内容质量

本模块提供了基于 LLM 的内容清洗能力，是规则清洗的重要补充。
相比纯规则方法，LLM 清洗能更智能地判断内容价值并修复错误。

## LLM 清洗的作用与优势

### 核心作用
1. **OCR 纠错**：修正 PDF 解析过程中产生的错别字、乱码等问题
2. **内容筛选**：智能判断文本块的信息价值，过滤低质量内容
3. **图片理解**：使用多模态模型（VL）理解图片内容并生成描述

### 相比规则清洗的优势
- **语义理解**：能理解内容上下文，判断"有用"vs"无用"
- **灵活性强**：无需预定义规则，自适应各类文档风格
- **修复能力**：不仅能删除，还能修复 OCR 错误
- **多模态支持**：VL 模型可直接理解图片内容

## 工作流程

1. 分离文本块、图片块、表格块
2. 文本块批量处理：修正 OCR 错误、判断保留/删除
3. 图片块单张处理（仅 VL 模型）：生成描述、判断价值
4. 表格块原样保留（后续切片阶段处理）
5. 汇总处理结果，记录 Token 消耗

## 使用示例

```python
from core.cleaner.llm_cleaner import LLMCleaner
from core.models.content_block import ContentBlock, BlockType

# 创建清洗器实例
cleaner = LLMCleaner(
    model="qwen-plus",       # 文本模型
    batch_size=20,           # 每批处理 20 个文本块
)

# 或使用多模态模型处理图片
vl_cleaner = LLMCleaner(
    model="qwen2.5-vl-72b",  # VL 模型，可处理图片
    batch_size=20,
)

# 执行清洗
blocks = [
    ContentBlock(type=BlockType.TEXT, text="这是一段文本内容"),
    ContentBlock(type=BlockType.IMAGE, text="", image_path="/path/to/image.png"),
]
result = cleaner.apply(blocks)

# 查看结果
print(f"保留: {len(result.blocks)} 块")
print(f"移除: {result.removed_count} 块")
print(f"修正: {result.modified_count} 块")
print(f"详情: {result.details}")
```

## 环境配置

LLM 清洗器按以下优先级读取 API 配置：
1. 构造函数参数（model, api_key, base_url）
2. 专用环境变量：LLM_CLEANER_MODEL, LLM_CLEANER_API_KEY, LLM_CLEANER_BASE_URL
3. 通用环境变量：LLM_MODEL, LLM_API_KEY, LLM_BASE_URL
4. DashScope 默认值：DASHSCOPE_API_KEY, https://dashscope.aliyuncs.com/compatible-mode/v1
"""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from typing import Optional

from core.http_client import create_openai_client
from core.llm_usage import TokenUsage
from core.models.content_block import BlockType, ContentBlock

from .base import CleanResult, CleanerRule

# ============================================================================
# 常量定义
# ============================================================================

# 支持视觉能力的模型关键词列表
# 这些模型名称中包含这些关键词时，会被识别为 VL（Vision-Language）模型
# VL 模型可以接受图片输入，因此能处理图片块
_VL_MODEL_KEYWORDS = ("vl", "vision", "visual", "qwen2.5-vl", "qwen-vl", "qvq")


def _is_vl_model(model_name: str) -> bool:
    """
    判断模型是否支持视觉能力（图片输入）。

    通过检查模型名称中是否包含视觉模型关键词来判断。

    Args:
        model_name: 模型名称，如 "qwen-plus", "qwen-vl-max", "qwen2.5-vl-72b"

    Returns:
        True 如果模型支持图片输入，False 否之

    Examples:
        >>> _is_vl_model("qwen-plus")
        False
        >>> _is_vl_model("qwen-vl-max")
        True
        >>> _is_vl_model("qwen2.5-vl-72b")
        True
    """
    name = model_name.lower()
    return any(kw in name for kw in _VL_MODEL_KEYWORDS)


def _encode_image(image_path: str) -> Optional[str]:
    """
    将本地图片编码为 Base64 字符串，用于 VL API 调用。

    VL 模型通过 API 接收图片时，需要将图片转为 Base64 格式。

    Args:
        image_path: 图片文件的本地路径

    Returns:
        Base64 编码的字符串，如果读取失败则返回 None

    Examples:
        >>> b64 = _encode_image("/path/to/image.png")
        >>> if b64:
        ...     print(f"编码成功，长度: {len(b64)}")
    """
    try:
        with open(image_path, "rb") as f:
            return base64.b64encode(f.read()).decode("utf-8")
    except Exception:
        return None


class LLMCleaner(CleanerRule):
    """
    LLM 内容清洗器 - 使用大语言模型智能清洗文档内容块。

    该类继承自 CleanerRule，实现了基于 LLM 的内容清洗逻辑。
    支持文本模型和多模态模型（VL）两种模式，根据模型类型自动选择处理策略。

    ## 处理策略

    ### 文本模型模式（如 qwen-plus, qwen-max）
    - **文本块**：修正 OCR 错误，判断信息价值，决定保留/删除/修复
    - **图片块**：直接保留原样（文本模型无法处理图片）
    - **表格块**：直接保留原样（交给后续切片阶段处理）

    ### VL 多模态模式（如 qwen-vl-max, qwen2.5-vl-72b）
    - **文本块**：同文本模型模式
    - **图片块**：发送图片给 VL 模型，生成描述，判断信息价值
    - **表格块**：直接保留原样

    ## LLM 如何优化内容

    1. **OCR 纠错**：识别并修正 PDF 解析产生的错别字、乱码
       - 输入："这是-个测试文档" → 输出："这是一个测试文档"

    2. **价值判断**：区分有意义内容 vs 无意义内容
       - 保留：正文、标题、表格数据、有价值的图表说明
       - 删除：广告、版权声明、页码、装饰性文字

    3. **图片理解**（VL 模式）：理解图片内容生成文字描述
       - 输入：病理图片 → 输出："显微镜下的细胞组织切片，显示..."

    Attributes:
        name: 规则名称，固定为 "llm_cleaner"
        model: 使用的 LLM 模型名称
        batch_size: 文本块批处理大小
        system_prompt: 发送给模型的系统提示词
        api_key: API 密钥
        base_url: API 基础 URL
        is_vl: 是否为 VL 多模态模型

    Example:
        >>> cleaner = LLMCleaner(model="qwen-plus", batch_size=20)
        >>> result = cleaner.apply(blocks)
        >>> print(f"处理完成：保留 {len(result.blocks)} 块")
    """

    name = "llm_cleaner"

    # ========================================================================
    # 系统提示词定义
    # ========================================================================

    SYSTEM_PROMPT = (
        "你是文档清洗助手。对给定的文本块列表，完成：\n"
        "1. 修正明显的 OCR 错误（错别字、乱码）\n"
        "2. 判断每个文本块的信息价值\n\n"
        "对每个文本块返回 JSON 对象：\n"
        '{"index": 序号, "action": "keep"|"discard"|"fix", '
        '"fixed_text": "修正后文本（仅fix时）", "reason": "简短说明"}\n\n'
        "丢弃标准：纯广告、版权声明、无实质信息的装饰文本、乱码无法修复\n"
        "保留标准：有知识价值的正文、表格数据、标题\n\n"
        "返回一个 JSON 数组，包含所有文本块的处理结果。"
    )
    """文本块清洗的系统提示词，指导 LLM 如何判断和处理内容。"""

    VL_IMAGE_PROMPT = (
        "这是从PDF文档中提取的图片。请判断该图片的信息价值，并生成简洁的文字描述。\n\n"
        "返回 JSON：\n"
        '{"action": "keep"|"discard", "description": "图片内容描述（keep时必填）", "reason": "判断理由"}\n\n'
        "保留标准：包含有效信息的图表、示意图、病理图片等\n"
        "丢弃标准：纯装饰图、空白图、无法识别的图"
    )
    """图片块处理的系统提示词，仅用于 VL 模型。"""

    def __init__(
        self,
        model: str = "",
        batch_size: int = 20,
        system_prompt: str = "",
        api_key: str = "",
        base_url: str = "",
    ):
        """
        初始化 LLM 清洗器。

        参数优先级从高到低：
        1. 构造函数显式传入的参数
        2. 专用环境变量（LLM_CLEANER_*）
        3. 通用环境变量（LLM_*）
        4. DashScope 默认值

        Args:
            model: LLM 模型名称。默认从 LLM_CLEANER_MODEL 环境变量读取，
                   或使用 "qwen-plus"。使用 VL 模型可处理图片。
            batch_size: 文本块批处理大小。一次 API 调用处理多少个文本块。
                        默认 20，平衡性能与 API 限制。
            system_prompt: 自定义系统提示词。默认使用 SYSTEM_PROMPT 常量。
            api_key: API 密钥。按优先级读取：参数 > LLM_CLEANER_API_KEY >
                     LLM_API_KEY > DASHSCOPE_API_KEY
            base_url: API 基础 URL。按优先级读取：参数 > LLM_CLEANER_BASE_URL >
                      LLM_BASE_URL > DashScope 默认值

        Examples:
            # 使用默认配置
            >>> cleaner = LLMCleaner()

            # 使用 VL 模型处理图片
            >>> cleaner = LLMCleaner(model="qwen2.5-vl-72b")

            # 自定义提示词
            >>> cleaner = LLMCleaner(
            ...     model="qwen-max",
            ...     batch_size=30,
            ...     system_prompt="自定义清洗规则..."
            ... )
        """
        self.model = model or os.environ.get("LLM_CLEANER_MODEL", "qwen-plus")
        self.batch_size = batch_size
        from core.llm_config import resolve_llm_param
        self.system_prompt = (
            system_prompt
            or resolve_llm_param("", "cleaner_system_prompt", ["LLM_CLEANER_SYSTEM_PROMPT"])
            or resolve_llm_param("", "system_prompt", [])
            or self.SYSTEM_PROMPT
        )
        self.api_key = (
            api_key
            or os.environ.get("LLM_CLEANER_API_KEY")
            or os.environ.get("LLM_API_KEY")
            or os.environ.get("DASHSCOPE_API_KEY")
            or ""
        )
        self.base_url = (
            base_url
            or os.environ.get("LLM_CLEANER_BASE_URL")
            or os.environ.get("LLM_BASE_URL")
            or "https://dashscope.aliyuncs.com/compatible-mode/v1"
        )
        self.is_vl = _is_vl_model(self.model)

    def apply(self, blocks: list[ContentBlock]) -> CleanResult:
        """
        执行 LLM 清洗，对内容块列表进行智能处理。

        处理流程：
        1. 检查 API 配置，未配置则跳过清洗
        2. 按类型分离块：文本块、图片块、表格块
        3. 批量处理文本块（调用 _process_text_batch）
        4. 逐个处理图片块（仅 VL 模型，调用 _process_image_block）
        5. 表格块原样保留
        6. 按原始顺序重组结果

        Args:
            blocks: 待清洗的内容块列表

        Returns:
            CleanResult 对象，包含：
            - blocks: 清洗后的内容块列表
            - removed_count: 被删除的块数量
            - modified_count: 被修改的块数量
            - details: 处理详情日志
            - metrics: Token 使用量统计

        Examples:
            >>> cleaner = LLMCleaner(model="qwen-plus")
            >>> blocks = [ContentBlock(...), ...]
            >>> result = cleaner.apply(blocks)
            >>> print(f"保留: {len(result.blocks)}, 移除: {result.removed_count}")
        """
        if not self.api_key:
            return CleanResult(blocks=blocks, details=["LLM 清洗跳过：未配置 API Key"])

        try:
            client = create_openai_client(api_key=self.api_key, base_url=self.base_url)
        except ImportError:
            return CleanResult(blocks=blocks, details=["LLM 清洗跳过：openai 未安装"])

        # 初始化统计变量
        kept: list[ContentBlock] = []      # 保留的块
        removed = 0                         # 删除计数
        modified = 0                        # 修改计数
        details: list[str] = []            # 处理日志
        token_usage = TokenUsage()         # Token 消耗统计

        # --------------------------------------------------------------------
        # 按处理模式分离块
        # 文本块：批量发送给 LLM 处理
        # 图片块：单张发送给 VL 模型（非 VL 模型直接保留）
        # 表格块：始终原样保留
        # --------------------------------------------------------------------
        text_indices: list[int] = []
        image_indices: list[int] = []
        table_indices: list[int] = []

        for i, b in enumerate(blocks):
            if b.type == BlockType.IMAGE:
                image_indices.append(i)
            elif b.is_table:
                table_indices.append(i)
            else:
                text_indices.append(i)

        # --------------------------------------------------------------------
        # 批量处理文本块
        # 每批 batch_size 个块，减少 API 调用次数
        # --------------------------------------------------------------------
        text_results: dict[int, ContentBlock | None] = {}
        text_blocks = [blocks[i] for i in text_indices]
        for batch_start in range(0, len(text_blocks), self.batch_size):
            batch = text_blocks[batch_start:batch_start + self.batch_size]
            results = self._process_text_batch(client, batch, token_usage)
            for j, (block, result) in enumerate(zip(batch, results or [None] * len(batch))):
                orig_idx = text_indices[batch_start + j]
                if result is None:
                    # API 调用失败，保留原块
                    text_results[orig_idx] = block
                elif result.get("action") == "discard":
                    # LLM 判断应删除
                    text_results[orig_idx] = None
                    removed += 1
                elif result.get("action") == "fix":
                    # LLM 判断需修正 OCR 错误
                    fixed = result.get("fixed_text", block.text)
                    text_results[orig_idx] = block.model_copy(update={"text": fixed})
                    modified += 1
                else:
                    # LLM 判断保留原样
                    text_results[orig_idx] = block

        # --------------------------------------------------------------------
        # 处理图片块（仅 VL 模型）
        # 非 VL 模型直接保留图片块原样
        # --------------------------------------------------------------------
        image_results: dict[int, ContentBlock | None] = {}
        for i in image_indices:
            block = blocks[i]
            if self.is_vl and block.image_path and Path(block.image_path).exists():
                # VL 模型：发送图片给模型理解
                result = self._process_image_block(client, block, token_usage)
                if result is None:
                    image_results[i] = block
                elif result.get("action") == "discard":
                    image_results[i] = None
                    removed += 1
                else:
                    # 使用模型生成的描述替换原文本
                    desc = result.get("description", block.text)
                    image_results[i] = block.model_copy(update={"text": desc})
                    modified += 1
            else:
                # 文本模型或无图片路径：保留原样
                image_results[i] = block

        # --------------------------------------------------------------------
        # 按原始顺序重组结果
        # --------------------------------------------------------------------
        for i, block in enumerate(blocks):
            if i in text_results:
                result_block = text_results[i]
            elif i in image_results:
                result_block = image_results[i]
            else:
                result_block = block  # 表格块始终保留
            if result_block is not None:
                kept.append(result_block)

        # 生成处理摘要日志
        mode = "VL模式" if self.is_vl else "文本模式"
        skipped_images = sum(1 for i in image_indices if not self.is_vl)
        msg = f"LLM 清洗({mode} {self.model})：移除 {removed}，修正 {modified}"
        if skipped_images:
            msg += f"，跳过 {skipped_images} 个图片块（文本模型不处理图片）"
        if table_indices:
            msg += f"，跳过 {len(table_indices)} 个表格块（原样保留）"
        details.append(msg)

        return CleanResult(
            blocks=kept,
            removed_count=removed,
            modified_count=modified,
            details=details,
            metrics=token_usage.to_metrics("llmCleaner"),
        )

    def _process_text_batch(
        self,
        client,
        batch: list[ContentBlock],
        token_usage: TokenUsage,
    ) -> Optional[list[dict]]:
        """
        批量处理文本块，调用 LLM 进行智能清洗。

        将一批文本块发送给 LLM，让模型判断每个块应该：
        - keep: 保留原样
        - discard: 删除（无价值内容）
        - fix: 修正 OCR 错误后保留

        为避免输入过长，每个文本块截取前 500 字符。

        Args:
            client: OpenAI 兼容的 API 客户端
            batch: 待处理的文本块列表（通常 20 个左右）
            token_usage: Token 使用量统计对象，会累加本次调用的消耗

        Returns:
            处理结果列表，每个元素为 dict：
            - {"index": int, "action": "keep"|"discard"|"fix",
               "fixed_text": str, "reason": str}
            如果 API 调用失败或解析错误，返回 None

        Examples:
            >>> batch = [ContentBlock(text="测试内容1"), ContentBlock(text="测试内容2")]
            >>> results = self._process_text_batch(client, batch, token_usage)
            >>> if results:
            ...     for r in results:
            ...         print(f"动作: {r['action']}, 理由: {r['reason']}")
        """
        # 构建请求体：截取前 500 字符避免超长
        texts = [{"index": i, "text": b.text[:500], "type": b.type.value}
                 for i, b in enumerate(batch)]
        user_msg = f"文本块列表：\n{json.dumps(texts, ensure_ascii=False)}"

        try:
            response = client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": self.system_prompt},
                    {"role": "user", "content": user_msg},
                ],
                temperature=0,  # 确定性输出，便于调试
            )
            token_usage.add_response(response)

            # 解析响应
            content = response.choices[0].message.content or "[]"
            content = content.strip()

            # 移除可能的 markdown 代码块标记
            if content.startswith("```"):
                content = content.split("\n", 1)[1].rsplit("```", 1)[0]

            result = json.loads(content)
            if isinstance(result, list):
                return result
        except Exception:
            pass
        return None

    def _process_image_block(
        self,
        client,
        block: ContentBlock,
        token_usage: TokenUsage,
    ) -> Optional[dict]:
        """
        处理单个图片块，使用 VL 模型理解图片内容并生成描述。

        将图片编码为 Base64，连同原始说明文字（如有）发送给 VL 模型。
        模型返回：
        - action: "keep" 或 "discard"
        - description: 图片内容描述（keep 时）
        - reason: 判断理由

        Args:
            client: OpenAI 兼容的 API 客户端
            block: 图片内容块，需包含有效的 image_path
            token_usage: Token 使用量统计对象

        Returns:
            处理结果字典：
            - {"action": "keep"|"discard", "description": str, "reason": str}
            如果图片读取失败或 API 调用失败，返回 None

        Examples:
            >>> block = ContentBlock(
            ...     type=BlockType.IMAGE,
            ...     text="图1：细胞结构",
            ...     image_path="/path/to/cell.png"
            ... )
            >>> result = self._process_image_block(client, block, token_usage)
            >>> if result and result["action"] == "keep":
            ...     print(f"描述: {result['description']}")
        """
        # 编码图片为 Base64
        b64 = _encode_image(block.image_path)
        if not b64:
            return None

        # 确定 MIME 类型
        suffix = Path(block.image_path).suffix.lower().lstrip(".")
        mime = {"jpg": "jpeg", "jpeg": "jpeg", "png": "png", "webp": "webp"}.get(suffix, "png")

        # 构建提示词：包含原始说明文字（如有）
        caption_hint = f"\n图片说明文字：{block.text}" if block.text.strip() else ""
        prompt = self.VL_IMAGE_PROMPT + caption_hint

        try:
            response = client.chat.completions.create(
                model=self.model,
                messages=[{
                    "role": "user",
                    "content": [
                        # 图片输入
                        {"type": "image_url",
                         "image_url": {"url": f"data:image/{mime};base64,{b64}"}},
                        # 文本提示
                        {"type": "text", "text": prompt},
                    ],
                }],
                temperature=0,
            )
            token_usage.add_response(response)

            # 解析响应
            content = response.choices[0].message.content or "{}"
            content = content.strip()

            # 移除可能的 markdown 代码块标记
            if content.startswith("```"):
                content = content.split("\n", 1)[1].rsplit("```", 1)[0]

            result = json.loads(content)
            if isinstance(result, dict):
                return result
        except Exception:
            pass
        return None
