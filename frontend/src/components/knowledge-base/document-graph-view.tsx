"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import type { Core, ElementDefinition } from "cytoscape";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import type { DocumentGraphEdge, DocumentGraphNode, DocumentGraphPayload } from "@/lib/contracts/types";

const CHUNK_EDGE_TYPES = [
  "adjacent",
  "sibling",
  "refers_to",
  "semantic_similar",
  "cause_of",
  "effect_of",
  "next_step",
  "prev_step",
  "duplicate_of",
];
const ENTITY_EDGE_TYPES = ["mentions", "triple", "triple_source"];
const EDGE_TYPES = [...CHUNK_EDGE_TYPES, ...ENTITY_EDGE_TYPES];
const DEFAULT_RELATION_VIEW = "chunk";

type RelationViewId = "chunk" | "entity" | "all";

const RELATION_VIEWS: Array<{ id: RelationViewId; label: string; description: string; edgeTypes: string[] }> = [
  { id: "chunk", label: "切片关系", description: "查看文本、图片、表格切片之间的顺序、同级、引用、语义相似和流程因果关系。", edgeTypes: CHUNK_EDGE_TYPES },
  { id: "entity", label: "实体关系", description: "查看切片提到的实体、三元组和来源回溯。", edgeTypes: ENTITY_EDGE_TYPES },
  { id: "all", label: "全部关系", description: "用于排查图谱数据完整性，展示当前文档内全部关系。", edgeTypes: EDGE_TYPES },
];

const EDGE_TYPE_LABELS: Record<string, string> = {
  adjacent: "相邻切片",
  sibling: "同级切片",
  refers_to: "引用指向",
  semantic_similar: "语义相似",
  cause_of: "原因指向",
  effect_of: "结果回指",
  next_step: "下一步",
  prev_step: "上一步",
  duplicate_of: "重复关系",
  mentions: "提到实体",
  triple: "三元组关系",
  triple_source: "三元组来源",
};

const EDGE_TYPE_COLORS: Record<string, string> = {
  adjacent: "#64748B",
  sibling: "#64748B",
  refers_to: "#2563EB",
  semantic_similar: "#8B5CF6",
  cause_of: "#DC2626",
  effect_of: "#EA580C",
  next_step: "#16A34A",
  prev_step: "#0891B2",
  duplicate_of: "#94A3B8",
  mentions: "#0F766E",
  triple: "#7C3AED",
  triple_source: "#B45309",
};

const NODE_LEGEND_ITEMS = [
  { label: "文本切片", className: "rounded-full bg-[#6B7280]" },
  { label: "图片切片", className: "rounded-[4px] bg-[#3B82F6]" },
  { label: "表格切片", className: "rounded-[4px] bg-[#D97706]" },
  { label: "实体/术语", className: "rounded-full bg-[#14B8A6]" },
];

const LOW_SIGNAL_EDGE_TYPES = new Set(["adjacent", "sibling", "duplicate_of"]);

const EDGE_TYPE_PRIORITY: Record<string, number> = {
  cause_of: 100,
  effect_of: 96,
  next_step: 92,
  prev_step: 88,
  refers_to: 84,
  semantic_similar: 72,
  mentions: 68,
  triple: 64,
  triple_source: 60,
  adjacent: 42,
  sibling: 38,
  duplicate_of: 20,
};

type DensityModeId = "readable" | "balanced" | "complete";
type LayoutModeId = "force" | "concentric" | "grid";

const DENSITY_MODES: Array<{
  id: DensityModeId;
  label: string;
  edgeLimit: number | null;
  includeIsolates: boolean;
}> = [
  { id: "readable", label: "清爽", edgeLimit: 160, includeIsolates: false },
  { id: "balanced", label: "标准", edgeLimit: 320, includeIsolates: false },
  { id: "complete", label: "完整", edgeLimit: null, includeIsolates: true },
];

const LAYOUT_MODES: Array<{ id: LayoutModeId; label: string }> = [
  { id: "force", label: "力导向" },
  { id: "concentric", label: "分层环形" },
  { id: "grid", label: "网格" },
];

