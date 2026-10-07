// src/services/api.ts
const API_URL = (import.meta.env.VITE_API_URL || "/api").replace(/\/$/, "");

export type RiskBand = 'MINIMAL' | 'LOW' | 'MODERATE' | 'ELEVATED' | 'SEVERE';

/** One computed market metric behind the financial dimension. */
export interface RiskSignal {
  metric: string;
  value: string;
  score: number;
  note: string;
}

export interface SanctionMatch {
  name: string;
  matched_name: string;
  matched_on: string;
  entity_type: string;
  program: string;
  remarks: string;
  confidence: number;
  source?: string;
  topics?: string[];
  datasets?: string[];
  url?: string;
}

/** One sourced fact, as collected by a specialist agent. */
export interface EvidenceRecord {
  id: string;
  summary: string;
  kind: 'filing' | 'market_data' | 'watchlist' | 'policy' | 'open_source' | 'baseline' | string;
  source: string;
  collector: string;
  confidence: 'high' | 'medium' | 'low' | string;
  collected_at: string;
  url?: string;
  detail?: string;
  metadata?: Record<string, any>;
}

/** One routing decision taken before any specialist ran. */
export interface RoutingDecision {
  agent: string;
  engaged: boolean;
  reason: string;
}

export interface InvestigationPlan {
  strategy: 'full' | 'reduced' | string;
  engaged: string[];
  skipped: RoutingDecision[];
  decisions: RoutingDecision[];
  summary: string;
}

export interface RiskDimension {
  id: string;
  label: string;
  weight: number;
  score: number;
  band: RiskBand;
  rationale: string;
  assessed: boolean;
  signals?: RiskSignal[];
  status?: string;
  matches?: SanctionMatch[];
  providers?: SanctionsProvider[];
  /** Ids of the evidence this factor rests on — resolvable against `evidence`. */
  evidence_ids?: string[];
  finding_ids?: string[];
  grounded?: boolean;
  contribution?: number;
}

/** The scored verdict the supervisor produces. */
export interface RiskAssessment {
  score: number;
  band: RiskBand;
  confidence: number;
  summary: string;
  key_drivers: string[];
  mitigants: string[];
  evidence_quality: string;
  dimensions: RiskDimension[];
  model_ok: boolean;
  model_error: string | null;
  /** True when a categorical finding lifted the score above the weighted average. */
  escalated?: boolean;
  escalation_note?: string;
  /** The composite before any escalation was applied. */
  weighted_average?: number;
  /** Every fact gathered during the run. */
  evidence?: EvidenceRecord[];
  /** The subset the verdict actually cites. */
  cited_evidence?: EvidenceRecord[];
}

export interface ComplianceObligation {
  control: string;
  requirement: string;
  applies_because: string;
  framework: string;
  page?: number | null;
  url?: string;
  excerpt?: string;
  severity: 'core' | 'standard' | 'advisory';
}

export interface CompliancePassage {
  framework: string;
  page?: number | null;
  url?: string;
  relevance?: number;
  excerpt: string;
}

/** Synthesised control set plus the passages it was drawn from. */
export interface ComplianceBrief {
  retrieval: 'frameworks' | 'web' | 'unavailable';
  summary: string;
  obligations: ComplianceObligation[];
  sources: CompliancePassage[];
  error: string | null;
}

export interface EdgarFlag {
  code: string;
  label: string;
  severity: 'severe' | 'elevated' | 'moderate' | 'low';
  floor: number;
  date: string;
  form: string;
  accession: string;
}

/** SEC EDGAR registrant profile and supervisory filing flags. */
export interface EdgarProfile {
  matched: boolean;
  subject: string;
  reason?: string;
  cik?: string;
  company?: string;
  ticker?: string;
  sic?: string;
  sic_description?: string;
  state_of_incorporation?: string;
  fiscal_year_end?: string;
  exchanges?: string[];
  source_url?: string;
  flags: EdgarFlag[];
  counts: Record<string, number>;
  routine?: Record<string, number>;
  latest_annual?: string | null;
  annual_age_days?: number | null;
  filings_reviewed?: number;
}

