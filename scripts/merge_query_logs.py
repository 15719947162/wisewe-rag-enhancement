#!/usr/bin/env python3
"""
合并 query_logs.py 文件，添加中文文档字符串
"""
import re
from pathlib import Path

# 中文文档映射
FUNCTION_DOCS = {
    'append_processing_cost_event': '''    """
    添加处理成本事件到数据库

    这个函数记录非模型处理的成本事件，如解析服务、存储服务等。
    记录失败不会影响正常业务流程。

    参数：
        record: ProcessingCostEventRecord 对象，包含成本事件的所有信息

    返回：
        bool: True 表示成功写入，False 表示写入失败

    使用示例：
        >>> record = ProcessingCostEventRecord(
        ...     event_type="parse_provider",
        ...     pipeline_domain="ingestion",
        ...     pipeline_stage="parse",
        ...     provider="mineru",
        ...     metric_value=10,
        ...     metric_unit="page"
        ... )
        >>> success = append_processing_cost_event(record)
    """
''',
    'refresh_processing_cost_estimates': '''    """
    刷新处理成本估算

    重新计算所有处理成本事件的预估成本。
    当定价规则更新后，可以调用此函数重新计算历史成本。

    工作原理：
        1. 查找所有缺失成本的事件记录
        2. 根据当前定价规则重新计算
        3. 更新数据库记录

    参数：
        start_at: 开始时间（可选）
        end_at: 结束时间（可选）

    返回：
        dict: 包含刷新结果

    使用示例：
        >>> result = refresh_processing_cost_estimates()
        >>> print(f"更新了 {result['updated']} 条记录")
    """
''',
    'fetch_processing_cost_tasks_for_identity': '''    """
    查询处理成本任务列表

    查询指定范围的处理成本任务列表，用于成本报告和审计。

    参数：
        limit: 返回数量限制，默认 20
        tenant_id: 租户 ID（可选）
        include_all_tenants: 是否包含所有租户
        visible_kb_ids: 可见的知识库 ID 列表
        task_id: 任务 ID（可选）
        kb_id: 知识库 ID（可选）
        document_id: 文档 ID（可选）
        pipeline_domain: 管道域（可选）
        pipeline_stage: 管道阶段（可选）
        event_type: 事件类型（可选）
        provider: 服务提供商（可选）
        model_name: 模型名称（可选）
        api_key_id: API Key ID（可选）
        app_id: 应用 ID（可选）
        start_at: 开始时间（可选）
        end_at: 结束时间（可选）

    返回：
        dict: 包含总体统计和任务列表
    """
''',
    'fetch_processing_cost_documents_for_identity': '''    """
    查询处理成本文档列表

    查询指定范围的文档处理成本列表，用于文档级成本追踪。

    参数：
        limit: 返回数量限制，默认 20
        tenant_id: 租户 ID（可选）
        include_all_tenants: 是否包含所有租户
        visible_kb_ids: 可见的知识库 ID 列表
        document_id: 文档 ID（可选）
        kb_id: 知识库 ID（可选）
        pipeline_domain: 管道域（可选）
        pipeline_stage: 管道阶段（可选）
        event_type: 事件类型（可选）
        provider: 服务提供商（可选）
        model_name: 模型名称（可选）
        api_key_id: API Key ID（可选）
        app_id: 应用 ID（可选）
        start_at: 开始时间（可选）
        end_at: 结束时间（可选）

    返回：
        dict: 包含总体统计和文档列表
    """
''',
    'fetch_processing_cost_task_detail_for_identity': '''    """
    查询处理成本任务详情

    查询指定任务的详细成本信息，包括各个阶段的成本分解。

    参数：
        task_id: 任务 ID
        tenant_id: 租户 ID（可选）
        include_all_tenants: 是否包含所有租户
        visible_kb_ids: 可见的知识库 ID 列表
        limit: 返回事件数量限制，默认 100

    返回：
        dict: 包含任务成本详情
    """
''',
    'fetch_processing_cost_document_detail_for_identity': '''    """
    查询处理成本文档详情

    查询指定文档的详细成本信息，包括各个阶段的成本分解。

    参数：
        document_id: 文档 ID
        tenant_id: 租户 ID（可选）
        include_all_tenants: 是否包含所有租户
        visible_kb_ids: 可见的知识库 ID 列表
        limit: 返回事件数量限制，默认 100

    返回：
        dict: 包含文档成本详情
    """
''',
    'fetch_project_cost_estimates_for_identity': '''    """
    查询项目成本估算

    查询解析服务和存储服务的成本估算汇总，用于项目级成本报告。

    参数：
        limit: 返回数量限制，默认 20
        tenant_id: 租户 ID（可选）
        include_all_tenants: 是否包含所有租户
        visible_kb_ids: 可见的知识库 ID 列表
        task_id: 任务 ID（可选）
        kb_id: 知识库 ID（可选）
        document_id: 文档 ID（可选）
        pipeline_domain: 管道域（可选）
        pipeline_stage: 管道阶段（可选）
        event_type: 事件类型（可选）
        provider: 服务提供商（可选）
        cost_source: 成本来源（可选）
        api_key_id: API Key ID（可选）
        app_id: 应用 ID（可选）
        start_at: 开始时间（可选）
        end_at: 结束时间（可选）

    返回：
        dict: 包含项目成本估算汇总
    """
''',
    '_insert_processing_cost_event': '''    """
    插入处理成本事件记录（内部函数）

    将处理成本事件记录插入到 kb_processing_cost_events 表。
    这个函数在数据库事务内部调用，不负责连接管理。

    参数：
        cur: 数据库游标
        record: ProcessingCostEventRecord 对象
    """
''',
    '_backfill_missing_processing_cost_events_from_llm_logs': '''    """
    从 LLM 调用日志回填缺失的成本事件（内部函数）

    为历史的 LLM 调用日志创建对应的成本事件记录。
    用于数据迁移或历史数据补全。

    参数：
        cur: 数据库游标
        start_at: 开始时间（可选）
        end_at: 结束时间（可选）

    返回：
        tuple[int, int]: (回填的记录数, 跳过的记录数)
    """
''',
    '_refresh_project_processing_cost_events': '''    """
    刷新项目处理成本事件（内部函数）

    重新计算解析服务和存储服务的成本估算。
    根据当前定价规则更新项目成本记录。

    参数：
        cur: 数据库游标
        start_at: 开始时间（可选）
        end_at: 结束时间（可选）

    返回：
        tuple[int, int]: (更新的记录数, 跳过的记录数)
    """
''',
}