function edgeTypesForView(viewId: RelationViewId) {
  return RELATION_VIEWS.find((view) => view.id === viewId)?.edgeTypes ?? CHUNK_EDGE_TYPES;
}

function isLargeGraph(graph: DocumentGraphPayload) {
  return graph.scope === "knowledge_base" || graph.nodes.length > 80 || graph.edges.length > 180;
}

function defaultDensityMode(graph: DocumentGraphPayload): DensityModeId {
  return isLargeGraph(graph) ? "readable" : "balanced";
}

function defaultVisibleEdgeTypes(viewId: RelationViewId, edges: DocumentGraphEdge[], graph: DocumentGraphPayload) {
  const base = edgeTypesForView(viewId);
  if (isLargeGraph(graph) && viewId === "chunk") {
    const focused = base.filter((type) => !LOW_SIGNAL_EDGE_TYPES.has(type) && edges.some((edge) => edge.type === type));
    if (focused.length > 0) {
      return new Set(focused);
    }
  }
  return new Set(base);
}

function defaultRelationView(edges: DocumentGraphEdge[]): RelationViewId {
  if (edges.some((edge) => CHUNK_EDGE_TYPES.includes(edge.type))) {
    return "chunk";
  }
  if (edges.some((edge) => ENTITY_EDGE_TYPES.includes(edge.type))) {
    return "entity";
  }
  return DEFAULT_RELATION_VIEW;
}

function edgeSortScore(edge: DocumentGraphEdge) {
  return (EDGE_TYPE_PRIORITY[edge.type] ?? 10) + Number(edge.weight ?? 1);
}

function buildLayout(mode: LayoutModeId, nodeCount: number) {
  if (mode === "concentric") {
    return {
      name: "concentric",
      animate: false,
      fit: true,
      padding: 52,
      minNodeSpacing: nodeCount > 120 ? 42 : 54,
    };
  }
  if (mode === "grid") {
    return {
      name: "grid",
      animate: false,
      fit: true,
      padding: 44,
      avoidOverlap: true,
      avoidOverlapPadding: 18,
    };
  }
  return {
    name: "cose",
    animate: false,
    fit: true,
    padding: 52,
    nodeRepulsion: nodeCount > 120 ? 18000 : 11000,
    idealEdgeLength: nodeCount > 120 ? 180 : 140,
    edgeElasticity: 45,
    gravity: 0.12,
    numIter: nodeCount > 120 ? 1800 : 1000,
  };
}

type DocumentGraphViewProps = {
  graph: DocumentGraphPayload;
  canvasClassName?: string;
};