export interface SanctionsProvider {
  id: string;
  label: string;
  status: string;
  available: boolean;
  detail: string;
}

export interface SanctionsScreening {
  screened: boolean;
  subject: string;
  list_size: number;
  status: 'HIT' | 'POSSIBLE_MATCH' | 'CLEAR' | 'UNAVAILABLE';
  matches: SanctionMatch[];
  providers?: SanctionsProvider[];
}

/**
 * Four states, not two. `empty` means the tool ran and found nothing - which is
 * a real answer about the entity. `unavailable` means it never ran at all, so
 * its silence says nothing. Conflating them is how a verdict claims confidence
 * it has not earned.
 */
export type ToolOutcome = 'ok' | 'empty' | 'failed' | 'unavailable';

export interface ToolInvocation {
  id: string;
  tool: string;
  caller: string;
  outcome: ToolOutcome;
  ok: boolean;
  /** Recorded so a screening can be reproduced; secret-looking names are masked. */
  arguments: Record<string, string | number | boolean | null>;
  duration_ms: number;
  result_digest: string;
  error: string;
  /** The evidence this call produced - the link that closes the chain. */
  evidence_ids: string[];
  called_at: string;
}

export interface ToolAudit {
  count: number;
  tools_used: string[];
  total_duration_ms: number;
  failures: number;
  summary: string;
  invocations: ToolInvocation[];
}

export type ReviewVerdict = 'endorsed' | 'qualified' | 'rejected';
export type ChallengeSeverity = 'blocking' | 'material' | 'advisory';

/** One objection the critic raised, with what would answer it. */
export interface Challenge {
  id: string;
  kind: string;
  severity: ChallengeSeverity;
  subject: string;
  detail: string;
  remedy: string;
}

export interface CriticReview {
  id: string;
  verdict: ReviewVerdict;
  summary: string;
  challenges: Challenge[];
  blocking: number;
  material: number;
  /** Deducted from the supervisor's confidence. Never negative. */
  confidence_penalty: number;
  /** How many times the verdict was sent back before reaching the officer. */
  revision: number;
  model_ok: boolean;
  model_error: string;
  reviewed_at: string;
}

export interface PriorReview {
  investigation_id: string;
  band: string;
  reviewed_at: string;
  status: string;
  approved: boolean | null;
}

export type HistoryTrend = 'first_review' | 'deteriorating' | 'stable' | 'improving';

export interface EntityHistory {
  subject: string;
  count: number;
  is_first_review: boolean;
  trend: HistoryTrend;
  trend_label: string;
  rejection_count: number;
  summary: string;
  reviews: PriorReview[];
  checked_at: string;
}

export interface GraphNode {
  id: string;
  label: string;
  kind: string;
  detail: string;
  flagged: boolean;
}

export interface GraphEdge {
  source: string;
  target: string;
  kind: string;
  label: string;
  evidence_ids: string[];
}

export interface KnowledgeGraph {
  root: string;
  node_count: number;
  edge_count: number;
  flagged_count: number;
  summary: string;
  nodes: GraphNode[];
  edges: GraphEdge[];
}

export interface FraudSignal {
  id: string;
  label: string;
  category: string;
  category_label: string;
  weight: number;
  detail: string;
  evidence_ids: string[];
  observed_at: string;
}

export interface FraudPattern {
  id: string;
  label: string;
  detail: string;
  signal_ids: string[];
  uplift: number;
}

export interface FraudAssessment {
  id: string;
  score: number;
  band: string;
  summary: string;
  categories: string[];
  signals: FraudSignal[];
  patterns: FraudPattern[];
  assessed_at: string;
}

