import { useEffect, useMemo, useRef, useState } from 'react';
import { AnimatePresence, motion, useReducedMotion } from 'framer-motion';
import {
  Activity,
  BookOpen,
  ChevronRight,
  Crosshair,
  Fingerprint,
  History,
  Share2,
  GitBranch,
  FileText,
  Gavel,
  Globe,
  ScrollText,
  Sparkles,
  TrendingUp,
  UserCheck,
} from 'lucide-react';
import type { AgentEvent, AgentUsage, InvestigationData, RoutingDecision } from '../services/api';

export type AgentPhase = 'idle' | 'active' | 'complete' | 'error' | 'waiting' | 'skipped';

export interface NodeRuntime {
  phase: AgentPhase;
  /** Milliseconds from the start of the run, as measured server-side. */
  startedAt?: number;
  endedAt?: number;
  durationMs?: number | null;
  /** Wall-clock reading taken when the browser saw the node start ticking. */
  tickingSince?: number;
  log?: string;
  error?: string | null;
}

export type AgentRuntime = Record<string, NodeRuntime>;

interface AgentDef {
  id: string;
  code: string;
  name: string;
  short: string;
  role: string;
  blurb: string;
  tool: string;
  emits: string;
  tab?: 'research' | 'financials' | 'osint' | 'compliance' | 'history' | 'network' | 'fraud' | 'provenance' | 'logs';
  icon: typeof FileText;
  x: number;
  y: number;
  r: number;
  kind: 'specialist' | 'analyst' | 'core';
}

const STAGE_W = 1180;
const STAGE_H = 500;
const COLLECTOR_X = 262;
const ANALYST_X = 500;
const SUPERVISOR = { x: 706, y: 250 };
const CRITIC = { x: 872, y: 250 };
const CHECKPOINT = { x: 1054, y: 150 };
const REPORT = { x: 1054, y: 350 };
const ORIGIN = { x: 58, y: 250 };

