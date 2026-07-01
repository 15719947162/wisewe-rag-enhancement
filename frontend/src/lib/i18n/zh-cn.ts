import type {
  BlockType,
  ChunkLayer,
  RetrievalChannel,
  TaskState,
} from "@/lib/contracts/types";

const taskStateLabels: Record<TaskState, string> = {
  pending: "待处理",
  running: "进行中",
  awaiting_confirmation: "待确认",
  success: "成功",
  degraded: "降级",
  failed: "失败",
  empty: "空",
};

const chunkLayerLabels: Record<ChunkLayer | "candidate", string> = {
  parent: "父块",
  child: "子块",
  enhanced: "增强块",
  candidate: "候选",
};

const retrievalChannelLabels: Record<RetrievalChannel, string> = {
  dense: "稠密",
  sparse: "稀疏",
  structured: "结构化",
  rrf: "RRF",
  related: "关联",
};

const queueLaneLabels = {
  pending: "待处理",
  failed: "失败",
  "low-score": "低分",
  recent: "最近",
} as const;

const blockTypeLabels: Record<BlockType, string> = {
  text: "正文",
  table: "表格",
  image: "ͼƬ",
  title: "标题",
};

const parseMethodLabels = {
  auto: "自动",
  txt: "文本优先",
} as const;

const strategyLabels = {
  paragraph: "段落切片",
  fixed_length: "固定长度",
  semantic: "语义切片",
  separator: "分隔符切片",
  llm: "LLM 切片",
  hierarchical: "分层切片",
  dense: "稠密",
  sparse: "稀疏",
  structured: "结构化",
  rrf: "RRF",
  related: "关联",
  "hash-skip": "哈希去重跳过",
} as const;

const areaLabels = {
  "offline-ingestion": "离线入库",
  evaluation: "评测",
  api: "API",
} as const;

export function getTaskStateLabel(state: TaskState): string {
  return taskStateLabels[state] ?? state;
}

export function getChunkLayerLabel(layer: ChunkLayer | "candidate"): string {
  return chunkLayerLabels[layer] ?? layer;
}

export function getRetrievalChannelLabel(channel: RetrievalChannel): string {
  return retrievalChannelLabels[channel] ?? channel;
}

export function getQueueLaneLabel(lane: string): string {
  return queueLaneLabels[lane as keyof typeof queueLaneLabels] ?? lane;
}

export function getBlockTypeLabel(type: BlockType): string {
  return blockTypeLabels[type] ?? type;
}

export function getParseMethodLabel(method: string): string {
  return parseMethodLabels[method as keyof typeof parseMethodLabels] ?? method;
}

export function getStrategyLabel(strategy: string): string {
  return strategyLabels[strategy as keyof typeof strategyLabels] ?? strategy;
}

export function getAreaLabel(area: string): string {
  return areaLabels[area as keyof typeof areaLabels] ?? area;
}