export interface InvestigationData {
  id: string;
  subject_name: string;
  ticker?: string;
  status: string;
  risk_level?: string;
  plan?: InvestigationPlan | null;
  risk_assessment?: RiskAssessment | null;
  confidence?: number;  // Certainty level 0-1
  human_approved?: boolean | null;
  supervisor_reasoning?: string;
  final_report?: string;
  research_data?: any[];
  financial_data?: Record<string, any>;
  news_data?: any[];
  compliance_data?: ComplianceBrief | null;
  sanctions_data?: SanctionsScreening | null;
  edgar_data?: EdgarProfile | null;
  logs?: string[];
  /** Every external tool call made during the run, with arguments and outcome. */
  tool_audit?: ToolAudit | null;
  /** The critic's independent reading of the verdict. */
  critic_review?: CriticReview | null;
  /** Prior reviews of the same entity. */
  memory_data?: EntityHistory | null;
  /** Entities connected to the subject, and how. */
  graph_data?: KnowledgeGraph | null;
  /** Fraud indicators and their convergence patterns. */
  fraud_data?: FraudAssessment | null;
  created_at?: string;
}

export interface AuditRecord {
  "Database ID": string;
  "Created At": string;
  Company: string;
  Ticker: string;
  Status: string;
  "Risk Level": string;
  Approved: string;
  /** Whether an executive report was stored and can be downloaded. */
  "Has Report"?: boolean;
}

export interface LlmProvider {
  name: string;
  available: boolean;
  role: 'primary' | 'fallback' | string;
  models: Record<string, string>;
}

/** What one agent spent at the gateway. */
export interface AgentUsage {
  calls: number;
  cache_hits: number;
  tokens: number;
  cost_usd: number;
  latency_ms: number;
  provider: string;
  model: string;
}

export interface LlmUsage {
  calls: number;
  cache_hits: number;
  cache_hit_rate: number;
  total_tokens: number;
  cost_usd: number;
  fallbacks: number;
  by_caller: Record<string, AgentUsage>;
}

/** Durability and provider guarantees the running backend actually provides. */
export interface SystemStatus {
  checkpointing: {
    backend: 'postgres' | 'memory' | string;
    durable: boolean;
    detail: string;
  };
  database: { engine: string };
  llm?: {
    providers: LlmProvider[];
    cache_enabled: boolean;
    usage_since_startup: LlmUsage;
  };
}

export interface FileMetadata {
  name: string;
  type: 'dataset' | 'doc';
  size: number;
  ext: string;
  path: string;
  id: string;
  filename: string;
  uploaded_at: string;
  status?: string;
}

/** One frame of live agent telemetry emitted by the graph while it runs. */
export type AgentEvent =
  | { type: 'run.start'; investigation_id?: string; nodes?: string[]; resumed?: boolean }
  | { type: 'node.start'; node: string; at: number }
  | {
      type: 'node.end';
      node: string;
      at: number;
      duration_ms: number | null;
      log: string;
      channels: string[];
      risk_level: string | null;
      error: string | null;
    }
  | { type: 'run.error'; message: string; at: number };

export type AgentEventHandler = (event: AgentEvent) => void;

export function assetUrl(path: string): string {
  return `${API_URL}${path}`;
}

/**
 * Link to a stored executive report. The server sets Content-Disposition, so a
 * plain anchor downloads it under the right filename even cross-origin.
 */
export function reportUrl(investigationId: string, format: 'md' | 'pdf'): string {
  return `${API_URL}/investigations/${investigationId}/report?format=${format}`;
}

export async function startInvestigation(subjectName: string, ticker: string): Promise<InvestigationData> {
  const res = await fetch(`${API_URL}/investigations`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ subject_name: subjectName, ticker }),
  });
  if (!res.ok) throw new Error(`API Error: ${res.statusText}`);
  return res.json();
}

export async function approveInvestigation(id: string, approved: boolean): Promise<InvestigationData> {
  const res = await fetch(`${API_URL}/investigations/${id}/approve`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ approved }),
  });
  if (!res.ok) throw new Error(`API Error: ${res.statusText}`);
  return res.json();
}

