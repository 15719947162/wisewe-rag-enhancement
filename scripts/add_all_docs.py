#!/usr/bin/env python3
"""
为 query_logs.py 的所有函数添加中文文档
"""
import re
from pathlib import Path

# 所有需要添加文档的函数列表
FUNCTION_DOCS = {
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
    """''',
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
    """''',
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
    """''',
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
    """''',
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
    """''',
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
    """''',
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
    """''',
    '_fetch_processing_cost_detail_for_identity': '''    """
    查询处理成本详情（内部函数）

    查询任务或文档的详细成本信息。

    参数：
        detail_type: 详情类型，'task' 或 'document'
        detail_id: 任务 ID 或文档 ID
        tenant_id: 租户 ID（可选）
        include_all_tenants: 是否包含所有租户
        visible_kb_ids: 可见的知识库 ID 列表
        limit: 返回事件数量限制

    返回：
        dict: 包含成本详情
    """''',
    '_processing_cost_filters': '''    """
    构建处理成本查询的过滤条件（内部函数）

    根据提供的参数构建 WHERE 子句和参数列表。

    参数：
        table_alias: 表别名（可选）
        tenant_id: 租户 ID（可选）
        include_all_tenants: 是否包含所有租户
        visible_kb_ids: 可见的知识库 ID 列表
        其他筛选参数...

    返回：
        tuple[list[str], list[Any]]: (WHERE 条件列表, 参数列表)
    """''',
    '_project_cost_event_predicate': '''    """
    构建项目成本事件的查询谓词（内部函数）

    生成用于筛选解析服务和存储服务成本事件的 SQL 条件。

    参数：
        table_alias: 表别名（可选）

    返回：
        str: SQL WHERE 条件
    """''',
    '_safe_slug': '''    """
    生成安全的短字符串（内部函数）

    将字符串截断为指定长度，如果为空则使用默认值。

    参数：
        value: 原始值
        fallback: 默认值
        max_len: 最大长度

    返回：
        str: 安全的短字符串
    """''',
    '_request_as_task_id': '''    """
    将请求 ID 转换为任务 ID（内部函数）

    对于导入任务，请求 ID 就是任务 ID。

    参数：
        request_id: 请求 ID
        usage_target_type: 使用目标类型

    返回：
        str | None: 任务 ID 或 None
    """''',
    '_usage_target_type': '''    """
    确定使用目标类型（内部函数）

    根据管道域和记录信息确定使用目标类型。

    参数：
        record: LLM 调用日志记录

    返回：
        str: 使用目标类型
    """''',
    '_llm_event_type': '''    """
    确定 LLM 事件类型（内部函数）

    根据管道阶段确定事件类型。

    参数：
        record: LLM 调用日志记录

    返回：
        str: 事件类型
    """''',
    '_estimate_processing_cost': '''    """
    估算处理成本（内部函数）

    根据事件类型和指标值估算处理成本。

    参数：
        record: 处理成本事件记录

    返回：
        ProcessingCostEstimate: 成本估算结果
    """''',
    '_estimate_parse_processing_cost': '''    """
    估算解析处理成本（内部函数）

    根据解析规则估算解析服务的成本。

    参数：
        record: 处理成本事件记录

    返回：
        ProcessingCostEstimate: 成本估算结果
    """''',
    '_estimate_oss_processing_cost': '''    """
    估算 OSS 存储成本（内部函数）

    根据存储规则估算 OSS 服务的成本。

    参数：
        record: 处理成本事件记录

    返回：
        ProcessingCostEstimate: 成本估算结果
    """''',
    '_resolve_token_pricing': '''    """
    解析 token 定价（内部函数）

    从配置中解析 token 的定价信息。

    参数：
        record: LLM 调用日志记录
        event_type: 事件类型（可选）

    返回：
        TokenPricing: 定价信息
    """''',
    '_estimate_token_cost': '''    """
    估算 token 成本（内部函数）

    根据 token 数量和定价信息估算成本。

    参数：
        prompt_tokens: 提示词 token 数
        completion_tokens: 生成词 token 数
        total_tokens: 总 token 数
        pricing: 定价信息（可选）

    返回：
        Decimal | None: 成本（如果无法估算则返回 None）
    """''',
    'repair_usage_document_id': '''    """
    修复使用日志的文档 ID

    为历史日志补充文档 ID 信息。

    参数：
        request_id: 请求 ID
        document_id: 文档 ID
        kb_id: 知识库 ID（可选）

    返回：
        dict: 包含修复结果
    """''',
    'fetch_app_usage_report_for_identity': '''    """
    查询应用使用报告

    查询指定范围的应用使用统计，包括 token 消耗、错误率等。

    参数：
        limit: 返回数量限制，默认 20
        tenant_id: 租户 ID（可选）
        include_all_tenants: 是否包含所有租户
        app_id: 应用 ID（可选）
        api_key_id: API Key ID（可选）
        kb_id: 知识库 ID（可选）
        pipeline_domain: 管道域（可选）
        start_at: 开始时间（可选）
        end_at: 结束时间（可选）

    返回：
        dict: 包含应用使用统计
    """''',
}

def add_doc_to_function(content, func_name, doc_string):
    """为函数添加文档字符串"""
    # 匹配函数定义行，可能跨多行
    pattern = rf'(def {func_name}\([^)]*\)\s*(?:-> [^:]+)?\s*:)'

    def replacer(match):
        # 检查下一行是否已经有文档字符串
        after_match = content[match.end():]
        next_line = after_match.split('\n')[1] if '\n' in after_match else ''

        # 如果下一行已经是文档字符串开头，跳过
        if next_line.strip().startswith('"""') or next_line.strip().startswith("'''"):
            return match.group(0)

        # 添加文档字符串
        return match.group(0) + '\n' + doc_string

    return re.sub(pattern, replacer, content)

def process_file(file_path):
    """处理文件"""
    print(f"正在处理 {file_path}...")

    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()

    for func_name, doc in FUNCTION_DOCS.items():
        old_content = content
        content = add_doc_to_function(content, func_name, doc)
        if content != old_content:
            print(f"  [OK] 已为函数 {func_name} 添加文档")

    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(content)

    print(f"[完成] 文件处理完成")

if __name__ == '__main__':
    file_path = Path('E:/study/wisewe-rag-enhancement/core/db/query_logs.py')
    process_file(file_path)
