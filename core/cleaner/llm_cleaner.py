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

# Models that support vision (image input)
_VL_MODEL_KEYWORDS = ("vl", "vision", "visual", "qwen2.5-vl", "qwen-vl", "qvq")


def _is_vl_model(model_name: str) -> bool:
    name = model_name.lower()
    return any(kw in name for kw in _VL_MODEL_KEYWORDS)


def _encode_image(image_path: str) -> Optional[str]:
    """Encode local image to base64 for VL API."""
    try:
        with open(image_path, "rb") as f:
            return base64.b64encode(f.read()).decode("utf-8")
    except Exception:
        return None


class LLMCleaner(CleanerRule):
    """Use LLM to clean blocks.

    Text models (qwen-plus, qwen-max, etc.):
      - Image/table blocks are skipped (preserved as-is)
      - Text blocks: fix OCR errors, judge info value

    VL models (qwen-vl-max, qwen2.5-vl-72b, etc.):
      - Image blocks: send image + caption, model generates description
      - Text/table blocks: same as text model
    """

    name = "llm_cleaner"

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

    VL_IMAGE_PROMPT = (
        "这是从PDF文档中提取的图片。请判断该图片的信息价值，并生成简洁的文字描述。\n\n"
        "返回 JSON：\n"
        '{"action": "keep"|"discard", "description": "图片内容描述（keep时必填）", "reason": "判断理由"}\n\n'
        "保留标准：包含有效信息的图表、示意图、病理图片等\n"
        "丢弃标准：纯装饰图、空白图、无法识别的图"
    )

    def __init__(
        self,
        model: str = "",
        batch_size: int = 20,
        system_prompt: str = "",
        api_key: str = "",
        base_url: str = "",
    ):
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
        if not self.api_key:
            return CleanResult(blocks=blocks, details=["LLM 清洗跳过：未配置 API Key"])

        try:
            client = create_openai_client(api_key=self.api_key, base_url=self.base_url)
        except ImportError:
            return CleanResult(blocks=blocks, details=["LLM 清洗跳过：openai 未安装"])

        kept: list[ContentBlock] = []
        removed = 0
        modified = 0
        details: list[str] = []
        token_usage = TokenUsage()

        # Separate blocks by processing mode
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

        # Process text blocks in batches
        text_results: dict[int, ContentBlock | None] = {}
        text_blocks = [blocks[i] for i in text_indices]
        for batch_start in range(0, len(text_blocks), self.batch_size):
            batch = text_blocks[batch_start:batch_start + self.batch_size]
            results = self._process_text_batch(client, batch, token_usage)
            for j, (block, result) in enumerate(zip(batch, results or [None] * len(batch))):
                orig_idx = text_indices[batch_start + j]
                if result is None:
                    text_results[orig_idx] = block
                elif result.get("action") == "discard":
                    text_results[orig_idx] = None
                    removed += 1
                elif result.get("action") == "fix":
                    fixed = result.get("fixed_text", block.text)
                    text_results[orig_idx] = block.model_copy(update={"text": fixed})
                    modified += 1
                else:
                    text_results[orig_idx] = block

        # Process image blocks
        image_results: dict[int, ContentBlock | None] = {}
        for i in image_indices:
            block = blocks[i]
            if self.is_vl and block.image_path and Path(block.image_path).exists():
                result = self._process_image_block(client, block, token_usage)
                if result is None:
                    image_results[i] = block
                elif result.get("action") == "discard":
                    image_results[i] = None
                    removed += 1
                else:
                    desc = result.get("description", block.text)
                    image_results[i] = block.model_copy(update={"text": desc})
                    modified += 1
            else:
                # Text model or no image path: preserve as-is
                image_results[i] = block

        # Rebuild in original order
        for i, block in enumerate(blocks):
            if i in text_results:
                result_block = text_results[i]
            elif i in image_results:
                result_block = image_results[i]
            else:
                result_block = block  # table: always keep
            if result_block is not None:
                kept.append(result_block)

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
                temperature=0,
            )
            token_usage.add_response(response)
            content = response.choices[0].message.content or "[]"
            content = content.strip()
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
        """Send image to VL model for description and value judgment."""
        b64 = _encode_image(block.image_path)
        if not b64:
            return None

        suffix = Path(block.image_path).suffix.lower().lstrip(".")
        mime = {"jpg": "jpeg", "jpeg": "jpeg", "png": "png", "webp": "webp"}.get(suffix, "png")

        caption_hint = f"\n图片说明文字：{block.text}" if block.text.strip() else ""
        prompt = self.VL_IMAGE_PROMPT + caption_hint

        try:
            response = client.chat.completions.create(
                model=self.model,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "image_url",
                         "image_url": {"url": f"data:image/{mime};base64,{b64}"}},
                        {"type": "text", "text": prompt},
                    ],
                }],
                temperature=0,
            )
            token_usage.add_response(response)
            content = response.choices[0].message.content or "{}"
            content = content.strip()
            if content.startswith("```"):
                content = content.split("\n", 1)[1].rsplit("```", 1)[0]
            result = json.loads(content)
            if isinstance(result, dict):
                return result
        except Exception:
            pass
        return None
