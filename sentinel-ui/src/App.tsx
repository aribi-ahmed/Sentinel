import { useCallback, useEffect, useRef, useState, type FormEvent, type ReactNode } from 'react';
import { motion } from 'framer-motion';
import {
  Activity,
  AlertCircle,
  BookOpen,
  Check,
  ChevronRight,
  Database,
  Download,
  FileText,
  Globe,
  Lock,
  Network,
  Shield,
  Fingerprint,
  History,
  Share2,
  Terminal,
  Wrench,
  TrendingUp,
  X,
} from 'lucide-react';
import {
  fetchAuditHistory,
  fetchSystemStatus,
  reportUrl,
  streamApproval,
  streamInvestigation,
  type AgentEvent,
  type AuditRecord,
  type InvestigationData,
  type SystemStatus,
} from './services/api';
import { AssetsModal } from './components/AssetsModal';
import { OsintFindings } from './components/OsintFindings';
import { VerdictPanel } from './components/VerdictPanel';
import { ComplianceBriefPanel } from './components/ComplianceBriefPanel';
import { LedgerModal } from './components/LedgerModal';
import { FilingsPanel } from './components/FilingsPanel';
import { ToolAuditPanel } from './components/ToolAuditPanel';
import { CriticPanel } from './components/CriticPanel';
import { WelyneBrand } from './components/WelyneBrand';
import { SiteFooter } from './components/SiteFooter';
import { FraudPanel, HistoryPanel, NetworkPanel } from './components/IntelligencePanels';
import { parsePythonLiteral } from './lib/pythonLiteral';
import { cleanText } from './lib/text';
import {
  AgentConsole,
  applyPlan,
  initialAgentRuntime,
  reduceAgentEvent,
  settleAgentRuntime,
  type AgentRuntime,
} from './components/AgentConsole';
import './App.css';

type EvidenceTab =
  | 'research'
  | 'financials'
  | 'osint'
  | 'compliance'
  | 'fraud'
  | 'network'
  | 'history'
  | 'provenance'
  | 'logs';

const tabs: { id: EvidenceTab; label: string; icon: typeof FileText }[] = [
  { id: 'research', label: 'Research baseline', icon: FileText },
  { id: 'financials', label: 'Financials', icon: TrendingUp },
  { id: 'osint', label: 'OSINT findings', icon: Globe },
  { id: 'compliance', label: 'Compliance RAG', icon: BookOpen },
  { id: 'fraud', label: 'Fraud signals', icon: Fingerprint },
  { id: 'network', label: 'Entity network', icon: Share2 },
  { id: 'history', label: 'Entity history', icon: History },
  { id: 'provenance', label: 'Tool provenance', icon: Wrench },
  { id: 'logs', label: 'Audit logs', icon: Terminal },
];

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function normalisePayload(value: unknown): unknown {
  if (typeof value !== 'string') return value;
  const trimmed = value.trim();

  // 1. Direct standard JSON parse
  try {
    return JSON.parse(trimmed);
  } catch {
    /* continue */
  }

  // 2. Python repr — apostrophes and braces inside snippets defeat the regex
  //    rewrite below, so try a real literal parse first.
  const literal = parsePythonLiteral(trimmed);
  if (literal !== undefined) return literal;

  // 3. Legacy regex rewrite, kept for payloads the parser rejects
  if (
    (trimmed.startsWith('[') && trimmed.endsWith(']')) ||
    (trimmed.startsWith('{') && trimmed.endsWith('}'))
  ) {
    try {
      let jsonStr = trimmed
        // Replace Python keys 'key': with "key":
        .replace(/'([a-zA-Z0-9_]+)'\s*:/g, '"$1":')
        // Replace Python string values : 'val' with : "val"
        .replace(/:\s*'([^']*)'/g, ': "$1"')
        // Handle Python booleans and None
        .replace(/:\s*True/gi, ': true')
        .replace(/:\s*False/gi, ': false')
        .replace(/:\s*None/gi, ': null');

      return JSON.parse(jsonStr);
    } catch {
      /* continue */
    }
  }

  return value;
}