export const AGENTS: AgentDef[] = [
  {
    id: 'research_analyst',
    code: 'R-01',
    name: 'Research Analyst',
    short: 'Research',
    role: 'Corporate baseline',
    blurb:
      'Resolves the entity into a identity file — sector, core business and ticker mapping — that the other three specialists are scored against.',
    tool: 'LLM baseline resolver',
    emits: 'research_data',
    tab: 'research',
    icon: FileText,
    x: COLLECTOR_X,
    y: 44,
    r: 32,
    kind: 'specialist',
  },
  {
    id: 'financial_analyst',
    code: 'F-02',
    name: 'Financial Analyst',
    short: 'Financials',
    role: 'Market telemetry',
    blurb:
      'Pulls live market structure for the ticker — market cap, P/E, total debt, free cash flow and margins — straight off the exchange feed.',
    tool: 'yfinance market feed',
    emits: 'financial_data',
    tab: 'financials',
    icon: TrendingUp,
    x: COLLECTOR_X,
    y: 147,
    r: 32,
    kind: 'specialist',
  },
  {
    id: 'news_analyst',
    code: 'O-03',
    name: 'OSINT Analyst',
    short: 'OSINT',
    role: 'Open-source signals',
    blurb:
      'Sweeps the open web for controversy, litigation and sanctions chatter attached to the target, returning sourced links rather than summaries.',
    tool: 'Tavily web search',
    emits: 'news_data',
    tab: 'osint',
    icon: Globe,
    x: COLLECTOR_X,
    y: 250,
    r: 32,
    kind: 'specialist',
  },
  {
    id: 'compliance_analyst',
    code: 'C-04',
    name: 'Compliance RAG',
    short: 'Compliance',
    role: 'Policy retrieval',
    blurb:
      'Queries the local vector store of enforcement manuals and NIST/ECCP guidance for the specific controls this evaluation has to satisfy.',
    tool: 'Chroma vector store',
    emits: 'compliance_data',
    tab: 'compliance',
    icon: BookOpen,
    x: COLLECTOR_X,
    y: 353,
    r: 32,
    kind: 'specialist',
  },
  {
    id: 'memory',
    code: 'M-05',
    name: 'Memory Agent',
    short: 'Memory',
    role: 'Entity recall',
    blurb:
      'Queries the audit ledger for earlier reviews of the same entity, so a verdict is placed against the record rather than delivered in isolation.',
    tool: 'Investigation ledger',
    emits: 'memory_data',
    tab: 'history',
    icon: History,
    x: COLLECTOR_X,
    y: 456,
    r: 32,
    kind: 'specialist',
  },
  {
    id: 'fraud_analyst',
    code: 'X-06',
    name: 'Fraud Analyst',
    short: 'Fraud',
    role: 'Signal convergence',
    blurb:
      'Scores red flags jointly rather than singly. An auditor change, a late filing and an officer exodus in one window describe a different company than any one of them alone.',
    tool: 'Fraud triangle model',
    emits: 'fraud_data',
    tab: 'fraud',
    icon: Fingerprint,
    x: ANALYST_X,
    y: 150,
    r: 34,
    kind: 'analyst',
  },
  {
    id: 'graph_analyst',
    code: 'G-07',
    name: 'Network Analyst',
    short: 'Network',
    role: 'Entity relationships',
    blurb:
      'Resolves the entities connected to the subject from collected evidence, surfacing exposure that sits one hop away rather than under the subject own name.',
    tool: 'Relationship resolver',
    emits: 'graph_data',
    tab: 'network',
    icon: Share2,
    x: ANALYST_X,
    y: 350,
    r: 34,
    kind: 'analyst',
  },
  {
    id: 'supervisor',
    code: 'S-00',
    name: 'Risk Supervisor',
    short: 'Supervisor',
    role: 'Synthesis & verdict',
    blurb:
      'Waits for every engaged agent to land, weighs five scored dimensions against the retrieved policy, and composes the banded verdict.',
    tool: 'Chief risk officer prompt',
    emits: 'risk_level · supervisor_reasoning',
    tab: 'logs',
    icon: Sparkles,
    x: SUPERVISOR.x,
    y: SUPERVISOR.y,
    r: 40,
    kind: 'core',
  },
  {
    id: 'critic',
    code: 'K-08',
    name: 'Critic',
    short: 'Critic',
    role: 'Independent review',
    blurb:
      'Reads the verdict back from the record and tries to break it. Six deterministic checks plus one narrow model question; a rejected verdict returns to the supervisor once.',
    tool: 'Anchored review checks',
    emits: 'critic_review',
    tab: 'provenance',
    icon: Gavel,
    x: CRITIC.x,
    y: CRITIC.y,
    r: 34,
    kind: 'core',
  },
  {
    id: 'human_approval',
    code: 'H-IL',
    name: 'Human Checkpoint',
    short: 'Checkpoint',
    role: 'Officer decision',
    blurb:
      'The graph interrupts here. Nothing is written to the ledger and no report is generated until a compliance officer renders a verdict.',
    tool: 'Graph interrupt',
    emits: 'human_approved',
    icon: UserCheck,
    x: CHECKPOINT.x,
    y: CHECKPOINT.y,
    r: 30,
    kind: 'core',
  },
  {
    id: 'summary',
    code: 'RPT',
    name: 'Report Writer',
    short: 'Report',
    role: 'Executive output',
    blurb:
      'Renders the approved (or cancelled) assessment into the executive markdown report and closes the run out to the audit ledger.',
    tool: 'Report composer',
    emits: 'final_report',
    icon: ScrollText,
    x: REPORT.x,
    y: REPORT.y,
    r: 30,
    kind: 'core',
  },
];

const AGENT_BY_ID = new Map(AGENTS.map((agent) => [agent.id, agent]));
const SPECIALISTS = AGENTS.filter((agent) => agent.kind === 'specialist');
const ANALYSTS = AGENTS.filter((agent) => agent.kind === 'analyst');

/** `cancelled` shares the report slot — a rejected run still produces a document. */
const NODE_ALIASES: Record<string, string> = { cancelled: 'summary' };