export function DocumentGraphView({ graph, canvasClassName = "h-[480px]" }: DocumentGraphViewProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const cyRef = useRef<Core | null>(null);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [relationView, setRelationView] = useState<RelationViewId>(() => defaultRelationView(graph.edges));
  const [visibleEdgeTypes, setVisibleEdgeTypes] = useState<Set<string>>(() =>
    defaultVisibleEdgeTypes(defaultRelationView(graph.edges), graph.edges, graph),
  );
  const [densityMode, setDensityMode] = useState<DensityModeId>(() => defaultDensityMode(graph));
  const [layoutMode, setLayoutMode] = useState<LayoutModeId>("force");
  const [showNodeLabels, setShowNodeLabels] = useState(() => !isLargeGraph(graph));
  const [showEdgeLabels, setShowEdgeLabels] = useState(false);

  const nodeById = useMemo(() => new Map(graph.nodes.map((node) => [node.id, node])), [graph.nodes]);
  const nodeIds = useMemo(() => new Set(graph.nodes.map((node) => node.id)), [graph.nodes]);
  const selectedNode = selectedNodeId ? nodeById.get(selectedNodeId) ?? null : null;
  const currentView = RELATION_VIEWS.find((view) => view.id === relationView) ?? RELATION_VIEWS[0];
  const currentDensity = DENSITY_MODES.find((mode) => mode.id === densityMode) ?? DENSITY_MODES[0];
  const availableEdgeTypes = currentView.edgeTypes;
  const candidateNodeIds = useMemo(() => {
    const entityNodeIds = new Set<string>();
    if (relationView === "all") {
      return nodeIds;
    }
    if (relationView === "chunk") {
      graph.nodes.forEach((node) => {
        if (node.type === "chunk") {
          entityNodeIds.add(node.id);
        }
      });
      return entityNodeIds;
    }
    graph.edges.forEach((edge) => {
      if (ENTITY_EDGE_TYPES.includes(edge.type) && nodeIds.has(edge.source) && nodeIds.has(edge.target)) {
        entityNodeIds.add(edge.source);
        entityNodeIds.add(edge.target);
      }
    });
    return entityNodeIds;
  }, [graph.edges, graph.nodes, nodeIds, relationView]);
  const candidateEdges = useMemo(
    () =>
      graph.edges.filter(
        (edge) => visibleEdgeTypes.has(edge.type) && candidateNodeIds.has(edge.source) && candidateNodeIds.has(edge.target),
      ),
    [candidateNodeIds, graph.edges, visibleEdgeTypes],
  );
  const visibleEdges = useMemo(() => {
    const sortedEdges = [...candidateEdges].sort((a, b) => edgeSortScore(b) - edgeSortScore(a));
    return currentDensity.edgeLimit === null ? sortedEdges : sortedEdges.slice(0, currentDensity.edgeLimit);
  }, [candidateEdges, currentDensity.edgeLimit]);
  const visibleNodeIds = useMemo(() => {
    if (currentDensity.includeIsolates) {
      return candidateNodeIds;
    }
    const connectedNodeIds = new Set<string>();
    visibleEdges.forEach((edge) => {
      connectedNodeIds.add(edge.source);
      connectedNodeIds.add(edge.target);
    });
    return connectedNodeIds.size > 0 ? connectedNodeIds : candidateNodeIds;
  }, [candidateNodeIds, currentDensity.includeIsolates, visibleEdges]);
  const visibleNodes = useMemo(() => graph.nodes.filter((node) => visibleNodeIds.has(node.id)), [graph.nodes, visibleNodeIds]);
  const hiddenEdgeCount = graph.edges.length - visibleEdges.length;
  const hiddenByDensityCount = Math.max(candidateEdges.length - visibleEdges.length, 0);

  useEffect(() => {
    const nextView = defaultRelationView(graph.edges);
    setRelationView(nextView);
    setVisibleEdgeTypes(defaultVisibleEdgeTypes(nextView, graph.edges, graph));
    setDensityMode(defaultDensityMode(graph));
    setShowNodeLabels(!isLargeGraph(graph));
    setShowEdgeLabels(false);
    setSelectedNodeId(null);
  }, [graph.documentId, graph.edges]);

  useEffect(() => {
    if (selectedNodeId && !visibleNodeIds.has(selectedNodeId)) {
      setSelectedNodeId(null);
    }
  }, [selectedNodeId, visibleNodeIds]);

  useEffect(() => {
    if (!containerRef.current || graph.nodes.length === 0) {
      return;
    }
    let cancelled = false;

    async function renderGraph() {
      const cytoscape = (await import("cytoscape")).default;
      if (cancelled || !containerRef.current) return;

      cyRef.current?.destroy();
      const elements: ElementDefinition[] = [
        ...visibleNodes.map((node) => ({
          data: {
            id: node.id,
            label: node.label,
            nodeType: node.type,
            chunkType: node.chunkType ?? "",
            entityType: node.entityType ?? "",
          },
        })),
        ...visibleEdges.map((edge) => ({
          data: {
            id: edge.id,
            source: edge.source,
            target: edge.target,
            label: EDGE_TYPE_LABELS[edge.type] ?? edge.label,
            edgeType: edge.type,
            weight: edge.weight ?? 1,
          },
        })),
      ];

      const cy = cytoscape({
        container: containerRef.current,
        elements,
        layout: buildLayout(layoutMode, visibleNodes.length),
        minZoom: 0.3,
        maxZoom: 2.5,
        style: [
          {
            selector: "node",
            style: {
              "background-color": "#6B7280",
              "border-color": "#F8FAFC",
              "border-width": "1px",
              color: "#1F2937",
              "font-size": "9px",
              height: "26px",
              label: showNodeLabels ? "data(label)" : "",
              "overlay-opacity": 0,
              shape: "ellipse",
              "text-max-width": "88px",
              "text-valign": "bottom",
              "text-wrap": "wrap",
              width: "26px",
            },
          },
          { selector: 'node[nodeType = "entity"]', style: { "background-color": "#14B8A6", height: "22px", width: "22px" } },
          { selector: 'node[chunkType = "image"]', style: { "background-color": "#3B82F6", shape: "round-rectangle" } },
          { selector: 'node[chunkType = "table"]', style: { "background-color": "#D97706", shape: "round-rectangle" } },
          {
            selector: "edge",
            style: {
              "curve-style": "bezier",
              "font-size": "8px",
              label: showEdgeLabels ? "data(label)" : "",
              "line-color": "#475569",
              "line-opacity": 0.9,
              "target-arrow-color": "#475569",
              "target-arrow-fill": "filled",
              "target-arrow-shape": "triangle",
              "text-background-color": "#F8FAFC",
              "text-background-opacity": 0.85,
              "text-background-padding": "2px",
              "text-opacity": 0.9,
              "text-rotation": "autorotate",
              width: "1.8px",
            },
          },
          { selector: 'edge[edgeType = "refers_to"]', style: { "line-color": "#2563EB", "target-arrow-color": "#2563EB" } },
          { selector: 'edge[edgeType = "adjacent"]', style: { "line-color": "#64748B", "target-arrow-color": "#64748B" } },
          { selector: 'edge[edgeType = "sibling"]', style: { "line-color": "#64748B", "target-arrow-color": "#64748B", "line-style": "dashed" } },
          { selector: 'edge[edgeType = "semantic_similar"]', style: { "line-color": "#8B5CF6", "target-arrow-color": "#8B5CF6", "line-style": "dotted" } },
          { selector: 'edge[edgeType = "cause_of"]', style: { "line-color": "#DC2626", "target-arrow-color": "#DC2626" } },
          { selector: 'edge[edgeType = "effect_of"]', style: { "line-color": "#EA580C", "target-arrow-color": "#EA580C" } },
          { selector: 'edge[edgeType = "next_step"]', style: { "line-color": "#16A34A", "target-arrow-color": "#16A34A" } },
          { selector: 'edge[edgeType = "prev_step"]', style: { "line-color": "#0891B2", "target-arrow-color": "#0891B2" } },
          { selector: 'edge[edgeType = "duplicate_of"]', style: { "line-color": "#94A3B8", "target-arrow-color": "#94A3B8", "line-style": "dashed" } },
          { selector: 'edge[edgeType = "mentions"]', style: { "line-color": "#0F766E", "target-arrow-color": "#0F766E" } },
          { selector: 'edge[edgeType = "triple"]', style: { "line-color": "#7C3AED", "target-arrow-color": "#7C3AED" } },
          { selector: 'edge[edgeType = "triple_source"]', style: { "line-color": "#B45309", "target-arrow-color": "#B45309" } },
          { selector: ":selected", style: { "border-color": "#111827", "border-width": "2px" } },
        ],
      });

      cy.on("tap", "node", (event) => {
        setSelectedNodeId(event.target.id());
      });
      cyRef.current = cy;
    }

    void renderGraph();
    return () => {
      cancelled = true;
      cyRef.current?.destroy();
      cyRef.current = null;
    };
  }, [graph.nodes.length, layoutMode, showEdgeLabels, showNodeLabels, visibleEdges, visibleNodes]);

  function toggleEdgeType(type: string) {
    setVisibleEdgeTypes((prev) => {
      const next = new Set(prev);
      if (next.has(type)) {
        next.delete(type);
      } else {
        next.add(type);
      }
      return next;
    });
  }

  function selectRelationView(viewId: RelationViewId) {
    const nextView = RELATION_VIEWS.find((view) => view.id === viewId) ?? RELATION_VIEWS[0];
    setRelationView(nextView.id);
    setVisibleEdgeTypes(new Set(nextView.edgeTypes));
  }

  if (graph.nodes.length === 0) {
    return (
      <div className="flex h-[360px] items-center justify-center rounded-lg border border-dashed border-[#C4B5FD] bg-[#F5F3FF] text-sm text-[#6D28D9]">
        当前文档暂无可展示的图谱数据。
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <div className="grid gap-3 md:grid-cols-5">
        <Stat label="节点" value={graph.stats.nodeCount} />
        <Stat label="关系" value={graph.stats.edgeCount} />
        <Stat label="切片" value={graph.stats.chunkCount} />
        <Stat label="实体" value={graph.stats.entityCount} />
        <Stat label="三元组" value={graph.stats.tripleCount} />
      </div>
      {graph.stats.truncated ? (
        <div className="rounded-sm border border-status-warning bg-[#FFFBEB] px-3 py-2 text-xs text-[#B45309]">
          图谱节点已超过当前预览上限，已按关系密度优先展示 {graph.stats.nodeCount} 个节点。
        </div>
      ) : null}
      <div className="flex flex-wrap gap-2">
        {RELATION_VIEWS.map((view) => (
          <Button
            key={view.id}
            variant={relationView === view.id ? "secondary" : "ghost"}
            size="sm"
            onClick={() => selectRelationView(view.id)}
          >
            {view.label}
          </Button>
        ))}
      </div>
      <div className="rounded-lg border border-[#C4B5FD] bg-[linear-gradient(90deg,#F5F3FF,#ECFDF5)] px-3 py-2 shadow-sm">
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-xs text-ink-secondary">
          <span className="font-semibold text-ink-primary">{currentView.label}</span>
          <span>{currentView.description}</span>
          <span className="font-mono text-ink-tertiary">
            可见节点 {visibleNodes.length} / 可见关联 {visibleEdges.length} / 隐藏关联 {hiddenEdgeCount}
            {hiddenByDensityCount > 0 ? ` / 密度折叠 ${hiddenByDensityCount}` : ""}
          </span>
        </div>
        <div className="mt-2 flex flex-wrap gap-x-4 gap-y-2">
          {NODE_LEGEND_ITEMS.map((item) => (
            <span key={item.label} className="inline-flex items-center gap-1.5 text-xs text-ink-tertiary">
              <span className={`h-3 w-3 ${item.className}`} />
              {item.label}
            </span>
          ))}
          {availableEdgeTypes.map((type) => (
            <span key={type} className="inline-flex items-center gap-1.5 text-xs text-ink-tertiary">
              <span className="h-[2px] w-5" style={{ backgroundColor: EDGE_TYPE_COLORS[type] ?? "#64748B" }} />
              {EDGE_TYPE_LABELS[type] ?? type}
            </span>
          ))}
        </div>
      </div>
      <div className="grid gap-3 rounded-lg border border-[#C4B5FD] bg-white px-3 py-3 shadow-sm lg:grid-cols-3">
        <ControlGroup label="显示密度">
          {DENSITY_MODES.map((mode) => (
            <Button key={mode.id} variant={densityMode === mode.id ? "secondary" : "ghost"} size="sm" onClick={() => setDensityMode(mode.id)}>
              {mode.label}
            </Button>
          ))}
        </ControlGroup>
        <ControlGroup label="布局">
          {LAYOUT_MODES.map((mode) => (
            <Button key={mode.id} variant={layoutMode === mode.id ? "secondary" : "ghost"} size="sm" onClick={() => setLayoutMode(mode.id)}>
              {mode.label}
            </Button>
          ))}
        </ControlGroup>
        <ControlGroup label="标签">
          <Button variant={showNodeLabels ? "secondary" : "ghost"} size="sm" onClick={() => setShowNodeLabels((value) => !value)}>
            节点名称
          </Button>
          <Button variant={showEdgeLabels ? "secondary" : "ghost"} size="sm" onClick={() => setShowEdgeLabels((value) => !value)}>
            关系名称
          </Button>
        </ControlGroup>
      </div>
      <div className="flex flex-wrap gap-2">
        {availableEdgeTypes.map((type) => (
          <Button
            key={type}
            variant={visibleEdgeTypes.has(type) ? "secondary" : "ghost"}
            size="sm"
            onClick={() => toggleEdgeType(type)}
          >
            {EDGE_TYPE_LABELS[type] ?? type}
          </Button>
        ))}
      </div>
      {visibleEdges.length === 0 && graph.edges.length > 0 ? (
        <div className="rounded-sm border border-status-warning bg-[#FFFBEB] px-3 py-2 text-xs text-[#B45309]">
          当前视图暂无可见连线；这份文档的关系集中在其他视图，可切换到“实体关系”或“全部关系”查看。
        </div>
      ) : null}
      <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_280px]">
        <div ref={containerRef} className={`${canvasClassName} rounded-lg border border-[#C4B5FD] bg-[radial-gradient(circle_at_top_left,#F5F3FF_0,#F8FAFC_38%,#ECFDF5_100%)] shadow-inner`} />
        <NodePanel node={selectedNode} edges={graph.edges} />
      </div>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-lg border border-[#C4B5FD] bg-[#F5F3FF] px-3 py-2">
      <p className="text-[11px] font-semibold text-[#6D28D9]">{label}</p>
      <p className="mt-1 font-mono text-lg font-semibold text-ink-primary">{value}</p>
    </div>
  );
}

function ControlGroup({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <p className="mb-2 text-[11px] font-semibold uppercase tracking-[0.12em] text-ink-tertiary">{label}</p>
      <div className="flex flex-wrap gap-2">{children}</div>
    </div>
  );
}

function NodePanel({ node, edges }: { node: DocumentGraphNode | null; edges: DocumentGraphEdge[] }) {
  if (!node) {
    return (
      <aside className="rounded-lg border border-[#C4B5FD] bg-white p-4 text-sm text-ink-tertiary shadow-sm">
        点击图中的节点查看详情。
      </aside>
    );
  }
  const linkedEdges = edges.filter((edge) => edge.source === node.id || edge.target === node.id).slice(0, 8);
  const content = typeof node.meta.content === "string" ? node.meta.content : "";
  return (
    <aside className="rounded-lg border border-[#C4B5FD] bg-white p-4 shadow-sm">
      <div className="flex flex-wrap items-center gap-2">
        <Badge variant="info">{node.type === "chunk" ? node.chunkType ?? "chunk" : node.entityType ?? "entity"}</Badge>
        <span className="font-mono text-xs text-ink-tertiary">{node.id}</span>
      </div>
      <h3 className="mt-3 text-sm font-semibold text-ink-primary">{node.label}</h3>
      {content ? <p className="mt-2 line-clamp-6 whitespace-pre-wrap text-xs leading-relaxed text-ink-secondary">{content}</p> : null}
      <div className="mt-3 space-y-1 text-xs text-ink-tertiary">
        {Object.entries(node.meta)
          .filter(([key]) => !["content"].includes(key))
          .slice(0, 8)
          .map(([key, value]) => (
            <div key={key} className="flex justify-between gap-3">
              <span>{key}</span>
              <span className="truncate text-right text-ink-secondary">{String(value ?? "")}</span>
            </div>
          ))}
      </div>
      {linkedEdges.length > 0 ? (
        <div className="mt-4 border-t border-border-subtle pt-3">
          <p className="text-[11px] font-semibold text-ink-tertiary">关联边</p>
          <div className="mt-2 space-y-1">
            {linkedEdges.map((edge) => (
              <div key={edge.id} className="rounded-md bg-[#F5F3FF] px-2 py-1 text-xs text-ink-secondary">
                {EDGE_TYPE_LABELS[edge.type] ?? edge.label}
              </div>
            ))}
          </div>
        </div>
      ) : null}
    </aside>
  );
}
