import { motion, useReducedMotion } from 'framer-motion';
import {
  AlertTriangle,
  ArrowDownRight,
  ArrowUpRight,
  Building2,
  CircleDot,
  Fingerprint,
  History,
  Landmark,
  Minus,
  Network,
  ShieldAlert,
  Sparkles,
} from 'lucide-react';
import type {
  EntityHistory,
  FraudAssessment,
  GraphNode,
  KnowledgeGraph,
} from '../services/api';
import './IntelligencePanels.css';

/* ── Memory ─────────────────────────────────────────────────────────────── */

const TREND = {
  first_review: { icon: Sparkles, tone: 'neutral', label: 'First review' },
  deteriorating: { icon: ArrowUpRight, tone: 'alert', label: 'Deteriorating' },
  improving: { icon: ArrowDownRight, tone: 'safe', label: 'Improving' },
  stable: { icon: Minus, tone: 'neutral', label: 'Stable' },
} as const;

export function HistoryPanel({ history }: { history?: EntityHistory | null }) {
  const reduceMotion = useReducedMotion();

  if (!history) {
    return <div className="intel-empty">No entity history was retrieved.</div>;
  }

  const trend = TREND[history.trend] ?? TREND.stable;
  const TrendIcon = trend.icon;

  return (
    <section className="intel-panel" data-testid="panel-history">
      <header className="intel-head">
        <div>
          <div className="eyebrow">// entity recall</div>
          <h4>
            {history.count === 0
              ? 'No prior review on record'
              : `${history.count} prior review${history.count === 1 ? '' : 's'}`}
          </h4>
        </div>
        <span className={`intel-badge is-${trend.tone}`}>
          <TrendIcon size={12} strokeWidth={2} />
          {trend.label}
        </span>
      </header>

      {history.summary && <p className="intel-lede">{history.summary}</p>}

      {history.rejection_count > 0 && (
        <div className="intel-warn">
          <ShieldAlert size={14} strokeWidth={1.9} />
          {history.rejection_count} previous verdict
          {history.rejection_count === 1 ? ' was' : 's were'} declined by an analyst.
        </div>
      )}

      {history.reviews.length > 0 && (
        <ol className="intel-timeline">
          {history.reviews.map((review, index) => (
            <motion.li
              key={review.investigation_id}
              initial={{ opacity: 0, x: -8 }}
              animate={{ opacity: 1, x: 0 }}
              transition={{ duration: reduceMotion ? 0 : 0.28, delay: reduceMotion ? 0 : index * 0.05 }}
            >
              <CircleDot size={11} strokeWidth={2} className={`band-${(review.band || 'unknown').toLowerCase()}`} />
              <div>
                <strong>{review.band || 'Not scored'}</strong>
                <span>
                  {new Date(review.reviewed_at).toLocaleDateString(undefined, {
                    day: '2-digit',
                    month: 'short',
                    year: 'numeric',
                  })}
                  {review.status ? ` · ${review.status}` : ''}
                </span>
              </div>
              {review.approved === false && <em className="intel-declined">declined</em>}
            </motion.li>
          ))}
        </ol>
      )}
    </section>
  );
}

/* ── Network ────────────────────────────────────────────────────────────── */

const NODE_ICON: Record<string, typeof Building2> = {
  subject: Sparkles,
  registrant: Landmark,
  jurisdiction: Building2,
  sector: Network,
  watchlist: ShieldAlert,
  programme: AlertTriangle,
  organisation: Building2,
  alias: CircleDot,
};

const KIND_LABEL: Record<string, string> = {
  subject: 'Subject',
  registrant: 'SEC registrant',
  jurisdiction: 'Jurisdiction',
  sector: 'Industry',
  watchlist: 'Watchlist entry',
  programme: 'Sanctions programme',
  organisation: 'Organisation',
  alias: 'Alias',
};

function NodeRow({
  node,
  index,
  relation,
  rootLabel,
  degree,
}: {
  node: GraphNode;
  index: number;
  relation: string | null;
  rootLabel: string;
  degree: number;
}) {
  const reduceMotion = useReducedMotion();
  const Icon = NODE_ICON[node.kind] ?? CircleDot;
  const isRoot = node.kind === 'subject';

  return (
    <motion.li
      className={`graph-node ${node.flagged ? 'is-flagged' : ''} ${isRoot ? 'is-root' : ''}`}
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: reduceMotion ? 0 : 0.26, delay: reduceMotion ? 0 : index * 0.05 }}
    >
      <Icon size={14} strokeWidth={1.9} />
      <div className="graph-node-body">
        <div className="graph-node-title">
          <strong>{node.label}</strong>
          <em className="graph-kind">{KIND_LABEL[node.kind] ?? node.kind}</em>
        </div>
        <span>{node.detail}</span>

        {/* The root's own edges are listed against each satellite, so repeating
            them here would say the same thing six times. */}
        {isRoot ? (
          <div className="graph-relations">
            <em>{degree} direct relationship{degree === 1 ? '' : 's'}</em>
          </div>
        ) : (
          relation && (
            <div className="graph-relations">
              <em>
                {rootLabel} — {relation} →
              </em>
            </div>
          )
        )}
      </div>
      {node.flagged && <span className="graph-flag">flagged</span>}
    </motion.li>
  );
}

