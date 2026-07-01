export type TaskState = "pending" | "running" | "awaiting_confirmation" | "success" | "degraded" | "failed" | "empty";

export type BlockType = "text" | "table" | "image" | "title";
export type ChunkLayer = "parent" | "child" | "enhanced";

export type PipelineStageKey =
  | "upload"
  | "parse"
  | "clean"
  | "chunk"
  | "quality"
  | "embedding"
  | "export"
  | "retrieval"
  | "rerank"
  | "generate"
  | "score";

export type RetrievalChannel = "dense" | "sparse" | "structured" | "rrf" | "related";

export type KnowledgeBase = {
  id: string;
  name: string;
  description: string;
  strategy: string;
  createdAt: string;
  docCount: number;
  chunkCount: number;
  lastUpdated: string;
  duplicatePolicy: string;
};

export type IntegrationIdentity = {
  tenantId: string;
  userId: string;
  username: string;
  displayName: string;
  tenantName: string;
  roleCodes: string[];
  roleNames?: string[];
  ragRole?: string;
  isTenantAdmin: boolean;
  source: "identity_snapshot" | string;
  syncedAt?: string;
};

export type IdentitySnapshotUsersPayload = {
  mode: "temporary_sso_deferred" | string;
  users: IntegrationIdentity[];
  count: number;
};

export type AuthSessionPayload = {
  mode: string;
  identity: IntegrationIdentity;
};

export type AiBaseSsoConfig = {
  configured: boolean;
  mode: string;
  legacyHeaderFallback: boolean;
};

export type DocumentRecord = {
  id: string;
  kbId: string;
  filename: string;
  fileHash: string;
  chunkCount: number;
  createdAt: string;
  updatedAt: string;
  status: TaskState;
  sourceStorage?: "local" | "oss" | "unknown" | string;
  sourceAvailable?: boolean;
  parserProvider?: string;
  strategy?: string;
  isHierarchical?: boolean;
  hierarchicalLayers?: string[];
};

export type ContentBlockRecord = {
  id: string;
  type: BlockType;
  text: string;
  page: number;
  level?: number | null;
  sourceFile: string;
  tableHtml?: string | null;
  imagePath?: string | null;
};

export type ChunkRecord = {
  id: string;
  documentId: string;
  kbId: string;
  source: string;
  page: number;
  chunkIndex: number;
  title?: string;
  content: string;
  strategy: string;
  layer: ChunkLayer;
  parentId?: string | null;
  relatedIds: string[];
  charCount: number;
  isTableChunk: boolean;
  isImageChunk: boolean;
  imagePath?: string | null;
  qualityScore?: number;
  rerankScore?: number;
  denseScore?: number;
};

export type DocumentDetail = {
  document: DocumentRecord;
  chunks: Array<
    ChunkRecord & {
      hasEmbedding?: boolean;
      createdAt?: string;
      relations?: Array<Record<string, unknown>>;
      triples?: Array<Record<string, unknown>>;
    }
  >;
};

export type DocumentGraphNode = {
  id: string;
  type: "chunk" | "entity";
  label: string;
  chunkType?: "text" | "image" | "table";
  entityType?: string;
  meta: Record<string, unknown>;
};

export type DocumentGraphEdge = {
  id: string;
  source: string;
  target: string;
  type: string;
  label: string;
  weight?: number;
  meta?: Record<string, unknown>;
};

export type DocumentGraphPayload = {
  documentId: string;
  kbId?: string;
  scope?: "document" | "knowledge_base";
  nodes: DocumentGraphNode[];
  edges: DocumentGraphEdge[];
  stats: {
    nodeCount: number;
    edgeCount: number;
    chunkCount: number;
    entityCount: number;
    tripleCount: number;
    truncated: boolean;
    documentCount?: number;
    totalChunkCount?: number;
    selectedChunkCount?: number;
  };
};

export type ChunkDraftRecord = {
  id: string;
  taskId: string;
  kbId: string;
  chunkId?: string | null;
  chunkIndex: number;
  content: string;
  source: string;
  page: number;
  strategy: string;
  layer: ChunkLayer;
  title?: string;
  parentId?: string | null;
  relatedIds: string[];
  isTableChunk: boolean;
  isImageChunk: boolean;
  userEdited: boolean;
  isDeleted: boolean;
  createdAt: string;
  expiresAt: string;
};

export type IngestionStage = {
  key: PipelineStageKey;
  label: string;
  status: TaskState;
  progress: number;
  inputCount: number;
  outputCount: number;
  latencyMs: number;
  reason: string;
  metrics?: Record<string, number>;
};

