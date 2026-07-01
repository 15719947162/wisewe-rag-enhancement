# Pipeline 文档

本目录聚焦当前真实链路：

- 离线入库链路：文档解析、清洗、切片、质量门控、向量化、关系写库
- 在线问答链路：检索、融合、图扩展、生成、评分与证据回溯
- Graph RAG 结构化检索链路：意图识别、实体召回、关系扩展、路径解释

性能优化过程、调参记录和技术演进已独立归档到 [../performance-optimizations/README.md](../performance-optimizations/README.md)。本目录只保留当前稳定链路和最终方案，避免把“链路应该如何工作”和“性能优化如何演进”混在一起。

## 文档清单

- [full-chain-technical-guide.md](./full-chain-technical-guide.md)：完整技术链路说明，按节点列输入、输出、关键代码、数据表、配置、指标和需求挂点
- [offline-ingestion-pipeline.md](./offline-ingestion-pipeline.md)：当前离线入库链路总览
- [three-layer-chunking-final-solution.md](./three-layer-chunking-final-solution.md)：三层切片最终技术方案，包含下载/资产准备边界、并发增强、key 池、热更新、指标和回退
- [parser-provider-poc.md](./parser-provider-poc.md)：MinerU 与阿里 Document Mind provider 显式切换和 A/B 对比记录
- [mineru-official-parser.md](./mineru-official-parser.md)：官方 MinerU 精准解析 provider 接入、配置和分片边界
- [document-mind-sharding.md](./document-mind-sharding.md)：Document Mind 大文件分片解析行为记录
- [hierarchical-async-enhancement-proposal.md](./hierarchical-async-enhancement-proposal.md)：Hierarchical 基础切片与 LLM 增强解耦方案存档；其中入库前有序并发增强已在 10-08 落地，后台渐进补强仍待后续显式模式
- [online-rag-pipeline.md](./online-rag-pipeline.md)：当前在线问答链路总览
- [online-retrieval.md](./online-retrieval.md)：在线召回术语与阶段边界
- [document-graph-preview.md](./document-graph-preview.md)：文档详情知识图谱预览，作为入库质量检查视图
- [../performance-optimizations/README.md](../performance-optimizations/README.md)：性能优化档案总入口，按解析 / 切片 / 向量化三条线归档
- [../rule/chunking-rules.md](../rule/chunking-rules.md)：切片策略总规则
- [../rule/hierarchical-chunking.md](../rule/hierarchical-chunking.md)：分层切片规则
- [../rule/hierarchical-chunking-extensions.md](../rule/hierarchical-chunking-extensions.md)：分层切片扩展说明
- [../rule/cleaner-rules.md](../rule/cleaner-rules.md)：清洗与质量过滤规则
- [../rule/enhanced-extraction.md](../rule/enhanced-extraction.md)：enhanced 抽取与结构化输出

## 推荐阅读顺序

1. 先看 [full-chain-technical-guide.md](./full-chain-technical-guide.md)，建立完整链路地图。
2. 再看离线入库、在线问答和在线召回三篇总览。
3. 需要改具体能力时，再看解析 provider、三层切片、图谱预览、清洗和 enhanced 抽取等细分文档。
4. 若要了解当前版本边界与下一步计划，再回到 `docs/iterations/` 和 `.planning/ROADMAP.md`。

## 当前口径

- 当前仓库不只有“向量 RAG”，还包含 Graph RAG 关系层、实体层和 `/api/rag/graph-query`。
- 当前入库链路已经由控制台和后端 API 驱动，不再只是早期脚本实验。
- 当前解析层默认仍走 `mineru`（302AI MinerU），并已支持 `PDF_PARSER_PROVIDER=mineru_official` 官方 MinerU 精准解析和 `PDF_PARSER_PROVIDER=ali_document_mind` Document Mind；三条云解析路径均为显式渠道，不做隐式回退。
- v6.0 已完成“可控入库与配置收口”的主链路，规则中心、完整图谱工作台与部分性能优化仍在后续 phase 内推进。