const PHASE_LABEL: Record<AgentPhase, string> = {
  idle: 'standby',
  active: 'running',
  complete: 'complete',
  error: 'failed',
  waiting: 'awaiting officer',
  skipped: 'not engaged',
};

/**
 * Reserved slot holding how far the current stream's clock is shifted. Resuming
 * past the human checkpoint opens a second stream that restarts at T+0, so its
 * timings are rebased onto the end of the first one — otherwise the checkpoint
 * and report would draw on top of the specialists in the concurrency trace.
 */
const CLOCK_KEY = '__clock';

function clockOffset(runtime: AgentRuntime): number {
  return runtime[CLOCK_KEY]?.startedAt ?? 0;
}

export function initialAgentRuntime(): AgentRuntime {
  return {
    ...Object.fromEntries(AGENTS.map((agent) => [agent.id, { phase: 'idle' as AgentPhase }])),
    [CLOCK_KEY]: { phase: 'idle' as AgentPhase, startedAt: 0 },
  };
}

/** Folds one telemetry frame into the runtime map the graph renders from. */
export function reduceAgentEvent(runtime: AgentRuntime, event: AgentEvent): AgentRuntime {
  if (event.type === 'run.start') {
    if (!event.resumed) return initialAgentRuntime();
    // A resumed run keeps everything the specialists already produced.
    const elapsed = Math.max(0, ...AGENTS.map((agent) => runtime[agent.id]?.endedAt ?? 0));
    return { ...runtime, [CLOCK_KEY]: { phase: 'idle', startedAt: elapsed } };
  }

  if (event.type === 'node.start') {
    const id = NODE_ALIASES[event.node] ?? event.node;
    if (!AGENT_BY_ID.has(id)) return runtime;
    return {
      ...runtime,
      [id]: {
        phase: 'active',
        startedAt: event.at + clockOffset(runtime),
        tickingSince: performance.now(),
      },
    };
  }

  if (event.type === 'node.end') {
    const id = NODE_ALIASES[event.node] ?? event.node;
    if (!AGENT_BY_ID.has(id)) return runtime;
    const previous = runtime[id] ?? { phase: 'idle' as AgentPhase };
    const endedAt = event.at + clockOffset(runtime);
    return {
      ...runtime,
      [id]: {
        ...previous,
        phase: event.error ? 'error' : 'complete',
        endedAt,
        durationMs: event.duration_ms ?? (previous.startedAt != null ? endedAt - previous.startedAt : null),
        log: event.log || previous.log,
        error: event.error,
        tickingSince: undefined,
      },
    };
  }

  return runtime;
}

/**
 * Marks the specialists the supervisor chose not to engage.
 *
 * Called as soon as the plan is known — before any telemetry arrives — so the
 * topology shows the reduced scope from the start rather than leaving skipped
 * agents looking as though they are still pending.
 */
export function applyPlan(runtime: AgentRuntime, decisions: RoutingDecision[]): AgentRuntime {
  const next: AgentRuntime = { ...runtime };
  for (const decision of decisions) {
    if (decision.engaged || !AGENT_BY_ID.has(decision.agent)) continue;
    next[decision.agent] = { phase: 'skipped', log: decision.reason };
  }
  return next;
}

/**
 * Resolves the runtime once a stream closes: LangGraph stops emitting at the
 * human interrupt, so the checkpoint has to be settled from the final payload.
 */
export function settleAgentRuntime(runtime: AgentRuntime, investigation: InvestigationData): AgentRuntime {
  const next: AgentRuntime = { ...runtime };
  for (const agent of AGENTS) {
    const entry = next[agent.id];
    if (entry?.phase === 'active') next[agent.id] = { ...entry, phase: 'complete', tickingSince: undefined };
  }

  // Skipped specialists keep their state; the plan already explained them.
  for (const decision of investigation.plan?.decisions ?? []) {
    if (!decision.engaged && AGENT_BY_ID.has(decision.agent)) {
      next[decision.agent] = { phase: 'skipped', log: decision.reason };
    }
  }

  const decided = investigation.human_approved === true || investigation.human_approved === false;
  if (!decided) {
    next.human_approval = { ...next.human_approval, phase: 'waiting' };
    return next;
  }

  if (next.human_approval?.phase !== 'complete') {
    next.human_approval = { ...next.human_approval, phase: 'complete' };
  }
  if (investigation.final_report && next.summary?.phase !== 'complete') {
    next.summary = { ...next.summary, phase: 'complete' };
  }
  return next;
}

