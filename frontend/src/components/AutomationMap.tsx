import { useCallback, useEffect, useState } from "react";
import {
  addEdge,
  applyEdgeChanges,
  applyNodeChanges,
  Background,
  Controls,
  MarkerType,
  MiniMap,
  ReactFlow,
  type Connection,
  type Edge,
  type EdgeChange,
  type Node,
  type NodeChange,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import type { AppFlow, AppIntegrations } from "../types";

type PanelMode = "flow" | "integrations";
type ResourceState = "active" | "busy" | "idle" | "pending" | "error";

interface ResourceItem {
  id: string;
  label: string;
  meta: string;
  state: ResourceState;
}

function stateLabel(state: ResourceState) {
  if (state === "active") return "Active";
  if (state === "busy") return "In progress";
  if (state === "error") return "Error";
  if (state === "idle") return "Ready";
  return "Pending";
}

function integrationLabel(item: Record<string, unknown>, fallback: string) {
  const label = item.label ?? item.name ?? item.url ?? item.id;
  return typeof label === "string" && label.trim() ? label : fallback;
}

function integrationMeta(item: Record<string, unknown>, fallback: string) {
  const meta = item.meta ?? item.description ?? item.type ?? item.status;
  return typeof meta === "string" && meta.trim() ? meta : fallback;
}

function integrationState(item: Record<string, unknown>): ResourceState {
  const state = item.state ?? item.status;
  if (
    state === "active" ||
    state === "busy" ||
    state === "idle" ||
    state === "pending" ||
    state === "error"
  ) {
    return state;
  }
  return "pending";
}

function toResources(items: Record<string, unknown>[], fallbackLabel: string): ResourceItem[] {
  return items.map((item, index) => ({
    id: String(item.id ?? `${fallbackLabel}-${index}`),
    label: integrationLabel(item, fallbackLabel),
    meta: integrationMeta(item, "Co-created with the user"),
    state: integrationState(item),
  }));
}

function ResourceLane({ title, items }: { title: string; items: ResourceItem[] }) {
  return (
    <section className="resource-lane">
      <div className="lane-head">
        <h3>{title}</h3>
        <span>{items.length}</span>
      </div>
      <div className="resource-list">
        {items.length === 0 && (
          <article className="resource-empty">
            <p>No items yet</p>
            <span>They'll be added when the app needs them.</span>
          </article>
        )}
        {items.map((item) => (
          <article className={`resource-item resource-${item.state}`} key={item.id}>
            <div className="resource-dot" aria-hidden="true" />
            <div>
              <p>{item.label}</p>
              <span>{item.meta}</span>
            </div>
            <strong>{stateLabel(item.state)}</strong>
          </article>
        ))}
      </div>
    </section>
  );
}

function cleanNode(node: Node): Node {
  return {
    id: node.id,
    type: node.type ?? "default",
    position: node.position,
    data: {
      label: typeof node.data?.label === "string" ? node.data.label : "New step",
    },
  };
}

function cleanEdge(edge: Edge): Edge {
  return {
    id: edge.id,
    source: edge.source,
    target: edge.target,
    type: edge.type ?? "smoothstep",
    label: typeof edge.label === "string" ? edge.label : undefined,
    animated: edge.animated ?? false,
    markerEnd: {
      type: MarkerType.ArrowClosed,
      color: "#0877ee",
    },
  };
}

function storedNode(node: Node): AppFlow["nodes"][number] {
  return {
    id: node.id,
    type: node.type ?? "default",
    position: node.position,
    data: {
      label: typeof node.data?.label === "string" ? node.data.label : "New step",
    },
  };
}

function storedEdge(edge: Edge): AppFlow["edges"][number] {
  return {
    id: edge.id,
    source: edge.source,
    target: edge.target,
    type: edge.type ?? "smoothstep",
    label: typeof edge.label === "string" ? edge.label : undefined,
    animated: edge.animated ?? false,
  };
}

function normalizeFlow(flow: AppFlow): { nodes: Node[]; edges: Edge[] } {
  return {
    nodes: flow.nodes.map((node) =>
      cleanNode({
        ...node,
        type: node.type ?? "default",
        data: {
          label: typeof node.data?.label === "string" ? node.data.label : "Step",
        },
      } as Node),
    ),
    edges: flow.edges.map((edge) =>
      cleanEdge({
        ...edge,
        type: edge.type ?? "smoothstep",
        markerEnd: {
          type: MarkerType.ArrowClosed,
          color: "#0877ee",
        },
      } as Edge),
    ),
  };
}

export function AutomationMap({
  mode,
  onClose,
  appTitle,
  flow,
  integrations,
  saving,
  onFlowChange,
}: {
  mode: PanelMode | null;
  onClose: () => void;
  appTitle: string;
  flow: AppFlow;
  integrations: AppIntegrations;
  saving: boolean;
  onFlowChange: (flow: AppFlow) => void;
}) {
  const [nodes, setNodes] = useState<Node[]>([]);
  const [edges, setEdges] = useState<Edge[]>([]);

  useEffect(() => {
    const normalized = normalizeFlow(flow);
    setNodes(normalized.nodes);
    setEdges(normalized.edges);
  }, [flow]);

  const publish = useCallback(
    (nextNodes: Node[], nextEdges: Edge[]) => {
      onFlowChange({
        nodes: nextNodes.map(storedNode),
        edges: nextEdges.map(storedEdge),
      });
    },
    [onFlowChange],
  );

  const onNodesChange = useCallback(
    (changes: NodeChange[]) => {
      setNodes((currentNodes) => {
        const nextNodes = applyNodeChanges(changes, currentNodes);
        publish(nextNodes, edges);
        return nextNodes;
      });
    },
    [edges, publish],
  );

  const onEdgesChange = useCallback(
    (changes: EdgeChange[]) => {
      setEdges((currentEdges) => {
        const nextEdges = applyEdgeChanges(changes, currentEdges);
        publish(nodes, nextEdges);
        return nextEdges;
      });
    },
    [nodes, publish],
  );

  const onConnect = useCallback(
    (connection: Connection) => {
      setEdges((currentEdges) => {
        const nextEdges = addEdge(
          {
            ...connection,
            type: "smoothstep",
            animated: true,
            markerEnd: {
              type: MarkerType.ArrowClosed,
              color: "#0877ee",
            },
          },
          currentEdges,
        );
        publish(nodes, nextEdges);
        return nextEdges;
      });
    },
    [nodes, publish],
  );

  if (!mode) return null;

  const mcpServers = toResources(integrations.mcp_servers, "MCP Server");
  const apis = toResources(integrations.apis, "API");
  const tools = toResources(integrations.tools, "Tool");

  return (
    <aside className="workspace-drawer" aria-label={mode === "flow" ? "App Flow" : "Integrations"}>
      <header className="workspace-head">
        <div>
          <p className="eyebrow">{mode === "flow" ? "React Flow" : "Connectors"}</p>
          <h2>{mode === "flow" ? "App Flow" : "Integrations"}</h2>
        </div>
        <button className="close-panel-btn" type="button" onClick={onClose} aria-label="Close">
          &times;
        </button>
      </header>

      <section className="intent-strip">
        <p>{appTitle}</p>
        <div className="intent-stats">
          <span>{flow.nodes.length} nodes</span>
          <span>{flow.edges.length} connections</span>
          <span>{saving ? "saving" : "saved"}</span>
        </div>
      </section>

      {mode === "flow" ? (
        <>
          <section className="react-flow-card" aria-label="Business logic flow">
            {nodes.length === 0 && (
              <div className="flow-empty-state">
                <p>The flow builds itself</p>
                <span>
                  Describe the automation in the chat and the steps will appear here
                  automatically.
                </span>
              </div>
            )}
            <ReactFlow
              nodes={nodes}
              edges={edges}
              onNodesChange={onNodesChange}
              onEdgesChange={onEdgesChange}
              onConnect={onConnect}
              fitView
              fitViewOptions={{ padding: 0.18 }}
              minZoom={0.35}
              maxZoom={1.7}
              proOptions={{ hideAttribution: true }}
            >
              <Background color="#d9e1ec" gap={22} />
              <Controls />
              <MiniMap pannable={false} zoomable={false} />
            </ReactFlow>
          </section>
        </>
      ) : (
        <div className="integrations-board">
          <ResourceLane title="External apps (auth)" items={mcpServers} />
          <ResourceLane title="APIs" items={apis} />
          <ResourceLane title="Tools" items={tools} />
        </div>
      )}
    </aside>
  );
}