/**
 * Radial layout: the subject at the centre, everything it connects to arranged
 * around it. Positions are derived rather than simulated, so the diagram is
 * stable between renders and cannot drift while an investigation streams.
 */
function NetworkDiagram({ graph }: { graph: KnowledgeGraph }) {
  const reduceMotion = useReducedMotion();

  const W = 640;
  const H = 340;
  const cx = W / 2;
  const cy = H / 2;
  const radius = Math.min(W, H) / 2 - 58;

  const root = graph.nodes.find((n) => n.kind === 'subject') ?? graph.nodes[0];
  const others = graph.nodes.filter((n) => n.id !== root.id);

  const positions = new Map<string, { x: number; y: number }>();
  positions.set(root.id, { x: cx, y: cy });

  others.forEach((node, index) => {
    // Start at -90 degrees so the first satellite sits directly above the root.
    const angle = (index / Math.max(others.length, 1)) * Math.PI * 2 - Math.PI / 2;
    positions.set(node.id, {
      x: cx + Math.cos(angle) * radius,
      y: cy + Math.sin(angle) * radius,
    });
  });

  return (
    <div className="network-diagram">
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Entity relationship diagram">
        {graph.edges.map((edge, index) => {
          const from = positions.get(edge.source);
          const to = positions.get(edge.target);
          if (!from || !to) return null;

          const target = graph.nodes.find((n) => n.id === edge.target);
          return (
            <motion.line
              key={`${edge.source}-${edge.target}-${edge.kind}`}
              x1={from.x}
              y1={from.y}
              x2={to.x}
              y2={to.y}
              className={target?.flagged ? 'net-edge is-flagged' : 'net-edge'}
              initial={{ pathLength: 0, opacity: 0 }}
              animate={{ pathLength: 1, opacity: 1 }}
              transition={{ duration: reduceMotion ? 0 : 0.5, delay: reduceMotion ? 0 : index * 0.06 }}
            />
          );
        })}

        {graph.nodes.map((node, index) => {
          const point = positions.get(node.id);
          if (!point) return null;

          const isRoot = node.id === root.id;
          const r = isRoot ? 26 : 16;
          const label = node.label.length > 22 ? `${node.label.slice(0, 21)}…` : node.label;

          return (
            <motion.g
              key={node.id}
              initial={{ opacity: 0, scale: 0.7 }}
              animate={{ opacity: 1, scale: 1 }}
              transition={{ duration: reduceMotion ? 0 : 0.34, delay: reduceMotion ? 0 : index * 0.06 }}
              style={{ transformOrigin: `${point.x}px ${point.y}px` }}
            >
              <circle
                cx={point.x}
                cy={point.y}
                r={r}
                className={`net-node is-${node.kind} ${node.flagged ? 'is-flagged' : ''} ${
                  isRoot ? 'is-root' : ''
                }`}
              />
              <text
                x={point.x}
                y={point.y + r + 14}
                textAnchor="middle"
                className={`net-label ${isRoot ? 'is-root' : ''}`}
              >
                {label}
              </text>
            </motion.g>
          );
        })}
      </svg>
    </div>
  );
}

export function NetworkPanel({ graph }: { graph?: KnowledgeGraph | null }) {
  if (!graph || graph.nodes.length === 0) {
    return <div className="intel-empty">No connected entities were resolved.</div>;
  }

  const root = graph.nodes.find((n) => n.kind === 'subject') ?? graph.nodes[0];
  const rootDegree = graph.edges.filter(
    (edge) => edge.source === root.id || edge.target === root.id,
  ).length;

  /** How this entity connects back to the subject, phrased from the subject out. */
  const relationFor = (nodeId: string) => {
    const edge = graph.edges.find(
      (candidate) => candidate.target === nodeId || candidate.source === nodeId,
    );
    return edge ? edge.label : null;
  };

  return (
    <section className="intel-panel" data-testid="panel-network">
      <header className="intel-head">
        <div>
          <div className="eyebrow">// entity network</div>
          <h4>
            {graph.node_count} entities · {graph.edge_count} relationships
          </h4>
        </div>
        {graph.flagged_count > 0 && (
          <span className="intel-badge is-alert">
            <ShieldAlert size={12} strokeWidth={2} />
            {graph.flagged_count} flagged
          </span>
        )}
      </header>

      <p className="intel-lede">{graph.summary}</p>

      <NetworkDiagram graph={graph} />

      <div className="intel-subhead">Entities</div>
      <ul className="graph-nodes">
        {graph.nodes.map((node, index) => (
          <NodeRow
            key={node.id}
            node={node}
            index={index}
            relation={node.id === root.id ? null : relationFor(node.id)}
            rootLabel={root.label}
            degree={rootDegree}
          />
        ))}
      </ul>

      <p className="intel-note">
        Every entity here was derived from retrieved evidence — a registrant record, a watchlist
        entry, or an organisation named in reporting.
      </p>
    </section>
  );
}