CLASS_DOCS = {
    'ProcessingCostEventRecord': '''    """
    处理成本事件记录

    记录一次处理任务的成本信息，包括解析服务、存储服务、模型调用等。
    这个记录会被写入 kb_processing_cost_events 表。

    与 LlmCallLogRecord 的区别：
        - LlmCallLogRecord 只关注模型调用的 token 消耗
        - ProcessingCostEventRecord 记录更广泛的成本事件，包括非模型成本
        - 一次处理任务可能产生多条成本事件记录

    属性说明：
        event_type: 事件类型，如 'parse_provider'、'oss_upload'
        pipeline_domain: 管道域，如 'ingestion'、'online_rag'
        pipeline_stage: 管道阶段，如 'parse'、'embedding'
        feature_name: 功能名称
        task_id: 任务 ID（用于导入任务的成本追踪）
        request_id: 请求 ID（用于在线问答的成本追踪）
        usage_target_type: 使用目标类型，如 'ingestion_task'、'rag_request'
        document_id: 文档 ID（可选）
        kb_id: 知识库 ID（可选）
        identity: 用户身份信息
        api_key_id: API Key ID（可选）
        app_id: 应用 ID（可选）
        provider: 服务提供商，如 'mineru'、'oss'
        model_name: 模型名称（如果是模型调用）
        external_job_id: 外部任务 ID
        metric_value: 指标值（如页数、字节数）
        metric_unit: 指标单位，如 'page'、'byte'
        prompt_tokens: 提示词 token 数（如果是模型调用）
        completion_tokens: 生成 token 数（如果是模型调用）
        total_tokens: 总 token 数（如果是模型调用）
        duration_ms: 处理耗时（毫秒）
        status: 处理状态，'success' 或 'error'
        error_code: 错误码
        estimated_cost: 预估成本
        cost_currency: 成本货币单位，如 'CNY'、'USD'
        cost_source: 成本来源
        llm_call_log_id: 关联的 LLM 调用日志 ID
        usage_source: 使用量来源，'runtime' 或 'backfilled'
        collection_status: 收集状态，'recorded' 或 'backfilled'
        metadata: 额外的元数据
        occurred_at: 事件发生时间
    """
''',
    'TokenPricing': '''    """
    Token 定价信息

    记录 token 的定价信息，用于成本估算。
    支持按提示词、生成词、总 token 数分别定价。

    属性说明：
        source: 价格来源，如 'configured_model_rate'、'configured_rate'
        currency: 货币单位，如 'CNY'、'USD'
        prompt_per_1k: 提示词每千 token 价格
        completion_per_1k: 生成词每千 token 价格
        total_per_1k: 总 token 每千 token 价格
        provider: 服务提供商（可选）
        model_name: 模型名称（可选）
        event_type: 事件类型（可选）
        matched_rule: 匹配的定价规则标识
    """
''',
    'ProcessingCostEstimate': '''    """
    处理成本估算结果

    记录一次处理任务的成本估算结果，用于成本核算和报告。

    属性说明：
        estimated_cost: 预估成本（Decimal 类型，支持高精度计算）
        currency: 货币单位，如 'CNY'、'USD'
        source: 成本来源，如 'estimated_parse_rate'、'estimated_oss_rate'
        pricing: 定价详情（字典格式，包含费率、数量等）
    """
''',
}

