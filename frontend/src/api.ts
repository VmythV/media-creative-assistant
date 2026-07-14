export interface Capability {
  type: string;
  name: string;
  available: boolean;
  features: string[];
  fallback: string | null;
  [key: string]: unknown;
}

export interface Asset {
  id: number;
  path: string;
  filename: string;
  duration: number | null;
  width: number | null;
  height: number | null;
  fps: number | null;
  has_audio: boolean;
  status: string;
}

export interface Highlight {
  asset_id: number;
  filename?: string;
  shot_index: number;
  start: number;
  end: number;
  score: number;
  category?: string;
  reason: string;
}

export interface Plan {
  id: number;
  goal: string;
  status: string;
  plan: {
    title?: string;
    clips?: PlanClip[];
    error?: string;
    execution?: ExecutionResult;
    render?: RenderResult;
    revised_from?: number;
    revision_instruction?: string;
    diff?: PlanDiff;
  };
  ir: Record<string, unknown> | null;
  created_at: string;
}

export interface PlanClip {
  section: string;
  asset_id: number;
  start: number;
  end: number;
  reason: string;
  subtitle: string | null;
  transition?: { type: string; duration: number } | null;
}

export interface ExecutionResult {
  mode: string;
  resolve?: {
    project: string; timeline: string; clips: number;
    subtitles: Record<string, unknown>;
    transitions?: { count: number; method: string } | null;
    music?: { file: string; method: string; track?: number } | null;
  };
  artifacts: Record<string, string>;
}

export interface PlanDiff {
  added: string[];
  removed: string[];
  changed: string[];
  duration: string;
  unchanged: number;
}

export interface RenderResult {
  video?: string;
  video_url?: string;
  duration?: number;
  subtitles_burned?: boolean;
  clips?: number;
  transitions?: number;
  music?: string | null;
  error?: string;
}

export interface MemoryItem {
  id: number;
  kind: string;
  content: string;
  source: string;
  created_at: string | null;
}

export interface LogEntry {
  id: number;
  task_id: string;
  tool: string;
  input: string;
  output: string;
  error: string | null;
  ts: string;
}

export interface AppEvent {
  type: string;
  ts: string;
  [key: string]: unknown;
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(url, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!resp.ok) {
    const body = await resp.json().catch(() => ({}));
    throw new Error(body.detail || `${resp.status} ${resp.statusText}`);
  }
  return resp.json();
}

export const api = {
  capabilities: () => request<{ capabilities: Capability[] }>("/api/capabilities"),
  assets: () => request<{ assets: Asset[] }>("/api/assets"),
  importAssets: (body: { paths?: string[]; directory?: string }) =>
    request<{ imported: Asset[]; errors: { path: string; error: string }[] }>(
      "/api/assets/import",
      { method: "POST", body: JSON.stringify(body) },
    ),
  analyze: (id: number) => request(`/api/assets/${id}/analyze`, { method: "POST" }),
  analyzeAll: () => request("/api/assets/analyze-all", { method: "POST" }),
  analysis: (id: number) =>
    request<{ asset: Asset; analysis: Record<string, any> }>(`/api/assets/${id}/analysis`),
  highlights: () => request<{ highlights: Highlight[] }>("/api/highlights"),
  plans: () => request<{ plans: Plan[] }>("/api/plans"),
  plan: (id: number) => request<Plan>(`/api/plans/${id}`),
  createPlan: (goal: string) =>
    request<{ plan_id: number }>("/api/plans", { method: "POST", body: JSON.stringify({ goal }) }),
  confirmPlan: (id: number) => request<Plan>(`/api/plans/${id}/confirm`, { method: "POST" }),
  executePlan: (id: number) =>
    request(`/api/plans/${id}/execute`, { method: "POST", body: JSON.stringify({}) }),
  renderPlan: (id: number) => request(`/api/plans/${id}/render`, { method: "POST" }),
  revisePlan: (id: number, instruction: string) =>
    request<{ plan_id: number }>(`/api/plans/${id}/revise`, {
      method: "POST", body: JSON.stringify({ instruction }),
    }),
  setMusic: (id: number, path: string) =>
    request<{ music: string }>(`/api/plans/${id}/music`, {
      method: "PUT", body: JSON.stringify({ path }),
    }),
  removeMusic: (id: number) => request(`/api/plans/${id}/music`, { method: "DELETE" }),
  logs: () => request<{ logs: LogEntry[] }>("/api/logs"),
  memories: () => request<{ memories: MemoryItem[] }>("/api/memory"),
  addMemory: (content: string) =>
    request<MemoryItem>("/api/memory", { method: "POST", body: JSON.stringify({ content }) }),
  deleteMemory: (id: number) => request(`/api/memory/${id}`, { method: "DELETE" }),
};
