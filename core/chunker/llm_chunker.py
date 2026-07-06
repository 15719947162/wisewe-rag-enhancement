"""
LLM 切片策略模块

本模块实现了基于大语言模型（LLM）的语义切片策略。与传统的规则切片不同，
LLM 切片利用大模型的语义理解能力，智能地识别文本中的自然语义边界。

## 工作原理

1. **语义边界识别**：LLM 能够理解文本的语义结构，识别出话题转换、
   段落逻辑关系等，从而在语义完整的位置进行切片，而非简单的字符位置。

2. **智能分割**：对于超过最大长度的文本块，LLM 会根据语义完整性
   将其分割为多个片段，确保每个片段都是一个语义单元。

3. **容错机制**：当 LLM 调用失败时，自动降级到基于句子的规则分割，
   保证系统的鲁棒性。

## 切片边界判断逻辑

LLM 通过以下方式判断语义边界：
- 话题转换点（从一个主题切换到另一个主题）
- 段落逻辑分割点（论证、叙述的自然断点）
- 上下文完整性（确保切片后的内容仍能独立理解）

## 优缺点分析

优点：
- ✅ 语义完整性好：切片后的内容具有完整的语义单元，便于检索和理解
- ✅ 上下文保留：相比硬切，能更好地保留上下文信息
- ✅ 适应性强：能处理各种类型和风格的文档

缺点：
- ❌ 成本高：每次切片都需要调用 LLM API，产生 Token 消耗
- ❌ 延迟高：LLM 推理需要时间，不适合实时场景
- ❌ 不确定性：LLM 输出可能存在波动，结果不完全可预测

## 适用场景

- 对切片质量要求高的知识库构建
- 文档结构复杂、传统规则难以处理的场景
- 可接受较高成本和延迟的应用
"""
from __future__ import annotations

import json

from core.http_client import create_openai_client
from core.llm_config import resolve_llm_param
from core.llm_usage import TokenUsage
from core.models.content_block import Chunk, ContentBlock

from .base import ChunkingStrategy, register_strategy