export type IngestionTask = {
  id: string;
  kbId: string;
  documentName: string;
  status: TaskState;
  awaitingConfirmation?: boolean;
  strategy: string;
  createdAt: string;
  updatedAt: string;
  parseMethod: string;
  chunkCount: number;
  stages: IngestionStage[];
  blocks: ContentBlockRecord[];
  chunks: ChunkRecord[];
  removedReasons: Array<{ label: string; count: number }>;
  qualityBreakdown: Array<{ label: string; count: number }>;
  chunkTimings?: Record<string, number>;
};

export type IngestionTaskLogRow = {
  key: PipelineStageKey | string;
  label: string;
  status: TaskState;
  progress: number;
  latencyMs: number;
  inputCount: number;
  outputCount: number;
  reason: string;
  metrics?: Record<string, number | string>;
};

export type ConsoleIngestionTask = {
  id: string;
  kbId: string;
  kbName: string;
  documentName: string;
  status: TaskState;
  strategy: string;
  createdAt: string;
  updatedAt: string;
  actorId: string;
  actorName: string;
  parseMethod: string;
  chunkCount: number;
  totalLatencyMs: number;
  currentStage: string;
  error?: string;
  stages: IngestionTaskLogRow[];
  chunkTimings?: Record<string, number | string>;
};

export type ConsoleIngestionTasksPayload = {
  items: ConsoleIngestionTask[];
  total: number;
  page: number;
  pageSize: number;
  pageCount: number;
};

export type LatestIngestionLogPayload = {
  task: ConsoleIngestionTask | null;
  lines: string[];
  lineCount: number;
  truncated: boolean;
  logPath?: string;
};

export type RetrievalCandidate = {
  id: string;
  source: string;
  documentName?: string;
  documentId?: string;
  page: number;
  chunkIndex?: number;
  location?: string;
  layer: ChunkLayer | "candidate";
  score: number;
  denseScore: number;
  rerankScore?: number;
  channel: RetrievalChannel;
  strategy?: string;
  title?: string;
  content: string;
  contextWindow?: string;
  isImageChunk?: boolean;
  imagePath?: string | null;
  imageUrl?: string | null;
  relatedIds: string[];
  bestChildId?: string;
  matchedBy?: string[];
  matchedEnhancedId?: string;
};

export type Citation = {
  index: number;
  source: string;
  documentName?: string;
  documentId?: string;
  page: number;
  chunkIndex?: number | null;
  location?: string;
  snippet: string;
  chunkId: string;
};

export type ScoreSummary = {
  relevanceScore: number;
  faithfulnessScore: number;
  llmScore?: number | null;
  cannotAnswer: boolean;
  interpretation: string;
};

export type AnswerResult = {
  query: string;
  kbId: string;
  answer: string;
  cannotAnswer: boolean;
  citations: Citation[];
  scores: ScoreSummary;
  recallChannels: Array<{ channel: RetrievalChannel; count: number }>;
  candidates: RetrievalCandidate[];
  contextWindow: string[];
  trace: Array<{
    key: PipelineStageKey;
    label: string;
    status: TaskState;
    detail: string;
  }>;
};

export type OverviewMetric = {
  label: string;
  value: string;
  helper: string;
  delta: string;
  tone: "neutral" | "good" | "warning";
};

export type AlertItem = {
  id: string;
  title: string;
  description: string;
  severity: Exclude<TaskState, "empty">;
  area: string;
};

export type QueueItem = {
  id: string;
  lane: "pending" | "failed" | "low-score" | "recent";
  title: string;
  subtitle: string;
  status: TaskState;
  linkedHref: string;
  updatedAt: string;
};

export type EvaluationRecord = {
  id: string;
  query: string;
  answer: string;
  relevanceScore: number;
  faithfulnessScore: number;
  llmScore?: number | null;
  cannotAnswer: boolean;
  failureReason?: string;
};

export type QueryLogRecord = {
  requestId: string;
  pipelineDomain: string;
  pipelineStage: string;
  tenantId?: string | null;
  actorId?: string | null;
  kbId: string;
  apiKeyId?: string | null;
  queryHash: string;
  querySummary: string;
  answerSummary: string;
  cannotAnswer: boolean;
  relevanceScore?: number | null;
  faithfulnessScore?: number | null;
  promptTokens: number;
  completionTokens: number;
  totalTokens: number;
  latencyMs: number;
  status: string;
  errorCode?: string | null;
  createdAt: string;
};

export type AuditLogRecord = {
  id: number;
  requestId?: string | null;
  tenantId?: string | null;
  actorId?: string | null;
  actorName?: string | null;
  actorSource?: string | null;
  action: string;
  resourceType: string;
  resourceId?: string | null;
  kbId?: string | null;
  apiKeyId?: string | null;
  outcome: string;
  riskLevel: "low" | "medium" | "high" | string;
  summary: string;
  metadata: Record<string, unknown>;
  createdAt: string;
};

