import type {
  ChunkRecord,
  RetrievalCandidate,
  ScoreSummary,
} from "@/lib/contracts/types";
import { getChunkLayerLabel, getRetrievalChannelLabel, getStrategyLabel, getTaskStateLabel } from "@/lib/i18n/zh-cn";

export function mapChunkToEvidenceRow(chunk: ChunkRecord) {
  return {
    id: chunk.id,
    source: chunk.source,
    page: chunk.page,
    layer: getChunkLayerLabel(chunk.layer),
    strategy: getStrategyLabel(chunk.strategy),
    status: getTaskStateLabel(chunk.qualityScore !== undefined && chunk.qualityScore < 0.4 ? "degraded" : "success"),
    score: chunk.qualityScore ?? null,
    summary: chunk.content,
  };
}

export function mapCandidateToEvidenceRow(candidate: RetrievalCandidate) {
  return {
    id: candidate.id,
    source: candidate.source,
    page: candidate.page,
    layer: getChunkLayerLabel(candidate.layer),
    strategy: getRetrievalChannelLabel(candidate.channel),
    status: getTaskStateLabel(candidate.rerankScore !== undefined && candidate.rerankScore < 0.5 ? "degraded" : "success"),
    score: candidate.rerankScore ?? candidate.score,
    summary: candidate.content,
  };
}

export function interpretScoreSummary(summary: ScoreSummary): string {
  if (summary.cannotAnswer) {
    return "当前召回资料不足以支撑可靠回答，系统已选择不生成可能误导的答案。";
  }

  if (summary.faithfulnessScore < 0.5) {
    return "忠实度偏弱，请检查引文映射与回退来源。";
  }

  if (summary.relevanceScore < 0.5) {
    return "召回质量偏弱，请复查 top_k、稀疏融合与知识库目标。";
  }

  return summary.interpretation;
}