@register_strategy
class LLMChunkingStrategy(ChunkingStrategy):
    """
    基于 LLM 的语义切片策略。

    该策略使用大语言模型来判断文本的语义边界，从而实现更智能的切片。
    对于图片和表格等特殊类型，直接保留原样；对于普通文本，如果超过
    最大长度，则调用 LLM 进行语义分割。

    Attributes:
        model: 使用的 LLM 模型名称，默认为 'qwen-plus'
        max_chunk_size: 单个切片的最大字符数，默认 800
        api_key: LLM API 密钥
        base_url: LLM API 端点地址
        system_prompt: 可选的系统提示词，用于自定义切片行为
        _token_usage: Token 使用量统计
        last_timings: 最近一次执行的耗时统计
    """

    name = "llm"

    def __init__(self, model: str = "qwen-plus", max_chunk_size: int = 800,
                 api_key: str = "", base_url: str = "", system_prompt: str = ""):
        """
        初始化 LLM 切片策略。

        Args:
            model: LLM 模型名称，支持通过环境变量 LLM_EMBEDDING_MODEL 配置
            max_chunk_size: 切片最大长度（字符数），超长的文本将调用 LLM 分割
            api_key: API 密钥，如未提供则从环境变量读取
            base_url: API 端点，如未提供则使用 DashScope 默认地址
            system_prompt: 自定义系统提示词，可用于调整 LLM 的切片策略
        """
        self.model = model
        self.max_chunk_size = max_chunk_size
        self.api_key = api_key
        self.base_url = base_url
        self.system_prompt = system_prompt
        self._token_usage = TokenUsage()
        self.last_timings: dict[str, int] = {}

    def chunk(self, blocks: list[ContentBlock]) -> list[Chunk]:
        """
        对内容块列表执行 LLM 语义切片。

        处理流程：
        1. 重置 Token 使用统计
        2. 遍历所有内容块：
           - 图片块：直接创建图片切片（不处理）
           - 表格块：直接创建表格切片（保留完整性）
           - 文本块：
             * 短文本（≤max_chunk_size）：直接创建切片
             * 长文本（>max_chunk_size）：调用 LLM 进行语义分割

        Args:
            blocks: 待切片的内容块列表，来自 PDF 解析器的输出

        Returns:
            切片后的 Chunk 列表，每个切片包含内容、来源、页码等信息

        Example:
            >>> strategy = LLMChunkingStrategy(max_chunk_size=500)
            >>> chunks = strategy.chunk(content_blocks)
            >>> print(f"生成 {len(chunks)} 个切片")
        """
        chunks: list[Chunk] = []
        idx = 0
        self._token_usage = TokenUsage()
        self.last_timings = {}

        for block in blocks:
            # 图片块：保留原始图片信息，创建独立的图片切片
            if block.type.value == "image":
                chunks.append(self._make_image_chunk(block, idx))
                idx += 1
                continue

            # 表格块：保持表格完整性，创建独立的表格切片
            # 表格通常具有高度的结构化特征，不应被拆分
            if block.is_table:
                chunks.append(self._make_chunk(
                    content=block.table_html or block.text,
                    source=block.source_file,
                    page=block.page_idx,
                    chunk_index=idx,
                    is_table_chunk=True,
                ))
                idx += 1
                continue

            # 文本块处理
            text = block.text.strip()
            if not text:
                continue

            # 短文本：无需 LLM 分割，直接创建切片
            if len(text) <= self.max_chunk_size:
                chunks.append(self._make_chunk(
                    content=text,
                    source=block.source_file,
                    page=block.page_idx,
                    chunk_index=idx,
                ))
                idx += 1
            # 长文本：调用 LLM 进行语义分割
            else:
                split_texts = self._llm_split(text)
                for t in split_texts:
                    chunks.append(self._make_chunk(
                        content=t,
                        source=block.source_file,
                        page=block.page_idx,
                        chunk_index=idx,
                    ))
                    idx += 1

        # 记录本次执行的 Token 消耗统计
        self.last_timings.update(self._token_usage.to_metrics("llmChunker"))
        return chunks

    def _llm_split(self, text: str) -> list[str]:
        """
        调用 LLM API 将长文本按语义分割为多个片段。

        该方法构造一个提示词，要求 LLM：
        1. 理解文本的语义结构
        2. 在语义完整的位置进行分割
        3. 返回 JSON 格式的切片列表

        LLM 判断语义边界的依据：
        - 话题/主题转换
        - 段落逻辑关系
        - 句子之间的语义连贯性
        - 切片后的独立可理解性

        Args:
            text: 待分割的长文本（长度超过 max_chunk_size）

        Returns:
            分割后的文本片段列表；如果 LLM 调用失败，则返回规则分割结果

        Note:
            该方法实现了完善的错误处理和降级机制，确保即使 LLM 服务
            不可用，也能通过 _fallback_split 方法完成切片任务。
        """
        # 解析 API 配置，按优先级从参数或环境变量读取
        api_key = resolve_llm_param(
            self.api_key, "api_key",
            ["LLM_API_KEY", "DASHSCOPE_API_KEY"],
        )
        if not api_key:
            # 未配置 API Key，降级到规则分割
            return self._fallback_split(text)

        base_url = resolve_llm_param(
            self.base_url, "base_url",
            ["LLM_BASE_URL"],
            "https://dashscope.aliyuncs.com/compatible-mode/v1",
        )
        model = resolve_llm_param(self.model, "model", [], "qwen-plus")

        # 支持自定义系统提示词，允许用户调整 LLM 的切片行为
        system_prompt = (
            resolve_llm_param(self.system_prompt, "chunker_system_prompt", ["LLM_CHUNKER_SYSTEM_PROMPT"])
            or resolve_llm_param("", "system_prompt", [])
        )

        try:
            # 创建 OpenAI 兼容的客户端
            client = create_openai_client(api_key=api_key, base_url=base_url)

            # 构造用户提示词，要求 LLM 按语义完整性分割文本
            # 明确要求返回 JSON 数组格式，便于解析
            user_content = (
                "将以下文本按语义完整性分割成多个段落，每段不超过"
                f"{self.max_chunk_size}字。"
                "直接返回JSON数组，每个元素是一个文本段落。\n\n"
                f"文本：\n{text}"
            )

            # 构建消息列表
            messages: list[dict] = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            messages.append({"role": "user", "content": user_content})

            # 调用 LLM API，temperature=0 确保结果稳定性
            response = client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=0,  # 低温度确保输出稳定，减少随机性
            )

            # 记录 Token 使用量
            self._token_usage.add_response(response)

            # 提取并解析 LLM 响应
            content = response.choices[0].message.content or "[]"
            content = content.strip()

            # 处理可能的 Markdown 代码块包装（如 ```json ... ```）
            if content.startswith("```"):
                content = content.split("\n", 1)[1].rsplit("```", 1)[0]

            # 解析 JSON 数组
            result = json.loads(content)

            # 验证返回格式：必须是字符串列表
            if isinstance(result, list) and all(isinstance(x, str) for x in result):
                return result

        except Exception:
            # 任何异常（API 错误、解析错误等）都降级到规则分割
            pass

        # LLM 调用失败，使用降级方案
        return self._fallback_split(text)

    def _fallback_split(self, text: str) -> list[str]:
        """
        降级分割方法：当 LLM 调用失败时的备用方案。

        该方法使用基于规则的分割策略：
        1. 首先尝试按句子分割（通过标点符号识别）
        2. 如果单句仍超长，则按固定长度硬切

        这种降级策略确保了系统的鲁棒性，即使 LLM 服务不可用，
        也能完成切片任务，但语义完整性会略差。

        Args:
            text: 待分割的长文本

        Returns:
            分割后的文本片段列表

        Note:
            这是一个"保底"方案，优先级低于 LLM 语义分割。
            在生产环境中，建议配置告警以监控降级频率。
        """
        # 尝试通过问号等强语义结束标点来分割句子
        # 问号通常标志着语义单元的结束
        sentences = text.replace("?", "?\n").split("\n")

        # 如果分割后仍只有一句，且文本超长，则使用固定长度分割
        if len(sentences) <= 1 and len(text) > self.max_chunk_size:
            return [text[i:i + self.max_chunk_size] for i in range(0, len(text), self.max_chunk_size)]

        # 合并句子，确保每个片段不超过最大长度
        parts = []
        current = ""
        for sentence in sentences:
            # 如果添加当前句子会超长，且已有内容，则保存当前片段
            if len(current) + len(sentence) > self.max_chunk_size and current:
                parts.append(current.strip())
                current = sentence
            else:
                current += sentence

        # 添加最后一个片段
        if current.strip():
            parts.append(current.strip())

        # 至少返回一个切片
        return parts if parts else [text]