export async function fetchAuditHistory(): Promise<AuditRecord[]> {
  const res = await fetch(`${API_URL}/investigations`);
  if (!res.ok) throw new Error(`API Error: ${res.statusText}`);
  return res.json();
}

export async function fetchSystemStatus(): Promise<SystemStatus> {
  const res = await fetch(`${API_URL}/system`);
  if (!res.ok) throw new Error(`API Error: ${res.statusText}`);
  return res.json();
}

export async function fetchAssetFiles(): Promise<FileMetadata[]> {
  const res = await fetch(`${API_URL}/assets/files`);
  if (!res.ok) throw new Error(`API Error: ${res.statusText}`);
  return res.json();
}

/**
 * Consumes a Server-Sent Events body, handing each telemetry frame to `onEvent`
 * and resolving with the investigation carried by the closing `run.complete`.
 */
async function consumeAgentStream(
  res: Response,
  onEvent?: AgentEventHandler,
): Promise<InvestigationData> {
  if (!res.body) throw new Error('Streaming is unavailable in this browser.');

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  // Held in an object so TypeScript keeps the assignments made inside the
  // frame handler visible to the checks after the read loop.
  const outcome: { final?: InvestigationData; error?: string } = {};

  const handleFrame = (frame: string) => {
    let name = 'message';
    const dataLines: string[] = [];
    for (const line of frame.split(/\r?\n/)) {
      if (line.startsWith('event:')) name = line.slice(6).trim();
      else if (line.startsWith('data:')) dataLines.push(line.slice(5).trim());
    }
    if (!dataLines.length) return;

    let payload: any;
    try {
      payload = JSON.parse(dataLines.join('\n'));
    } catch {
      return;
    }

    if (name === 'run.complete') {
      outcome.final = payload as InvestigationData;
      return;
    }
    if (name === 'run.error') outcome.error = String(payload.message || 'Agent run failed.');
    onEvent?.({ type: name, ...payload } as AgentEvent);
  };

  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    let split = buffer.indexOf('\n\n');
    while (split !== -1) {
      handleFrame(buffer.slice(0, split));
      buffer = buffer.slice(split + 2);
      split = buffer.indexOf('\n\n');
    }
  }
  if (buffer.trim()) handleFrame(buffer);

  if (!outcome.final) {
    throw new Error(outcome.error || 'The agent stream closed before returning a result.');
  }
  return outcome.final;
}

/**
 * Launches an investigation over SSE so the UI can watch the specialist agents
 * run in parallel. Falls back to the blocking endpoint when the stream route is
 * unavailable, in which case no telemetry frames are emitted.
 */
export async function streamInvestigation(
  subjectName: string,
  ticker: string,
  onEvent?: AgentEventHandler,
): Promise<InvestigationData> {
  let res: Response;
  try {
    res = await fetch(`${API_URL}/investigations/stream`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
      body: JSON.stringify({ subject_name: subjectName, ticker }),
    });
  } catch {
    return startInvestigation(subjectName, ticker);
  }

  if (res.status === 404 || res.status === 405) return startInvestigation(subjectName, ticker);
  if (!res.ok) throw new Error(`API Error: ${res.statusText}`);
  return consumeAgentStream(res, onEvent);
}

/** Resumes the graph past the human checkpoint, streaming the remaining nodes. */
export async function streamApproval(
  id: string,
  approved: boolean,
  onEvent?: AgentEventHandler,
): Promise<InvestigationData> {
  let res: Response;
  try {
    res = await fetch(`${API_URL}/investigations/${id}/approve/stream`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
      body: JSON.stringify({ approved }),
    });
  } catch {
    return approveInvestigation(id, approved);
  }

  if (res.status === 404 || res.status === 405) return approveInvestigation(id, approved);
  if (!res.ok) throw new Error(`API Error: ${res.statusText}`);
  return consumeAgentStream(res, onEvent);
}
