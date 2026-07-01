from __future__ import annotations

import json

from core.http_client import create_openai_client
from core.llm_config import resolve_llm_param
from core.llm_usage import TokenUsage
from core.models.content_block import Chunk, ContentBlock

from .base import ChunkingStrategy, register_strategy


@register_strategy
class LLMChunkingStrategy(ChunkingStrategy):
    """Use LLM to determine optimal chunk boundaries."""

    name = "llm"

    def __init__(self, model: str = "qwen-plus", max_chunk_size: int = 800,
                 api_key: str = "", base_url: str = "", system_prompt: str = ""):
        self.model = model
        self.max_chunk_size = max_chunk_size
        self.api_key = api_key
        self.base_url = base_url
        self.system_prompt = system_prompt
        self._token_usage = TokenUsage()
        self.last_timings: dict[str, int] = {}

    def chunk(self, blocks: list[ContentBlock]) -> list[Chunk]:
        chunks: list[Chunk] = []
        idx = 0
        self._token_usage = TokenUsage()
        self.last_timings = {}

        for block in blocks:
            if block.type.value == "image":
                chunks.append(self._make_image_chunk(block, idx))
                idx += 1
                continue

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

            text = block.text.strip()
            if not text:
                continue

            if len(text) <= self.max_chunk_size:
                chunks.append(self._make_chunk(
                    content=text,
                    source=block.source_file,
                    page=block.page_idx,
                    chunk_index=idx,
                ))
                idx += 1
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

        self.last_timings.update(self._token_usage.to_metrics("llmChunker"))
        return chunks

    def _llm_split(self, text: str) -> list[str]:
        """Call LLM API to split text into semantic chunks."""
        api_key = resolve_llm_param(
            self.api_key, "api_key",
            ["LLM_API_KEY", "DASHSCOPE_API_KEY"],
        )
        if not api_key:
            return self._fallback_split(text)

        base_url = resolve_llm_param(
            self.base_url, "base_url",
            ["LLM_BASE_URL"],
            "https://dashscope.aliyuncs.com/compatible-mode/v1",
        )
        model = resolve_llm_param(self.model, "model", [], "qwen-plus")
        system_prompt = (
            resolve_llm_param(self.system_prompt, "chunker_system_prompt", ["LLM_CHUNKER_SYSTEM_PROMPT"])
            or resolve_llm_param("", "system_prompt", [])
        )

        try:
            client = create_openai_client(api_key=api_key, base_url=base_url)
            user_content = (
                "将以下文本按语义完整性分割成多个段落，每段不超过"
                f"{self.max_chunk_size}字。"
                "直接返回JSON数组，每个元素是一个文本段落。\n\n"
                f"文本：\n{text}"
            )
            messages: list[dict] = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            messages.append({"role": "user", "content": user_content})
            response = client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=0,
            )
            self._token_usage.add_response(response)
            content = response.choices[0].message.content or "[]"
            content = content.strip()
            if content.startswith("```"):
                content = content.split("\n", 1)[1].rsplit("```", 1)[0]
            result = json.loads(content)
            if isinstance(result, list) and all(isinstance(x, str) for x in result):
                return result
        except Exception:
            pass

        return self._fallback_split(text)

    def _fallback_split(self, text: str) -> list[str]:
        """Fallback: split by sentences, then by length if needed."""
        sentences = text.replace("?", "?\n").split("\n")
        if len(sentences) <= 1 and len(text) > self.max_chunk_size:
            return [text[i:i + self.max_chunk_size] for i in range(0, len(text), self.max_chunk_size)]

        parts = []
        current = ""
        for sentence in sentences:
            if len(current) + len(sentence) > self.max_chunk_size and current:
                parts.append(current.strip())
                current = sentence
            else:
                current += sentence
        if current.strip():
            parts.append(current.strip())
        return parts if parts else [text]
        parts = []
        current = ""
        for sentence in sentences:
            if len(current) + len(sentence) > self.max_chunk_size and current:
                parts.append(current.strip())
                current = sentence
            else:
                current += sentence
        if current.strip():
            parts.append(current.strip())
        return parts if parts else [text]