def has_chinese_doc(text):
    """检查文本是否包含中文文档"""
    # 检查是否包含中文字符
    chinese_pattern = re.compile(r'[一-龥]+')
    return bool(chinese_pattern.search(text))

def add_docs_to_function(content, func_name, doc_string):
    """为函数添加文档字符串"""
    # 匹配函数定义
    pattern = rf'^(def {func_name}\([^)]*\):)\s*$'
    replacement = rf'\1\n{doc_string}'
    return re.sub(pattern, replacement, content, flags=re.MULTILINE)

def add_docs_to_class(content, class_name, doc_string):
    """为类添加文档字符串"""
    # 匹配类定义
    pattern = rf'^(@dataclass\([^)]*\)\s*\n)?(class {class_name}:)\s*$'
    match = re.search(pattern, content, flags=re.MULTILINE)

    if match:
        # 如果有装饰器，在类定义后添加文档
        if match.group(1):
            replacement = rf'{match.group(1)}{match.group(2)}\n{doc_string}'
        else:
            replacement = rf'{match.group(2)}\n{doc_string}'
        return content[:match.start()] + replacement + content[match.end():]
    return content

def process_file(source_file, target_file):
    """处理文件"""
    print(f"正在处理 {source_file}...")

    # 读取源文件
    with open(source_file, 'r', encoding='utf-8') as f:
        content = f.read()

    # 添加模块文档
    module_doc = '''"""
查询日志记录模块
================

这个模块负责记录和管理 RAG 系统中的各类日志，包括：

1. RAG 查询日志 - 记录用户的问答请求，包括问题、答案、相关性评分等
2. LLM 调用日志 - 记录所有大模型 API 调用，包括 token 消耗、延迟、模型信息等
3. 审计日志 - 记录系统关键操作，用于安全审计和合规追溯
4. 处理成本事件 - 记录各类处理任务的成本，包括解析、存储等

核心设计原则：
- 日志记录失败不能影响正常业务流程
- 敏感信息脱敏：查询内容存储摘要和哈希值
- 支持 token 消耗统计和成本估算
- 支持按小时聚合的使用量统计
- 支持多维度的成本核算

典型使用场景：
- 用户发起问答请求 → 记录 RAG 查询日志
- 调用 LLM 生成答案 → 记录 LLM 调用日志和成本事件
- 文档解析任务 → 记录解析成本事件
- OSS 存储/下载 → 记录存储成本事件
- 创建知识库、删除文档 → 记录审计日志
- 运营人员查看使用情况 → 查询 token 使用统计和成本报表

数据流向：
用户请求 → 业务逻辑 → 调用日志记录函数 → PostgreSQL 数据库
                           ↓
                    更新小时级聚合统计
                           ↓
                    更新成本事件表

安全考虑：
- 查询内容不完整存储，只存摘要和哈希
- API Key 等敏感字段自动脱敏
- 支持租户隔离和用户追溯
"""

'''

    # 如果文件开头不是模块文档，添加它
    if not content.startswith('"""'):
        content = module_doc + content

    # 为类添加文档
    for class_name, doc in CLASS_DOCS.items():
        content = add_docs_to_class(content, class_name, doc)
        print(f"  已为类 {class_name} 添加文档")

    # 为函数添加文档
    for func_name, doc in FUNCTION_DOCS.items():
        content = add_docs_to_function(content, func_name, doc)
        print(f"  已为函数 {func_name} 添加文档")

    # 写入目标文件
    with open(target_file, 'w', encoding='utf-8') as f:
        f.write(content)

    print(f"[OK] 已完成文件处理，输出到 {target_file}")

if __name__ == '__main__':
    source = Path('E:/all_project/AI/rag-enhancement/core/db/query_logs.py')
    target = Path('E:/study/wisewe-rag-enhancement/core/db/query_logs.py')

    process_file(source, target)