function edgePath(x1: number, y1: number, x2: number, y2: number) {
  const dx = (x2 - x1) * 0.46;
  return `M ${x1} ${y1} C ${x1 + dx} ${y1}, ${x2 - dx} ${y2}, ${x2} ${y2}`;
}

function verticalPath(x: number, y1: number, y2: number) {
  const dy = (y2 - y1) * 0.5;
  return `M ${x} ${y1} C ${x} ${y1 + dy}, ${x} ${y2 - dy}, ${x} ${y2}`;
}

interface EdgeDef {
  id: string;
  d: string;
  from: string;
  to: string;
}

const EDGES: EdgeDef[] = [
  ...SPECIALISTS.map((agent) => ({
    id: `fan-${agent.id}`,
    d: edgePath(ORIGIN.x + 24, ORIGIN.y, agent.x - agent.r - 6, agent.y),
    from: 'origin',
    to: agent.id,
  })),
  ...SPECIALISTS.flatMap((agent) =>
    ANALYSTS.map((analyst) => ({
      id: `tier2-${agent.id}-${analyst.id}`,
      d: edgePath(agent.x + agent.r + 6, agent.y, analyst.x - analyst.r - 6, analyst.y),
      from: agent.id,
      to: analyst.id,
    })),
  ),
  ...ANALYSTS.map((analyst) => ({
    id: `join-${analyst.id}`,
    d: edgePath(analyst.x + analyst.r + 6, analyst.y, SUPERVISOR.x - 46, SUPERVISOR.y),
    from: analyst.id,
    to: 'supervisor',
  })),
  {
    id: 'review',
    d: edgePath(SUPERVISOR.x + 46, SUPERVISOR.y, CRITIC.x - 40, CRITIC.y),
    from: 'supervisor',
    to: 'critic',
  },
  {
    id: 'verdict',
    d: edgePath(CRITIC.x + 40, CRITIC.y, CHECKPOINT.x - 36, CHECKPOINT.y),
    from: 'critic',
    to: 'human_approval',
  },
  {
    id: 'release',
    d: verticalPath(CHECKPOINT.x, CHECKPOINT.y + 36, REPORT.y - 36),
    from: 'human_approval',
    to: 'summary',
  },
];

function formatDuration(ms?: number | null) {
  if (ms == null) return '—';
  if (ms < 1000) return `${Math.round(ms)}ms`;
  return `${(ms / 1000).toFixed(ms < 10000 ? 2 : 1)}s`;
}

/** Highest number of specialists that were mid-flight at the same instant. */
function peakParallelism(runtime: AgentRuntime) {
  const spans = SPECIALISTS.map((agent) => runtime[agent.id])
    .filter((entry): entry is NodeRuntime => entry?.startedAt != null)
    .map((entry) => ({ start: entry.startedAt as number, end: entry.endedAt ?? Infinity }));
  if (!spans.length) return 0;

  let peak = 0;
  for (const probe of spans) {
    const overlapping = spans.filter((span) => span.start <= probe.start && span.end > probe.start).length;
    peak = Math.max(peak, overlapping);
  }
  return peak;
}

interface AgentConsoleProps {
  runtime: AgentRuntime;
  running: boolean;
  investigation: InvestigationData | null;
  subjectLabel: string;
  /** Gateway spend per agent, keyed by node id. */
  usage?: Record<string, AgentUsage>;
  onInspectEvidence?: (tab: 'research' | 'financials' | 'osint' | 'compliance' | 'logs') => void;
}