function formatLabel(label: string) {
  return label
    .replace(/([a-z])([A-Z])/g, '$1 $2')
    .replace(/[_-]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function formatScalar(value: unknown): string {
  if (value === null || value === undefined || value === '') return 'Not provided';
  if (typeof value === 'boolean') return value ? 'Yes' : 'No';
  if (typeof value === 'number') return new Intl.NumberFormat('en-US', { maximumFractionDigits: 2 }).format(value);
  return cleanText(String(value));
}

function splitNarrative(value: string) {
  return cleanText(value)
    .split(/\n{2,}/)
    .map((part) => part.trim())
    .filter(Boolean);
}

function HumanValue({ value }: { value: unknown }) {
  const normalised = normalisePayload(value);
  if (Array.isArray(normalised)) {
    return (
      <div className="nested-list">
        {normalised.map((item, index) => (
          <div className="nested-item" key={index}>
            <HumanValue value={item} />
          </div>
        ))}
      </div>
    );
  }
  if (isRecord(normalised)) {
    return <HumanizedData value={normalised} compact />;
  }
  if (typeof normalised === 'string') {
    return <span className="human-value narrative-value">{cleanText(normalised)}</span>;
  }
  return <span className="human-value">{formatScalar(normalised)}</span>;
}

export function HumanizedData({ value, compact = false }: { value: unknown; compact?: boolean }) {
  const normalised = normalisePayload(value);

  if (Array.isArray(normalised)) {
    return (
      <div className={`evidence-stack ${compact ? 'is-compact' : ''}`}>
        {normalised.map((item, index) => (
          <article className="evidence-card" key={index}>
            <div className="evidence-card-index">Evidence {String(index + 1).padStart(2, '0')}</div>
            <HumanizedData value={item} compact />
          </article>
        ))}
      </div>
    );
  }

  if (isRecord(normalised) && ('query' in normalised || 'results' in normalised)) {
    return <OsintFindings value={normalised} />;
  }

  if (isRecord(normalised)) {
    const entries = Object.entries(normalised).filter(
      ([, item]) => item !== null && item !== undefined && item !== ''
    );
    return entries.length ? (
      <dl className={`field-grid ${compact ? 'is-compact' : ''}`}>
        {entries.map(([key, item]) => (
          <div
            className={`field-row ${isRecord(item) || Array.isArray(item) ? 'is-wide' : ''}`}
            key={key}
          >
            <dt>{formatLabel(key)}</dt>
            <dd>
              <HumanValue value={item} />
            </dd>
          </div>
        ))}
      </dl>
    ) : (
      <EmptyData label="No readable fields returned." />
    );
  }

  if (typeof normalised === 'string') {
    const paragraphs = splitNarrative(normalised);
    return (
      <div className="human-copy">
        {paragraphs.map((paragraph, index) => (
          <p key={index}>{paragraph}</p>
        ))}
      </div>
    );
  }

  return (
    <div className="human-copy">
      <p>{formatScalar(normalised)}</p>
    </div>
  );
}

function MarkdownInline({ text }: { text: string }) {
  const tokenPattern = /(\*\*[^*]+\*\*|__[^_]+__|`[^`]+`|\*[^*]+\*|_[^_]+_|\[[^\]]+\]\([^)]+\))/g;
  const parts = text.split(tokenPattern).filter(Boolean);
  return (
    <>
      {parts.map((part, index) => {
        if ((part.startsWith('**') && part.endsWith('**')) || (part.startsWith('__') && part.endsWith('__'))) {
          return <strong key={index}>{part.slice(2, -2)}</strong>;
        }
        if (part.startsWith('`') && part.endsWith('`')) {
          return <code key={index}>{part.slice(1, -1)}</code>;
        }
        if ((part.startsWith('*') && part.endsWith('*')) || (part.startsWith('_') && part.endsWith('_'))) {
          return <em key={index}>{part.slice(1, -1)}</em>;
        }
        const link = part.match(/^\[([^\]]+)\]\((https?:\/\/[^)]+)\)$/);
        if (link) {
          return <a key={index} href={link[2]} target="_blank" rel="noreferrer">{link[1]}</a>;
        }
        return <span key={index}>{part}</span>;
      })}
    </>
  );
}

function MarkdownHeading({ level, children }: { level: number; children: ReactNode }) {
  if (level === 1) return <h1>{children}</h1>;
  if (level === 2) return <h2>{children}</h2>;
  if (level === 3) return <h3>{children}</h3>;
  if (level === 4) return <h4>{children}</h4>;
  if (level === 5) return <h5>{children}</h5>;
  return <h6>{children}</h6>;
}

function MarkdownReport({ content }: { content: string }) {
  const blocks: ReactNode[] = [];
  const lines = content.replace(/\r/g, '').split('\n');
  let paragraph: string[] = [];
  let unordered: string[] = [];
  let ordered: string[] = [];

  const flushParagraph = () => {
    if (paragraph.length) {
      blocks.push(<p key={`paragraph-${blocks.length}`}><MarkdownInline text={paragraph.join(' ')} /></p>);
      paragraph = [];
    }
  };
  const flushLists = () => {
    if (unordered.length) {
      blocks.push(<ul key={`unordered-${blocks.length}`}>{unordered.map((item, index) => <li key={index}><MarkdownInline text={item} /></li>)}</ul>);
      unordered = [];
    }
    if (ordered.length) {
      blocks.push(<ol key={`ordered-${blocks.length}`}>{ordered.map((item, index) => <li key={index}><MarkdownInline text={item} /></li>)}</ol>);
      ordered = [];
    }
  };
  const flushAll = () => {
    flushParagraph();
    flushLists();
  };

  lines.forEach((line) => {
    const trimmed = line.trim();
    if (!trimmed) {
      flushAll();
      return;
    }
    const heading = trimmed.match(/^(#{1,6})\s+(.+)$/);
    if (heading) {
      flushAll();
      blocks.push(
        <MarkdownHeading key={`heading-${blocks.length}`} level={heading[1].length}>
          <MarkdownInline text={heading[2]} />
        </MarkdownHeading>,
      );
      return;
    }
    if (/^(---+|\*\*\*+|___+)$/.test(trimmed)) {
      flushAll();
      blocks.push(<hr key={`rule-${blocks.length}`} />);
      return;
    }
    const bullet = trimmed.match(/^[-*+]\s+(.+)$/);
    if (bullet) {
      flushParagraph();
      if (ordered.length) flushLists();
      unordered.push(bullet[1]);
      return;
    }
    const number = trimmed.match(/^\d+\.\s+(.+)$/);
    if (number) {
      flushParagraph();
      if (unordered.length) flushLists();
      ordered.push(number[1]);
      return;
    }
    if (trimmed.startsWith('>')) {
      flushAll();
      blocks.push(<blockquote key={`quote-${blocks.length}`}><MarkdownInline text={trimmed.replace(/^>\s?/, '')} /></blockquote>);
      return;
    }
    paragraph.push(trimmed);
  });
  flushAll();
  return <div className="markdown-report">{blocks}</div>;
}

function hasEvidence(value: unknown) {
  const normalised = normalisePayload(value);
  if (Array.isArray(normalised)) return normalised.length > 0;
  if (isRecord(normalised)) return Object.keys(normalised).length > 0;
  return typeof normalised === 'string' ? normalised.trim().length > 0 : normalised !== null && normalised !== undefined;
}

// Bands the scoring engine can return, ordered from least to most severe.
const RISK_TONE: Record<string, string> = {
  MINIMAL: 'safe',
  LOW: 'safe',
  CLEAR: 'safe',
  APPROVED: 'safe',
  MODERATE: 'watch',
  ELEVATED: 'alert',
  SEVERE: 'critical',
};

function RiskBadge({ level, score }: { level?: string; score?: number }) {
  const band = (level || 'PENDING').toUpperCase();
  const tone = RISK_TONE[band] ?? 'alert';
  return (
    <div className={`risk-badge is-${tone}`} data-testid="status-risk-assessment">
      <span className="risk-dot" aria-hidden="true" />
      <span>Risk assessment</span>
      <strong>{band}</strong>
      {typeof score === 'number' && <b className="risk-score">{Math.round(score)}<i>/100</i></b>}
    </div>
  );
}

function App() {
  const [ticker, setTicker] = useState('TSLA');
  const [subjectName, setSubjectName] = useState('Tesla Inc');
  const [loading, setLoading] = useState(false);
  const [activeTab, setActiveTab] = useState<EvidenceTab>('research');
  const [investigation, setInvestigation] = useState<InvestigationData | null>(null);
  const [history, setHistory] = useState<AuditRecord[]>([]);
  const [showHistory, setShowHistory] = useState(false);
  const [showAssets, setShowAssets] = useState(false);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [agentRuntime, setAgentRuntime] = useState<AgentRuntime>(() => initialAgentRuntime());
  const [system, setSystem] = useState<SystemStatus | null>(null);

  // Read at startup for the durability badge, and again after each run so the
  // per-agent token figures reflect the work just done.
  const refreshSystem = useCallback(() => {
    fetchSystemStatus().then(setSystem).catch(() => setSystem(null));
  }, []);

  useEffect(refreshSystem, [refreshSystem]);
  const evidenceRef = useRef<HTMLElement | null>(null);

  const handleAgentEvent = useCallback((event: AgentEvent) => {
    setAgentRuntime((previous) => reduceAgentEvent(previous, event));
  }, []);

  const focusEvidence = useCallback((tab: EvidenceTab) => {
    setActiveTab(tab);
    evidenceRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }, []);

  const loadHistory = async () => {
    setHistoryLoading(true);
    setHistoryError(null);
    try {
      const records = await fetchAuditHistory();
      setHistory(Array.isArray(records) ? records : []);
    } catch (err) {
      setHistoryError(err instanceof Error ? err.message : 'Ledger is unreachable.');
    } finally {
      setHistoryLoading(false);
    }
  };

  const handleStart = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const cleanName = subjectName.trim();
    const cleanTicker = ticker.trim().toUpperCase();
    if (!cleanName || !cleanTicker) {
      setError('Target entity and ticker are required before agents can launch.');
      return;
    }
    setLoading(true);
    setError(null);
    setAgentRuntime(initialAgentRuntime());
    try {
      const data = await streamInvestigation(cleanName, cleanTicker, handleAgentEvent);
      setInvestigation(data);
      setAgentRuntime((previous) =>
        settleAgentRuntime(applyPlan(previous, data.plan?.decisions ?? []), data),
      );
      setActiveTab('research');
      void loadHistory();
      refreshSystem();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to trigger investigation.');
    } finally {
      setLoading(false);
    }
  };

  const handleApproval = async (approved: boolean) => {
    if (!investigation) return;
    setLoading(true);
    setError(null);
    try {
      const updated = await streamApproval(investigation.id, approved, handleAgentEvent);
      setInvestigation(updated);
      setAgentRuntime((previous) => settleAgentRuntime(previous, updated));
      void loadHistory();
      refreshSystem();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to process officer decision.');
    } finally {
      setLoading(false);
    }
  };

  // The download links hit the database, so they only work once the record has
  // been committed with a report.
  const reportReady = Boolean(investigation?.final_report) && Boolean(investigation?.id);

  const showCheckpoint =
    Boolean(investigation) &&
    investigation?.status !== 'completed' &&
    investigation?.status !== 'failed' &&
    investigation?.human_approved !== true &&
    investigation?.human_approved !== false;

  const renderEvidence = () => {
    if (!investigation) return null;
    if (activeTab === 'research') {
      const hasBaseline = hasEvidence(investigation.research_data);
      if (!hasBaseline && !investigation.edgar_data) {
        return <EmptyData label="No research baseline returned." />;
      }
      return (
        <div data-testid="data-research">
          <FilingsPanel profile={investigation.edgar_data} />
          {hasBaseline && <HumanizedData value={investigation.research_data} />}
        </div>
      );
    }
    if (activeTab === 'financials') {
      return hasEvidence(investigation.financial_data)
        ? <div data-testid="data-financials"><HumanizedData value={investigation.financial_data} /></div>
        : <EmptyData label="No financial evidence returned." />;
    }
    if (activeTab === 'osint') {
      return hasEvidence(investigation.news_data)
        ? <OsintFindings value={investigation.news_data} />
        : <EmptyData label="No OSINT findings returned." />;
    }
    if (activeTab === 'compliance') {
      return hasEvidence(investigation.compliance_data)
        ? <ComplianceBriefPanel value={investigation.compliance_data} />
        : <EmptyData label="No compliance obligations retrieved." />;
    }
    if (activeTab === 'fraud') {
      return <FraudPanel assessment={investigation.fraud_data} />;
    }
    if (activeTab === 'network') {
      return <NetworkPanel graph={investigation.graph_data} />;
    }
    if (activeTab === 'history') {
      return <HistoryPanel history={investigation.memory_data} />;
    }
    if (activeTab === 'provenance') {
      return <ToolAuditPanel audit={investigation.tool_audit} />;
    }
    return investigation.logs?.length ? (
      <div className="log-list" data-testid="data-logs">
        {investigation.logs.map((log, index) => <div key={`${log}-${index}`}>{log}</div>)}
      </div>
    ) : <EmptyData label="Execution sequence initialized. Awaiting agent telemetry." />;
  };

  return (
    <div className="sentinel-shell">
      <header className="topbar">
        <div className="container topbar-inner">
          <div className="brand-group">
            <WelyneBrand />
            <div className="brand-divider" aria-hidden="true" />
            <div className="brand" data-testid="text-brand">
              <div className="brand-mark" aria-hidden="true"><Shield size={17} strokeWidth={1.6} /></div>
              <div className="brand-name">SENTINEL <span>//</span> AI</div>
            </div>
          </div>
          <div className="topbar-right">
            <div className="api-status" data-testid="status-api">
              <span className="status-dot" aria-hidden="true" />
              <span>REST API</span>
              <strong>active</strong>
            </div>
            {system && (
              <div
                className={`api-status durability ${system.checkpointing.durable ? 'is-durable' : 'is-volatile'}`}
                title={system.checkpointing.detail}
                data-testid="status-durability"
              >
                <span className="status-dot" aria-hidden="true" />
                <span>State</span>
                <strong>
                  {system.checkpointing.durable
                    ? `durable · ${system.checkpointing.backend}`
                    : 'in-memory'}
                </strong>
              </div>
            )}
            <button
              type="button"
              className="ledger-toggle focus-ring"
              aria-expanded={showHistory}
              onClick={() => {
                const nextOpenState = !showHistory;
                setShowHistory(nextOpenState);
                if (nextOpenState) void loadHistory();
              }}
              data-testid="button-toggle-ledger"
            >
              <Database size={15} strokeWidth={1.5} />
              <span>{showHistory ? 'Close ledger' : 'SQL ledger'}</span>
            </button>
            <button
              type="button"
              className="ledger-toggle focus-ring"
              onClick={() => setShowAssets(true)}
              data-testid="button-open-assets"
            >
              <BookOpen size={15} strokeWidth={1.5} />
              <span>Docs &amp; datasets</span>
            </button>
          </div>
        </div>
      </header>

      <main className="container main">
        <section className="hero-grid animate-enter" aria-labelledby="page-title">
          <div className="hero-copy">
            <div className="eyebrow">// compliance intelligence protocol</div>
            <h1 id="page-title">Enterprise <em>risk</em> evaluator</h1>
            <p>
              Autonomous multi-agent synthesis across research, market signals, OSINT,
              and policy controls — with a human decision checkpoint before release.
            </p>
            <form className="launch-panel panel" onSubmit={handleStart} data-testid="form-launch-investigation">
              <div className="field">
                <label htmlFor="ticker">Ticker</label>
                <input
                  id="ticker"
                  value={ticker}
                  onChange={(event) => setTicker(event.target.value.toUpperCase())}
                  placeholder="TSLA"
                  autoComplete="off"
                  required
                  className="focus-ring"
                  data-testid="input-ticker"
                />
              </div>
              <div className="field">
                <label htmlFor="entity">Target entity</label>
                <input
                  id="entity"
                  value={subjectName}
                  onChange={(event) => setSubjectName(event.target.value)}
                  placeholder="Tesla Inc"
                  autoComplete="organization"
                  required
                  className="entity focus-ring"
                  data-testid="input-subject-name"
                />
              </div>
              <button type="submit" className="launch-button focus-ring" disabled={loading} data-testid="button-run-agents">
                {loading ? 'Analyzing…' : 'Run agents'}
                <ChevronRight size={15} aria-hidden="true" />
              </button>
            </form>
            {error && (
              <div className="error-banner" role="alert" data-testid="status-error">
                <AlertCircle size={14} style={{ verticalAlign: 'middle', marginRight: 8 }} />
                {error}
              </div>
            )}
          </div>
          <div className="hero-index" aria-label="System metadata">
            <div>MODE / <strong>HUMAN-IN-LOOP</strong></div>
            <div>AGENTS / <strong>04 SPECIALISTS</strong></div>
            <div>LEDGER / <strong>APPEND-ONLY</strong></div>
          </div>
        </section>

        <AgentConsole
          runtime={agentRuntime}
          running={loading}
          investigation={investigation}
          subjectLabel={
            investigation
              ? `${investigation.subject_name} · ${investigation.ticker || 'N/A'}`
              : `${subjectName.trim() || 'no target'} · ${ticker.trim().toUpperCase() || '—'}`
          }
          usage={system?.llm?.usage_since_startup.by_caller}
          onInspectEvidence={focusEvidence}
        />

        {investigation && (
          <motion.section
            className="investigation"
            initial={{ opacity: 0, y: 18 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: .45 }}
            aria-labelledby="investigation-title"
            data-testid="section-investigation"
          >
            <div className="target-card panel cornered">
              <div>
                <div className="target-meta">Target entity</div>
                <h2 id="investigation-title" className="target-title">
                  {investigation.subject_name} <span>({investigation.ticker || 'N/A'})</span>
                </h2>
                <div className="target-id">ID: {investigation.id || 'pending assignment'}</div>
              </div>
              <RiskBadge level={investigation.risk_level} score={investigation.risk_assessment?.score} />
            </div>

            <VerdictPanel
              assessment={investigation.risk_assessment}
              sanctions={investigation.sanctions_data}
              fallbackReasoning={investigation.supervisor_reasoning}
            />

            {/* Directly under the verdict, never behind a disclosure: an
                objection nobody sees is the same as no critic at all. */}
            <CriticPanel
              review={investigation.critic_review}
              confidence={investigation.confidence}
            />

            <section className="evidence" ref={evidenceRef} aria-labelledby="evidence-title">
              <div className="section-heading">
                <div>
                  <div className="eyebrow">/ evidence matrix</div>
                  <h2 id="evidence-title">Specialist evidence</h2>
                </div>
                <div className="record-count"><Network size={13} style={{ verticalAlign: 'middle', marginRight: 5 }} /> live synthesis</div>
              </div>
              <div className="tabs" role="tablist" aria-label="Investigation evidence">
                {tabs.map(({ id, label, icon: Icon }) => (
                  <button
                    type="button"
                    role="tab"
                    aria-selected={activeTab === id}
                    key={id}
                    className={`tab focus-ring ${activeTab === id ? 'active' : ''}`}
                    onClick={() => setActiveTab(id)}
                    data-testid={`tab-${id}`}
                  >
                    <Icon size={14} strokeWidth={1.5} />
                    {label}
                  </button>
                ))}
              </div>
              <div className="evidence-panel panel" role="tabpanel" data-testid={`panel-${activeTab}`}>
                <div className="panel-kicker">Human-readable specialist analysis / {activeTab}</div>
                {renderEvidence()}
              </div>
            </section>

            {showCheckpoint && (
              <section className="checkpoint panel" aria-labelledby="checkpoint-title" data-testid="section-human-checkpoint">
                <div className="checkpoint-copy">
                  <div className="eyebrow">// human compliance checkpoint</div>
                  <h2 id="checkpoint-title">Officer decision required</h2>
                  <p>Review the specialist findings above and render a final compliance judgment to advance this investigation.</p>
                </div>
                <div className="checkpoint-actions">
                  <button type="button" className="reject-button focus-ring" onClick={() => void handleApproval(false)} disabled={loading} data-testid="button-reject-assessment">
                    <X size={14} style={{ verticalAlign: 'middle', marginRight: 7 }} /> Reject assessment
                  </button>
                  <button type="button" className="decision-button focus-ring" onClick={() => void handleApproval(true)} disabled={loading} data-testid="button-approve-assessment">
                    <Check size={14} style={{ verticalAlign: 'middle', marginRight: 7 }} /> Approve &amp; generate report
                  </button>
                </div>
              </section>
            )}

            {(investigation.status === 'completed' || Boolean(investigation.final_report)) && (
              <section className="report panel cornered" aria-labelledby="report-title" data-testid="section-final-report">
                <div className="report-header">
                  <div>
                    <div className="eyebrow">// completed assessment</div>
                    <h2 id="report-title">Executive compliance report</h2>
                  </div>
                  <div className="report-actions">
                    {/* Served from the stored record, so these are the same bytes
                        the ledger hands back months later. */}
                    <a
                      className={`download-button focus-ring ${reportReady ? '' : 'is-disabled'}`}
                      href={reportReady ? reportUrl(investigation.id, 'md') : undefined}
                      aria-disabled={!reportReady}
                      data-testid="button-download-report"
                    >
                      <FileText size={14} strokeWidth={1.8} /> Markdown
                    </a>
                    <a
                      className={`download-button is-primary focus-ring ${reportReady ? '' : 'is-disabled'}`}
                      href={reportReady ? reportUrl(investigation.id, 'pdf') : undefined}
                      aria-disabled={!reportReady}
                      data-testid="button-download-pdf"
                    >
                      <Download size={14} strokeWidth={1.8} /> PDF
                    </a>
                  </div>
                </div>
                <div className="report-body" data-testid="text-final-report">
                  {investigation.final_report
                    ? <MarkdownReport content={investigation.final_report} />
                    : <p className="report-pending">Report generation is in progress. Awaiting final payload.</p>}
                </div>
              </section>
            )}
          </motion.section>
        )}

        {!investigation && (
          <section className="empty-ledger" style={{ marginTop: 70 }} data-testid="status-pending-investigation">
            <Lock size={20} />
            <div>Awaiting target selection. Launch an investigation to unlock the evidence matrix.</div>
          </section>
        )}

        <footer className="footer-line">
          <span>Sentinel // controlled intelligence surface</span>
          <span><Activity size={12} style={{ verticalAlign: 'middle', marginRight: 6 }} /> All decisions ledgered</span>
        </footer>
      </main>

      <LedgerModal
        isOpen={showHistory}
        onClose={() => setShowHistory(false)}
        records={history}
        loading={historyLoading}
        error={historyError}
        onReload={() => void loadHistory()}
      />

      <AssetsModal isOpen={showAssets} onClose={() => setShowAssets(false)} />

      <SiteFooter />
    </div>
  );
}

function EmptyData({ label }: { label: string }) {
  return <div className="empty-data" data-testid="status-evidence-empty">{label}</div>;
}

export default App;