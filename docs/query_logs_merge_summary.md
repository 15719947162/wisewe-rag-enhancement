# query_logs.py 合并总结

## 合并完成时间
2026-07-14

## 合并概述
成功将源项目（`E:\all_project\AI\rag-enhancement`）的成本核算相关代码合并到目标项目（`E:\study\wisewe-rag-enhancement`）。

## 文件变化
- **原文件行数**: 2236 行
- **合并后行数**: 4296 行
- **新增代码行数**: 2060 行

## 新增内容清单

### 1. 数据类 (DataClass)

#### ProcessingCostEventRecord (第 103-202 行)
- **功能**: 处理成本事件记录
- **用途**: 记录一次处理任务的成本信息，包括解析服务、存储服务、模型调用等
- **写入表**: `kb_processing_cost_events`
- **关键字段**:
  - event_type: 事件类型（parse_provider, oss_upload 等）
  - metric_value/metric_unit: 成本计算指标
  - estimated_cost: 预估成本
  - cost_source: 成本来源

#### TokenPricing (第 204-240 行)
- **功能**: Token 定价信息
- **用途**: 记录 token 的定价信息，用于成本估算
- **关键字段**:
  - prompt_per_1k: 提示词每千 token 价格
  - completion_per_1k: 生成词每千 token 价格
  - total_per_1k: 总 token 每千 token 价格

#### ProcessingCostEstimate (第 242-262 行)
- **功能**: 处理成本估算结果
- **用途**: 记录一次处理任务的成本估算结果
- **关键字段**:
  - estimated_cost: 预估成本
  - pricing: 定价详情

### 2. 核心公共函数

#### append_processing_cost_event() (第 494-519 行)
- **功能**: 添加处理成本事件到数据库
- **用途**: 记录非模型处理的成本事件
- **参数**: ProcessingCostEventRecord 对象
- **返回**: bool（成功/失败）

#### refresh_processing_cost_estimates() (第 676-798 行)
- **功能**: 刷新处理成本估算
- **用途**: 重新计算所有处理成本事件的预估成本
- **参数**: start_at, end_at（可选时间范围）
- **返回**: dict（包含更新统计）

#### fetch_processing_cost_tasks_for_identity() (第 1680-1855 行)
- **功能**: 查询处理成本任务列表
- **用途**: 获取成本报告和审计数据
- **参数**: 多种筛选条件
- **返回**: dict（包含总体统计和任务列表）

#### fetch_processing_cost_documents_for_identity() (第 1858-1957 行)
- **功能**: 查询处理成本文档列表
- **用途**: 文档级成本追踪
- **参数**: 多种筛选条件
- **返回**: dict（包含总体统计和文档列表）

#### fetch_processing_cost_task_detail_for_identity() (第 1960-1975 行)
- **功能**: 查询处理成本任务详情
- **用途**: 查看任务各阶段的成本分解
- **参数**: task_id, tenant_id 等
- **返回**: dict（包含任务成本详情）

#### fetch_processing_cost_document_detail_for_identity() (第 1978-1993 行)
- **功能**: 查询处理成本文档详情
- **用途**: 查看文档各阶段的成本分解
- **参数**: document_id, tenant_id 等
- **返回**: dict（包含文档成本详情）

#### fetch_project_cost_estimates_for_identity() (第 1996-2210 行)
- **功能**: 查询项目成本估算
- **用途**: 获取解析服务和存储服务的成本汇总
- **参数**: 多种筛选条件
- **返回**: dict（包含项目成本估算汇总）

### 3. 内部辅助函数

#### _insert_processing_cost_event() (第 537-632 行)
- 插入处理成本事件记录（事务内部调用）

#### _backfill_missing_processing_cost_events_from_llm_logs() (第 933-1102 行)
- 从 LLM 调用日志回填缺失的成本事件

#### _refresh_project_processing_cost_events() (第 1105-1227 行)
- 刷新项目处理成本事件

#### _processing_cost_filters() (第 2487-2558 行)
- 构建处理成本查询的过滤条件

#### _project_cost_event_predicate() (第 2561-2569 行)
- 构建项目成本事件的查询谓词

#### _estimate_processing_cost() (第 2828-2838 行)
- 估算处理成本

#### _estimate_parse_processing_cost() (第 2841-2898 行)
- 估算解析处理成本