export function AgentConsole({
  runtime,
  running,
  investigation,
  subjectLabel,
  usage,
  onInspectEvidence,
}: AgentConsoleProps) {
  const reduceMotion = useReducedMotion();
  // Null until the operator picks a node themselves; until then the panel
  // follows whichever agent is currently working.
  const [picked, setPicked] = useState<string | null>(null);
  const [hovered, setHovered] = useState<string | null>(null);
  const [now, setNow] = useState(() => performance.now());
  const tickRef = useRef<number | null>(null);

  const anyActive = AGENTS.some((agent) => runtime[agent.id]?.phase === 'active');

  // Drive the live millisecond counters only while something is actually running.
  useEffect(() => {
    if (!anyActive) {
      if (tickRef.current) window.clearInterval(tickRef.current);
      tickRef.current = null;
      return;
    }
    tickRef.current = window.setInterval(() => setNow(performance.now()), 90);
    return () => {
      if (tickRef.current) window.clearInterval(tickRef.current);
      tickRef.current = null;
    };
  }, [anyActive]);

  const latestActive = AGENTS.filter((agent) => runtime[agent.id]?.phase === 'active').sort(
    (a, b) => (runtime[b.id].startedAt ?? 0) - (runtime[a.id].startedAt ?? 0),
  )[0]?.id;

  const liveDuration = (agent: AgentDef) => {
    const entry = runtime[agent.id];
    if (!entry) return null;
    if (entry.phase === 'active' && entry.tickingSince != null) {
      return Math.max(0, now - entry.tickingSince);
    }
    return entry.durationMs ?? null;
  };

  const timeline = useMemo(() => {
    const rows = AGENTS.map((agent) => {
      const entry = runtime[agent.id];
      const start = entry?.startedAt ?? null;
      const live = entry?.phase === 'active' && entry.tickingSince != null
        ? Math.max(0, now - entry.tickingSince)
        : null;
      const end = entry?.endedAt ?? (start != null && live != null ? start + live : null);
      return { agent, start, end, phase: entry?.phase ?? 'idle' };
    }).filter((row) => row.start != null);

    const span = Math.max(100, ...rows.map((row) => row.end ?? 0));
    return { rows, span };
    // `now` is what keeps the in-flight bars growing between telemetry frames.
  }, [runtime, now]);

  const completedSpecialists = SPECIALISTS.filter(
    (agent) => runtime[agent.id]?.phase === 'complete',
  ).length;
  // The denominator is what was engaged, not the roster size: 3/3 on a reduced
  // plan is complete, and showing 3/4 would read as a failure.
  const engagedSpecialists =
    SPECIALISTS.length - SPECIALISTS.filter((a) => runtime[a.id]?.phase === 'skipped').length;
  const peak = peakParallelism(runtime);

  const runState: { label: string; tone: string } = (() => {
    if (running || anyActive) return { label: 'agents executing', tone: 'is-running' };
    if (runtime.human_approval?.phase === 'waiting') return { label: 'awaiting officer', tone: 'is-waiting' };
    if (investigation?.final_report) return { label: 'run closed', tone: 'is-complete' };
    if (investigation) return { label: 'evidence held', tone: 'is-complete' };
    return { label: 'standby', tone: 'is-idle' };
  })();

  const activeSet = new Set(
    AGENTS.filter((agent) => runtime[agent.id]?.phase === 'active').map((agent) => agent.id),
  );
  const doneSet = new Set(
    AGENTS.filter((agent) => runtime[agent.id]?.phase === 'complete').map((agent) => agent.id),
  );

  const edgeState = (edge: EdgeDef): 'idle' | 'flowing' | 'charged' => {
    if (edge.from === 'origin') {
      if (activeSet.has(edge.to)) return 'flowing';
      return doneSet.has(edge.to) ? 'charged' : 'idle';
    }
    if (activeSet.has(edge.to)) return 'flowing';
    if (doneSet.has(edge.from) && doneSet.has(edge.to)) return 'charged';
    if (doneSet.has(edge.from)) return 'charged';
    return 'idle';
  };

  const focus = AGENT_BY_ID.get(hovered ?? picked ?? latestActive ?? 'supervisor') ?? AGENTS[4];
  // Only the agents that actually call a model appear in the ledger; the rest
  // simply render without these rows rather than showing zeroes.
  const focusUsage = usage?.[focus.id];
  const focusRuntime = runtime[focus.id] ?? { phase: 'idle' as AgentPhase };
  const FocusIcon = focus.icon;

  const feed = useMemo(() => {
    const entries = AGENTS.map((agent) => ({ agent, entry: runtime[agent.id] }))
      .filter((row) => row.entry?.log)
      .sort((a, b) => (a.entry?.endedAt ?? 0) - (b.entry?.endedAt ?? 0));
    return entries.slice(-4);
  }, [runtime]);

  return (
    <section className="agent-console panel cornered" aria-labelledby="agent-console-title" data-testid="section-agent-console">
      <div className="section-heading agent-console-heading">
        <div>
          <div className="eyebrow">/ live orchestration</div>
          <h2 id="agent-console-title">Agent execution topology</h2>
        </div>
        <div className="console-stats">
          <div className={`console-state ${runState.tone}`}>
            <i aria-hidden="true" />
            {runState.label}
          </div>
          <div className="console-metric">
            <span>specialists</span>
            <strong data-testid="text-specialists-complete">
              {completedSpecialists}/{engagedSpecialists}
            </strong>
          </div>
          <div className="console-metric">
            <span>peak parallel</span>
            <strong>×{peak || '—'}</strong>
          </div>
        </div>
      </div>

      {investigation?.plan && investigation.plan.strategy === 'reduced' && (
        <div className="plan-notice" role="status" data-testid="notice-reduced-plan">
          <GitBranch size={14} strokeWidth={1.9} />
          <div>
            <strong>Reduced scope</strong>
            <span>
              {investigation.plan.skipped
                .map((decision) => `${decision.agent.replace(/_/g, ' ')} — ${decision.reason}`)
                .join(' · ')}
            </span>
          </div>
        </div>
      )}

      <div className="agent-console-grid">
        <div className="graph-stage">
          <svg
            className="agent-graph"
            viewBox={`0 0 ${STAGE_W} ${STAGE_H}`}
            role="img"
            aria-label={`Execution topology for ${subjectLabel}. ${completedSpecialists} of 4 specialist agents complete.`}
          >
            <defs>
              <linearGradient id="edge-live" x1="0" x2="1">
                <stop offset="0%" stopColor="var(--orange)" stopOpacity="0.15" />
                <stop offset="55%" stopColor="var(--orange)" stopOpacity="0.95" />
                <stop offset="100%" stopColor="var(--gold)" stopOpacity="0.5" />
              </linearGradient>
              <radialGradient id="core-glow">
                <stop offset="0%" stopColor="rgba(247,147,26,.42)" />
                <stop offset="100%" stopColor="rgba(247,147,26,0)" />
              </radialGradient>
            </defs>

            <g className="graph-grid" aria-hidden="true">
              {Array.from({ length: 9 }, (_, index) => (
                <path key={`h-${index}`} d={`M 0 ${index * 58} H ${STAGE_W}`} />
              ))}
              {Array.from({ length: 18 }, (_, index) => (
                <path key={`v-${index}`} d={`M ${index * 58} 0 V ${STAGE_H}`} />
              ))}
            </g>

            {EDGES.map((edge) => {
              const state = edgeState(edge);
              return (
                <g key={edge.id} className={`graph-edge is-${state}`}>
                  <path id={edge.id} d={edge.d} className="edge-line" />
                  {state === 'flowing' && !reduceMotion && (
                    <circle className="edge-packet" r="3.4">
                      <animateMotion dur="1.5s" repeatCount="indefinite" keyPoints="0;1" keyTimes="0;1" calcMode="linear">
                        <mpath href={`#${edge.id}`} />
                      </animateMotion>
                    </circle>
                  )}
                </g>
              );
            })}

            <g className="graph-origin" aria-hidden="true">
              <rect x={ORIGIN.x - 22} y={ORIGIN.y - 22} width="44" height="44" transform={`rotate(45 ${ORIGIN.x} ${ORIGIN.y})`} />
              <g transform={`translate(${ORIGIN.x - 9} ${ORIGIN.y - 9})`}>
                <Crosshair size={18} strokeWidth={1.6} />
              </g>
              <text x={ORIGIN.x} y={ORIGIN.y + 48} textAnchor="middle" className="origin-label">
                TARGET
              </text>
              <text x={ORIGIN.x} y={ORIGIN.y + 62} textAnchor="middle" className="origin-sub">
                fan-out ×4
              </text>
            </g>

            {AGENTS.map((agent) => {
              const entry = runtime[agent.id] ?? { phase: 'idle' as AgentPhase };
              const Icon = agent.icon;
              const isFocus = focus.id === agent.id;
              const duration = liveDuration(agent);
              return (
                <g
                  key={agent.id}
                  className={`graph-node ${agent.kind}-node is-${entry.phase} ${isFocus ? 'is-focus' : ''}`}
                  tabIndex={0}
                  role="button"
                  aria-label={`${agent.name}: ${PHASE_LABEL[entry.phase]}`}
                  onMouseEnter={() => setHovered(agent.id)}
                  onMouseLeave={() => setHovered(null)}
                  onFocus={() => setHovered(agent.id)}
                  onBlur={() => setHovered(null)}
                  onClick={() => setPicked(agent.id)}
                  onKeyDown={(event) => {
                    if (event.key === 'Enter' || event.key === ' ') {
                      event.preventDefault();
                      setPicked(agent.id);
                    }
                  }}
                  data-testid={`node-${agent.id}`}
                >
                  <circle className="node-aura" cx={agent.x} cy={agent.y} r={agent.r + 16} />
                  <circle className="node-ring" cx={agent.x} cy={agent.y} r={agent.r} />
                  {entry.phase === 'active' && (
                    <circle
                      className="node-scan"
                      cx={agent.x}
                      cy={agent.y}
                      r={agent.r + 8}
                      style={{ transformOrigin: `${agent.x}px ${agent.y}px` }}
                    />
                  )}
                  <circle className="node-core" cx={agent.x} cy={agent.y} r={agent.r - 11} />
                  <g className="node-icon" transform={`translate(${agent.x - 9} ${agent.y - 9})`}>
                    <Icon size={18} strokeWidth={1.7} />
                  </g>
                  <text className="node-code" x={agent.x} y={agent.y - agent.r - 11} textAnchor="middle">
                    {agent.code}
                  </text>
                  <text className="node-name" x={agent.x} y={agent.y + agent.r + 21} textAnchor="middle">
                    {agent.name}
                  </text>
                  <text className="node-status" x={agent.x} y={agent.y + agent.r + 36} textAnchor="middle">
                    {PHASE_LABEL[entry.phase]}
                    {duration != null ? ` · ${formatDuration(duration)}` : ''}
                  </text>
                </g>
              );
            })}
          </svg>

          <div className="graph-caption">
            <span>
              <Activity size={12} /> {subjectLabel}
            </span>
            <span className="graph-legend">
              <span><i className="legend-dot idle" /> standby</span>
              <span><i className="legend-dot active" /> running</span>
              <span><i className="legend-dot complete" /> complete</span>
              <span><i className="legend-dot waiting" /> checkpoint</span>
            </span>
          </div>
        </div>

        <aside className="agent-inspector" aria-live="polite">
          <div className="inspector-topline">
            <span className="inspector-code">{focus.code}</span>
            <span className={`inspector-status is-${focusRuntime.phase}`}>
              <i aria-hidden="true" />
              {PHASE_LABEL[focusRuntime.phase]}
            </span>
          </div>

          <div className="inspector-title">
            <span className="inspector-glyph" aria-hidden="true">
              <FocusIcon size={17} strokeWidth={1.6} />
            </span>
            <div>
              <h3>{focus.name}</h3>
              <div className="inspector-role">{focus.role}</div>
            </div>
          </div>

          <p>{focus.blurb}</p>

          <dl className="inspector-meta">
            <div>
              <dt>Instrument</dt>
              <dd>{focus.tool}</dd>
            </div>
            <div>
              <dt>Writes</dt>
              <dd>{focus.emits}</dd>
            </div>
            <div>
              <dt>Started</dt>
              <dd>{focusRuntime.startedAt != null ? `T+${formatDuration(focusRuntime.startedAt)}` : '—'}</dd>
            </div>
            <div>
              <dt>Duration</dt>
              <dd>{formatDuration(liveDuration(focus))}</dd>
            </div>

            {focusUsage && (
              <>
                <div>
                  <dt>Model</dt>
                  <dd title={`${focusUsage.provider} · ${focusUsage.model}`}>
                    {focusUsage.model.split('/').pop()}
                  </dd>
                </div>
                <div>
                  <dt>Tokens</dt>
                  <dd>
                    {focusUsage.tokens.toLocaleString()}
                    <em className="usage-cost"> · ${focusUsage.cost_usd.toFixed(6)}</em>
                  </dd>
                </div>
              </>
            )}
          </dl>

          {focusRuntime.error ? (
            <div className="inspector-note is-error">{focusRuntime.error}</div>
          ) : focusRuntime.log ? (
            <div className="inspector-note">{focusRuntime.log}</div>
          ) : (
            <div className="inspector-note is-quiet">
              {focusRuntime.phase === 'active' ? 'Working — telemetry lands on completion.' : 'No telemetry recorded yet.'}
            </div>
          )}

          {focus.tab && onInspectEvidence && (
            <button
              type="button"
              className="inspector-action focus-ring"
              onClick={() => onInspectEvidence(focus.tab!)}
              disabled={!investigation}
              data-testid={`button-inspect-${focus.id}`}
            >
              <span>Open {focus.short.toLowerCase()} evidence</span>
              <ChevronRight size={14} />
            </button>
          )}
        </aside>
      </div>

      <div className="agent-timeline">
        <div className="timeline-heading">
          <span>Concurrency trace</span>
          <span className="timeline-span">
            {timeline.rows.length ? `${formatDuration(timeline.span)} agent compute` : 'no run recorded'}
          </span>
        </div>

        {timeline.rows.length === 0 ? (
          <div className="timeline-empty">
            Launch an investigation to watch the four specialists execute against a shared clock.
          </div>
        ) : (
          <div className="timeline-rows">
            {timeline.rows.map(({ agent, start, end, phase }) => {
              const left = ((start ?? 0) / timeline.span) * 100;
              const width = Math.max(1.2, (((end ?? 0) - (start ?? 0)) / timeline.span) * 100);
              return (
                <div className="timeline-row" key={agent.id}>
                  <span className="timeline-label">{agent.short}</span>
                  <div className="timeline-track">
                    <motion.span
                      className={`timeline-bar is-${phase}`}
                      style={{ left: `${left}%` }}
                      animate={{ width: `${width}%` }}
                      transition={{ duration: reduceMotion ? 0 : 0.18, ease: 'linear' }}
                    />
                  </div>
                  <span className="timeline-time">{formatDuration((end ?? 0) - (start ?? 0))}</span>
                </div>
              );
            })}
          </div>
        )}
      </div>

      <div className="agent-feed" aria-live="polite">
        <AnimatePresence initial={false}>
          {feed.length === 0 ? (
            <motion.div
              key="feed-empty"
              className="feed-line is-quiet"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
            >
              <Gavel size={11} /> Telemetry channel open. No agent has reported yet.
            </motion.div>
          ) : (
            feed.map(({ agent, entry }) => (
              <motion.div
                key={`${agent.id}-${entry?.endedAt ?? 0}`}
                className="feed-line"
                initial={{ opacity: 0, x: -8 }}
                animate={{ opacity: 1, x: 0 }}
                exit={{ opacity: 0 }}
                transition={{ duration: reduceMotion ? 0 : 0.25 }}
              >
                <span className="feed-stamp">T+{formatDuration(entry?.endedAt)}</span>
                <span className="feed-code">{agent.code}</span>
                <span className="feed-text">{entry?.log}</span>
              </motion.div>
            ))
          )}
        </AnimatePresence>
      </div>
    </section>
  );
}