/* ── Fraud ──────────────────────────────────────────────────────────────── */

const CATEGORY_META: Record<string, { label: string; blurb: string }> = {
  pressure: {
    label: 'Financial pressure',
    blurb: 'Incentive to misstate: losses, leverage, accelerated obligations.',
  },
  opportunity: {
    label: 'Control weakness',
    blurb: 'Oversight that would ordinarily catch a misstatement is absent.',
  },
  disclosure: {
    label: 'Reporting failure',
    blurb: 'The event where a misstatement surfaces: restatements, late filings.',
  },
};

const ORDER = ['pressure', 'opportunity', 'disclosure'] as const;

export function FraudPanel({ assessment }: { assessment?: FraudAssessment | null }) {
  const reduceMotion = useReducedMotion();

  if (!assessment) {
    return <div className="intel-empty">Fraud indicators were not assessed.</div>;
  }

  if (assessment.signals.length === 0) {
    return (
      <section className="intel-panel" data-testid="panel-fraud">
        <header className="intel-head">
          <div>
            <div className="eyebrow">// fraud indicators</div>
            <h4>No indicators identified</h4>
          </div>
          <span className="intel-badge is-safe">{assessment.band}</span>
        </header>
        <p className="intel-lede">{assessment.summary}</p>
      </section>
    );
  }

  const present = new Set(assessment.categories);

  return (
    <section className="intel-panel" data-testid="panel-fraud">
      <header className="intel-head">
        <div>
          <div className="eyebrow">// fraud indicators</div>
          <h4>
            {assessment.signals.length} indicator
            {assessment.signals.length === 1 ? '' : 's'} · {assessment.score.toFixed(0)}/100
          </h4>
        </div>
        <span className={`intel-badge band-${assessment.band.toLowerCase()}`}>
          <Fingerprint size={12} strokeWidth={2} />
          {assessment.band}
        </span>
      </header>

      <p className="intel-lede">{assessment.summary}</p>

      {/* Coverage across the three conditions is what drives the score: any one
          alone is common, all three together is not. */}
      <div className="fraud-triangle">
        {ORDER.map((key) => (
          <div key={key} className={`fraud-condition ${present.has(key) ? 'is-present' : ''}`}>
            <strong>{CATEGORY_META[key].label}</strong>
            <span>{CATEGORY_META[key].blurb}</span>
            <em>{present.has(key) ? 'present' : 'not observed'}</em>
          </div>
        ))}
      </div>

      {assessment.patterns.length > 0 && (
        <div className="fraud-patterns">
          <div className="intel-subhead">Convergence patterns</div>
          {assessment.patterns.map((pattern, index) => (
            <motion.div
              key={pattern.id}
              className="fraud-pattern"
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: reduceMotion ? 0 : 0.3, delay: reduceMotion ? 0 : index * 0.08 }}
            >
              <div className="fraud-pattern-head">
                <AlertTriangle size={13} strokeWidth={1.9} />
                <strong>{pattern.label}</strong>
                <span>+{pattern.uplift.toFixed(0)}</span>
              </div>
              <p>{pattern.detail}</p>
            </motion.div>
          ))}
        </div>
      )}

      <div className="intel-subhead">Indicators</div>
      <ul className="fraud-signals">
        {assessment.signals.map((signal, index) => (
          <motion.li
            key={signal.id}
            className={`fraud-signal is-${signal.category}`}
            initial={{ opacity: 0, x: -8 }}
            animate={{ opacity: 1, x: 0 }}
            transition={{ duration: reduceMotion ? 0 : 0.26, delay: reduceMotion ? 0 : index * 0.04 }}
          >
            <div className="fraud-signal-head">
              <strong>{signal.label}</strong>
              <span className="fraud-weight">{signal.weight.toFixed(0)}</span>
            </div>
            <p>{signal.detail}</p>
            <em>{signal.category_label}</em>
          </motion.li>
        ))}
      </ul>
    </section>
  );
}
