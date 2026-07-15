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
  category?: string | null;
  highlight_count?: number;
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
    publish?: PublishKit;
    review?: ReviewReport;
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

export interface ReviewReport {
  verdict: string; // pass / needs_improvement / has_problems
  issues: { type: string; severity: string; detail: string; suggestion?: string; fix_ops?: unknown[] }[];
  summary: string;
  auto_fixable?: number;
}

export interface PublishKit {
  title: string;
  description: string;
  hashtags: string[];
  platform: string;
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
  resolution?: string;
  subtitles_burned?: boolean;
  clips?: number;
  transitions?: number;
  music?: string | null;
  error?: string;
}

export interface ChatAction {
  intent: string;
  params: Record<string, unknown>;
  status: string; // pending/done/failed/skipped/invalid
  result?: Record<string, unknown>;
  error?: string;
}

export interface ChatMessage {
  role: string; // user/assistant/action
  content?: string;
  intent?: string;
  status?: string;
  result?: Record<string, unknown>;
  error?: string;
}

export interface MemoryItem {
  id: number;
  kind: string;
  content: string;
  source: string;
  created_at: string | null;
}

export interface BackgroundTask {
  id: number;
  kind: string;
  status: string;
  payload: Record<string, unknown>;
  detail: string | null;
  created_at: string | null;
  updated_at: string | null;
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
  reanalyze: (id: number) => request(`/api/assets/${id}/reanalyze`, { method: "POST" }),
  deleteAsset: (id: number) => request(`/api/assets/${id}`, { method: "DELETE" }),
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
  renderPlan: (id: number, engine = "ffmpeg") =>
    request(`/api/plans/${id}/render`, { method: "POST", body: JSON.stringify({ engine }) }),
  revisePlan: (id: number, instruction: string) =>
    request<{ plan_id: number }>(`/api/plans/${id}/revise`, {
      method: "POST", body: JSON.stringify({ instruction }),
    }),
  setMusic: (id: number, path: string) =>
    request<{ music: string }>(`/api/plans/${id}/music`, {
      method: "PUT", body: JSON.stringify({ path }),
    }),
  removeMusic: (id: number) => request(`/api/plans/${id}/music`, { method: "DELETE" }),
  musicLibrary: () =>
    request<{ tracks: { id: number; path: string; filename: string; duration: number }[] }>("/api/music"),
  scanMusic: () => request<{ added: number; total: number }>("/api/music/scan", { method: "POST" }),
  recommendMusic: (id: number, mood?: string) =>
    request<{ music: string; reason: string }>(`/api/plans/${id}/music/recommend`, {
      method: "POST", body: JSON.stringify({ mood }),
    }),
  setSubtitleStyle: (id: number, body: { preset?: string; position?: string }) =>
    request<{ style: Record<string, unknown> }>(`/api/plans/${id}/subtitle-style`, {
      method: "PUT", body: JSON.stringify(body),
    }),
  resetSubtitleStyle: (id: number) =>
    request(`/api/plans/${id}/subtitle-style`, { method: "DELETE" }),
  setOutput: (id: number, aspect: string, fill = "blur") =>
    request<{ render: { width: number; height: number; fill: string } }>(
      `/api/plans/${id}/output`,
      { method: "PUT", body: JSON.stringify({ aspect, fill }) },
    ),
  resetOutput: (id: number) => request(`/api/plans/${id}/output`, { method: "DELETE" }),
  reviewRender: (id: number) =>
    request<{ review: ReviewReport }>(`/api/plans/${id}/review`, { method: "POST" }),
  applyFixes: (id: number) =>
    request<{ fixed: boolean; new_plan_id: number | null; applied: string[]; manual: string[]; message?: string }>(
      `/api/plans/${id}/apply-fixes`, { method: "POST" }),
  publishKit: (id: number, platform = "抖音") =>
    request<{ publish: PublishKit }>(`/api/plans/${id}/publish-kit`, {
      method: "POST", body: JSON.stringify({ platform }),
    }),
  logs: () => request<{ logs: LogEntry[] }>("/api/logs"),
  tasks: () => request<{ tasks: BackgroundTask[] }>("/api/tasks"),
  chat: (message: string, session_id?: string | null) =>
    request<{ session_id: string; reply: string; actions: ChatAction[] }>(
      "/api/chat",
      { method: "POST", body: JSON.stringify({ message, session_id }) },
    ),
  chatSession: (id: string) =>
    request<{ session_id: string; messages: ChatMessage[] }>(`/api/chat/${id}`),
  memories: () => request<{ memories: MemoryItem[] }>("/api/memory"),
  addMemory: (content: string) =>
    request<MemoryItem>("/api/memory", { method: "POST", body: JSON.stringify({ content }) }),
  deleteMemory: (id: number) => request(`/api/memory/${id}`, { method: "DELETE" }),
};
