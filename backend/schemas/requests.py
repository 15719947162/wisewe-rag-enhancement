"""
请求模型定义模块

本模块定义了后端 API 的所有请求体模型，使用 Pydantic 进行数据验证和序列化。

主要功能：
1. 自动验证请求数据的格式和类型
2. 提供清晰的字段约束和默认值
3. 生成 OpenAPI 文档（FastAPI 自动集成）
4. 确保请求参数的合法性

验证机制：
- 类型检查：确保字段类型正确（str、int、float、bool 等）
- 值范围约束：通过 Field(ge=, le=) 设置最小/最大值
- 长度约束：通过 Field(min_length=, max_length=) 限制字符串长度
- 默认值：提供合理的默认参数值

请求示例：
    # 查询请求
    {
        "query": "什么是机器学习？",
        "kb_id": "kb_001",
        "top_k": 5,
        "min_score": 0.5,
        "use_llm_check": true,
        "use_llm_score": false
    }

    # 创建知识库请求
    {
        "name": "产品文档库",
        "description": "存放产品相关的技术文档",
        "strategy": "hierarchical"
    }
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class QueryRequest(BaseModel):
    """
    知识库查询请求模型

    用于在指定知识库中检索相关文档片段，支持向量相似度搜索和 LLM 辅助筛选。

    字段说明：
        query: 用户查询文本，必填字段
        kb_id: 知识库 ID，默认为 "default"
        top_k: 返回结果数量，范围 [1, 20]，默认 8
        min_score: 最小相似度阈值，范围 [0.0, 1.0]，默认 0.3
        use_llm_check: 是否使用 LLM 二次筛选结果，默认 False
        use_llm_score: 是否使用 LLM 对结果打分，默认 False

    请求示例：
        {
            "query": "如何配置环境变量？",
            "kb_id": "tech_docs",
            "top_k": 10,
            "min_score": 0.4
        }
    """

    query: str
    kb_id: str = "default"
    top_k: int = Field(default=8, ge=1, le=20)  # ge=greater or equal, le=less or equal
    min_score: float = Field(default=0.3, ge=0.0, le=1.0)
    use_llm_check: bool = False
    use_llm_score: bool = False


class GraphQueryRequest(BaseModel):
    """
    图谱查询请求模型

    用于在知识图谱中进行查询，支持结构化检索和推理路径解释。
    相比普通查询，增加了图谱特有的参数选项。

    字段说明：
        query: 用户查询文本，必填字段
        kb_id: 知识库 ID，默认为 "default"
        top_k: 返回结果数量，范围 [1, 20]，默认 5
        min_score: 最小相似度阈值，范围 [0.0, 1.0]，默认 0.3
        explain: 是否返回推理路径解释，默认 False
        intent: 用户意图标签，可选字段，用于优化检索

    请求示例：
        {
            "query": "谁创建了 TensorFlow？",
            "kb_id": "tech_docs",
            "top_k": 5,
            "min_score": 0.5,
            "explain": true,
            "intent": "entity_query"
        }
    """

    query: str
    kb_id: str = "default"
    top_k: int = Field(default=5, ge=1, le=20)
    min_score: float = Field(default=0.3, ge=0.0, le=1.0)
    explain: bool = False
    intent: str | None = None


class ParsePreviewRequest(BaseModel):
    """
    解析预览请求模型

    用于预览 PDF 解析结果，无需完整处理流程。
    主要用于测试和验证解析器配置。

    字段说明：
        pdf_path: PDF 文件路径，可选字段
                  如果未提供，可能使用默认测试文件

    请求示例：
        {
            "pdf_path": "data/input/sample.pdf"
        }
    """

    pdf_path: str | None = None


# 任务状态枚举类型
# 用于标识异步任务的处理状态
TaskState = Literal["pending", "running", "success", "degraded", "failed", "empty"]
# pending: 任务已创建，等待处理
# running: 任务正在执行中
# success: 任务成功完成
# degraded: 任务部分成功（部分结果可用）
# failed: 任务执行失败
# empty: 无结果（如输入为空）


class KnowledgeBaseCreateRequest(BaseModel):
    """
    创建知识库请求模型

    用于创建新的知识库实例，配置知识库名称、描述和切片策略。

    字段说明：
        name: 知识库名称，必填，长度 [1, 100] 字符
        description: 知识库描述，可选，最大 500 字符，默认空字符串
        strategy: 切片策略名称，默认 "hierarchical"
                  可选值：fixed_length, paragraph, semantic, separator, llm, hierarchical

    请求示例：
        {
            "name": "产品技术文档库",
            "description": "包含所有产品的技术文档和 API 说明",
            "strategy": "hierarchical"
        }
    """

    name: str = Field(..., min_length=1, max_length=100)  # ... 表示必填字段
    description: str = Field(default="", max_length=500)
    strategy: str = Field(default="hierarchical")



class KnowledgeBaseUpdateRequest(BaseModel):
    """
    更新知识库请求模型

    用于更新现有知识库的元数据和配置。
    字段约束与创建请求一致。

    字段说明：
        name: 新的知识库名称，必填，长度 [1, 100] 字符
        description: 新的知识库描述，可选，最大 500 字符，默认空字符串
        strategy: 新的切片策略，默认 "hierarchical"

    请求示例：
        {
            "name": "更新后的文档库名称",
            "description": "更新后的描述信息",
            "strategy": "semantic"
        }
    """

    name: str = Field(..., min_length=1, max_length=100)
    description: str = Field(default="", max_length=500)
    strategy: str = Field(default="hierarchical")


class KnowledgeBaseTransferOwnerRequest(BaseModel):
    """
    知识库所有权转移请求模型

    用于将知识库的所有权转移给其他用户。
    需要提供新所有者的用户 ID。

    字段说明：
        ownerUserId: 新所有者的用户 ID，必填，长度 [1, 64] 字符

    请求示例：
        {
            "ownerUserId": "user_12345"
        }
    """

    ownerUserId: str = Field(..., min_length=1, max_length=64)


class ChunkDraftUpdateRequest(BaseModel):
    """
    切片草稿更新请求模型

    用于更新切片草稿的内容。
    主要在人工审核/编辑切片时使用。

    字段说明：
        content: 新的切片内容，必填，至少 1 个字符

    请求示例：
        {
            "content": "这是修改后的切片内容，包含了更完整的信息。"
        }
    """

    content: str = Field(..., min_length=1)


class ChunkDraftMergeRequest(BaseModel):
    """
    切片草稿合并请求模型

    用于将多个切片草稿合并为一个新切片。
    合并操作会创建新的切片，保留原始切片内容。

    字段说明：
        task_id: 关联的任务 ID，必填字段
        draft_ids: 要合并的草稿 ID 列表，至少需要 2 个草稿

    请求示例：
        {
            "task_id": "task_001",
            "draft_ids": ["draft_001", "draft_002", "draft_003"]
        }
    """

    task_id: str
    draft_ids: list[str] = Field(default_factory=list, min_length=2)