#### _estimate_oss_processing_cost() (第 2901-2962 行)
- 估算 OSS 存储成本

#### _resolve_token_pricing() (第 3045-3116 行)
- 解析 token 定价

#### _estimate_token_cost() (第 3140-3169 行)
- 估算 token 成本

### 4. 更新的现有类

#### LlmCallLogRecord
- **新增字段**:
  - app_id: 应用 ID
  - document_id: 文档 ID
  - usage_target_type: 使用目标类型
  - event_type: 事件类型
  - usage_source: 使用量来源

### 5. 其他新增函数

#### repair_usage_document_id()
- 修复使用日志的文档 ID

#### fetch_app_usage_report_for_identity()
- 查询应用使用报告

## 中文文档字符串添加情况

### 已添加文档的类
1. ✅ ProcessingCostEventRecord - 完整的中文文档字符串
2. ✅ TokenPricing - 完整的中文文档字符串
3. ✅ ProcessingCostEstimate - 完整的中文文档字符串

### 已添加文档的函数
1. ✅ append_processing_cost_event - 完整的中文文档字符串和使用示例
2. ✅ refresh_processing_cost_estimates - 完整的中文文档字符串和使用示例
3. ✅ fetch_processing_cost_tasks_for_identity - 完整的参数说明
4. ✅ fetch_processing_cost_documents_for_identity - 完整的参数说明
5. ✅ fetch_processing_cost_task_detail_for_identity - 完整的参数说明
6. ✅ fetch_processing_cost_document_detail_for_identity - 完整的参数说明
7. ✅ fetch_project_cost_estimates_for_identity - 完整的参数说明
8. ✅ _insert_processing_cost_event - 内部函数文档
9. ✅ _backfill_missing_processing_cost_events_from_llm_logs - 内部函数文档
10. ✅ _refresh_project_processing_cost_events - 内部函数文档
11. ✅ _processing_cost_filters - 内部函数文档
12. ✅ _project_cost_event_predicate - 内部函数文档
13. ✅ _safe_slug - 内部函数文档
14. ✅ _request_as_task_id - 内部函数文档
15. ✅ _usage_target_type - 内部函数文档
16. ✅ _llm_event_type - 内部函数文档
17. ✅ _estimate_processing_cost - 内部函数文档
18. ✅ _estimate_parse_processing_cost - 内部函数文档
19. ✅ _estimate_oss_processing_cost - 内部函数文档
20. ✅ _resolve_token_pricing - 内部函数文档
21. ✅ _estimate_token_cost - 内部函数文档
22. ✅ repair_usage_document_id - 完整的文档说明
23. ✅ fetch_app_usage_report_for_identity - 完整的参数说明

### 模块级文档
✅ 已添加完整的模块级中文文档字符串，说明模块的职责、设计原则、使用场景和数据流向

## 验证结果

### 函数统计
- fetch_processing_cost_* 函数: 4 个
- append_processing_cost_event 函数: 1 个
- refresh_processing_cost_estimates 函数: 1 个

### 功能验证
- ✅ 所有新增的类都有完整的中文文档字符串
- ✅ 所有新增的公共函数都有完整的中文文档字符串
- ✅ 所有新增的内部函数都有中文文档字符串
- ✅ 保留了目标项目的中文注释风格
- ✅ 代码结构和逻辑与源项目一致
- ✅ 模块级文档已更新，包含成本核算说明

## 合并策略
1. 使用源项目的代码作为基础（包含所有功能实现）
2. 为所有新增的类和函数添加中文文档字符串
3. 保持代码结构和逻辑与源项目一致
4. 为内部辅助函数添加中文注释
5. 更新模块级文档，说明新增的成本核算功能

## 注意事项
1. 源项目的 `core.runtime_settings` 模块已包含在导入中
2. LlmCallLogRecord 类新增了多个字段，需要在调用时注意
3. 新增的成本核算功能需要数据库中存在 `kb_processing_cost_events` 表
4. 定价规则通过环境变量或配置文件设置

## 后续建议
1. 确保数据库 schema 包含 `kb_processing_cost_events` 表
2. 配置成本定价规则（KB_PROCESSING_COST_RATES_JSON, KB_TOKEN_MODEL_RATES_JSON）
3. 在使用 LlmCallLogRecord 时注意新增的字段
4. 测试成本核算相关的 API 接口