export type IdentitySyncLogRecord = {
  id: number;
  syncMode: string;
  sourceHost: string;
  sourceSchema: string;
  requestedLimit: number;
  tenantsCount: number;
  usersCount: number;
  rolesCount: number;
  userRolesCount: number;
  deletedCount: number;
  lastSyncAt: string;
  maxUpdatedAt: string;
  snapshotVersion: string;
  hasMore: boolean;
  status: string;
  errorMessage: string;
  startedAt: string;
  finishedAt: string;
};

export type TokenUsageBucket = {
  requestCount: number;
  promptTokens: number;
  completionTokens: number;
  totalTokens: number;
  avgLatencyMs: number;
};

export type TokenUsagePipelineBucket = TokenUsageBucket & {
  pipelineDomain: string;
};

export type TokenUsageKnowledgeBaseBucket = TokenUsageBucket & {
  kbId: string;
};

export type TokenUsageApiKeyBucket = TokenUsageBucket & {
  apiKeyId: string;
};

export type TokenUsageDetailStatus = "recorded" | "not_recorded" | "query_log_fallback" | string;

export type LlmCallUsageRecord = {
  id: number;
  requestId?: string | null;
  pipelineDomain: string;
  pipelineStage: string;
  featureName: string;
  provider: string;
  modelName: string;
  modelVersion?: string | null;
  promptTokens: number;
  completionTokens: number;
  totalTokens: number;
  latencyMs: number;
  status: string;
  errorCode?: string | null;
  kbId?: string | null;
  apiKeyId?: string | null;
  createdAt: string;
};

export type TokenUsageStageBucket = TokenUsageBucket & {
  pipelineStage: string;
  stageLabel: string;
  featureName: string;
  detailStatus: TokenUsageDetailStatus;
  lastCalledAt?: string;
  calls: LlmCallUsageRecord[];
};

export type TokenUsageHourlyBucket = TokenUsageBucket & {
  hourBucket: string;
  pipelineDomain: string;
  pipelineStage: string;
  featureName: string;
  latencyMsSum: number;
  errorCount: number;
  estimatedCost: number;
};

export type TokenUsageCostSummary = {
  currency: string;
  estimatedCost: number;
  recent24hEstimatedCost: number;
  ratesPer1k: {
    prompt: number;
    completion: number;
    total: number;
  };
  configured: boolean;
};

export type TokenUsageQuota = {
  enforced: boolean;
  dailyTokenLimit: number;
  monthlyTokenLimit: number;
  currentScopeTokenUsage: number;
  dailyUsageRatio: number;
  monthlyUsageRatio: number;
  alertThreshold: number;
};

export type TokenUsageQuotaAlert = {
  id: string;
  severity: "warning" | "critical" | string;
  title: string;
  message: string;
  usageRatio: number;
  limit: number;
};

export type TokenUsageSummary = {
  source: "kb_rag_query_logs" | string;
  fallbackSource?: string;
  scope?: "all_tenants" | "tenant" | "unscoped" | string;
  detailAvailable?: boolean;
  chartReady?: boolean;
  overall: TokenUsageBucket;
  byPipeline: TokenUsagePipelineBucket[];
  byKnowledgeBase: TokenUsageKnowledgeBaseBucket[];
  byApiKey: TokenUsageApiKeyBucket[];
  pipelineStages?: TokenUsageStageBucket[];
  llmCalls?: LlmCallUsageRecord[];
  hourlyUsage?: TokenUsageHourlyBucket[];
  costSummary?: TokenUsageCostSummary;
  quota?: TokenUsageQuota;
  quotaAlerts?: TokenUsageQuotaAlert[];
};

export type ApiKeyStatus = "active" | "disabled" | "deleted";

export type ApiKeyRecord = {
  id: string;
  appId?: string | null;
  name: string;
  tenantId?: string | null;
  createdBy?: string | null;
  keyPrefix: string;
  keySuffix: string;
  status: ApiKeyStatus;
  kbIds: string[];
  capabilities: string[];
  requireSignature?: boolean;
  allowedIps?: string[];
  rpmLimit?: number;
  dailyRequestLimit?: number;
  note: string;
  expiresAt?: string | null;
  lastUsedAt?: string | null;
  createdAt?: string | null;
  updatedAt?: string | null;
  deletedAt?: string | null;
  plainKey?: string;
};

export type OpenApiAppRecord = {
  id: string;
  name: string;
  tenantId?: string | null;
  ownerUserId?: string | null;
  status: ApiKeyStatus;
  note: string;
  createdAt?: string | null;
  updatedAt?: string | null;
  deletedAt?: string | null;
};

export type SettingsGroup = {
  id: string;
  title: string;
  description: string;
  values: Array<{
    label: string;
    value: string;
    category: "common" | "advanced";
    editable?: boolean;
    source?: "env" | "config" | "code" | "system" | "db";
    sensitive?: boolean;
    hasValue?: boolean;
  }>;
};
