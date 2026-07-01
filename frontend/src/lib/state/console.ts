import { cache } from "react";

import {
  getAlerts,
  getAnswerPreview,
  getDocuments,
  getEvaluations,
  getIngestionTasks,
  getKnowledgeBases,
  getOverviewMetrics,
  getQueueItems,
  getSettingsGroups,
} from "@/lib/api/client";

export const loadOverviewState = cache(async () => {
  const [metrics, alerts, queue, tasks] = await Promise.all([
    getOverviewMetrics(),
    getAlerts(),
    getQueueItems(),
    getIngestionTasks(),
  ]);

  return { metrics, alerts, queue, tasks };
});

export const loadKnowledgeBaseState = cache(async () => {
  const [kbs, docs] = await Promise.all([getKnowledgeBases(), getDocuments()]);
  return { kbs, docs };
});

export const loadOfflineState = cache(async () => {
  const tasks = await getIngestionTasks();
  return { tasks };
});

export const loadOnlineState = cache(async () => {
  const answer = await getAnswerPreview();
  return { answer };
});

export const loadEvaluationState = cache(async () => {
  const evaluations = await getEvaluations();
  return { evaluations };
});

export const loadSettingsState = cache(async () => {
  const settings = await getSettingsGroups();
  return { settings };
});
