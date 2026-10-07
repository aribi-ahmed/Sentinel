import { useEffect, useMemo, useRef, useState } from 'react';
import { AnimatePresence, motion, useReducedMotion } from 'framer-motion';
import {
  AlertOctagon,
  ArrowUpRight,
  ChevronDown,
  Gauge,
  Minus,
  ExternalLink,
  ShieldCheck,
  TrendingDown,
  TrendingUp,
} from 'lucide-react';
import type {
  EvidenceRecord,
  RiskAssessment,
  RiskBand,
  RiskDimension,
  SanctionsScreening,
} from '../services/api';
import './VerdictPanel.css';

const BAND_ORDER: RiskBand[] = ['MINIMAL', 'LOW', 'MODERATE', 'ELEVATED', 'SEVERE'];

const BAND_COPY: Record<RiskBand, string> = {
  MINIMAL: 'No adverse signal beyond ordinary commercial activity',
  LOW: 'Routine exposure for the sector; no substantiated allegation',
  MODERATE: 'Live exposure that is bounded, disclosed and manageable',
  ELEVATED: 'Findings against the entity; material penalty is plausible',
  SEVERE: 'Designation, charges or insolvency — dealing is restricted',
};

/** Gauge geometry: a 220° sweep, open at the bottom. */
const ARC_START = 160;
const ARC_SWEEP = 220;
const RADIUS = 82;
const CENTRE = { x: 100, y: 96 };

function bandOf(score: number): RiskBand {
  if (score >= 80) return 'SEVERE';
  if (score >= 60) return 'ELEVATED';
  if (score >= 40) return 'MODERATE';
  if (score >= 20) return 'LOW';
  return 'MINIMAL';
}

function polar(angleDegrees: number, radius = RADIUS) {
  const radians = (angleDegrees * Math.PI) / 180;
  return {
    x: CENTRE.x + radius * Math.cos(radians),
    y: CENTRE.y - radius * Math.sin(radians),
  };
}

function arcPath(fromScore: number, toScore: number, radius = RADIUS) {
  // Scores run clockwise from ARC_START, so the angle decreases as risk rises.
  const start = polar(ARC_START - (fromScore / 100) * ARC_SWEEP, radius);
  const end = polar(ARC_START - (toScore / 100) * ARC_SWEEP, radius);
  const large = ((toScore - fromScore) / 100) * ARC_SWEEP > 180 ? 1 : 0;
  return `M ${start.x} ${start.y} A ${radius} ${radius} 0 ${large} 1 ${end.x} ${end.y}`;
}

/** Counts a number up to its target so the score lands rather than appears. */
function useCountUp(target: number, enabled: boolean) {
  const [value, setValue] = useState(0);
  const frame = useRef<number | null>(null);

  useEffect(() => {
    if (!enabled) return;
    const started = performance.now();
    const duration = 1100;

    const tick = (now: number) => {
      const progress = Math.min(1, (now - started) / duration);
      // Ease-out cubic, so the count decelerates into place.
      setValue(target * (1 - (1 - progress) ** 3));
      if (progress < 1) frame.current = requestAnimationFrame(tick);
    };

    frame.current = requestAnimationFrame(tick);
    return () => {
      if (frame.current) cancelAnimationFrame(frame.current);
    };
  }, [target, enabled]);

  // With motion disabled the final value is simply derived, so no render is
  // spent tweening toward a number the viewer asked not to see move.
  return enabled ? value : target;
}

const KIND_LABEL: Record<string, string> = {
  filing: 'Filing',
  market_data: 'Market',
  watchlist: 'Watchlist',
  policy: 'Policy',
  open_source: 'Open source',
  baseline: 'Baseline',
};

/** The evidence a factor cites, resolved from the run's pool. */
function CitedEvidence({ records }: { records: EvidenceRecord[] }) {
  if (!records.length) return null;

  return (
    <div className="cited-evidence">
      <div className="cited-head">
        Cited evidence
        <span>{records.length}</span>
      </div>
      <ul>
        {records.map((record) => (
          <li key={record.id} className={`is-${record.confidence}`}>
            <span className="cited-confidence" title={`${record.confidence} confidence`}>
              {record.confidence}
            </span>
            <div className="cited-body">
              <strong>{record.summary}</strong>
              {record.detail && <p>{record.detail}</p>}
              <span className="cited-source">
                {KIND_LABEL[record.kind] ?? record.kind} · {record.source} · {record.collector.replace(/_/g, ' ')}
              </span>
            </div>
            {record.url && (
              <a
                className="cited-link focus-ring"
                href={record.url}
                target="_blank"
                rel="noopener noreferrer"
                title={record.url}
              >
                <ExternalLink size={12} strokeWidth={1.9} />
              </a>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}

function DimensionRow({
  dimension,
  index,
  animate,
  evidence,
}: {
  dimension: RiskDimension;
  index: number;
  animate: boolean;
  evidence: Map<string, EvidenceRecord>;
}) {
  const [open, setOpen] = useState(false);

  const cited = (dimension.evidence_ids ?? [])
    .map((id) => evidence.get(id))
    .filter((record): record is EvidenceRecord => record !== undefined);

  const hasDetail =
    Boolean(dimension.rationale) ||
    Boolean(dimension.signals?.length) ||
    Boolean(dimension.matches?.length) ||
    cited.length > 0;

  return (
    <div className={`dimension-row is-${dimension.band.toLowerCase()} ${open ? 'is-open' : ''}`}>
      <button
        type="button"
        className="dimension-head focus-ring"
        onClick={() => hasDetail && setOpen((value) => !value)}
        aria-expanded={hasDetail ? open : undefined}
        disabled={!hasDetail}
        data-testid={`dimension-${dimension.id}`}
      >
        <span className="dimension-label">
          {dimension.label}
          <em>{Math.round(dimension.weight * 100)}% weight</em>
        </span>

        <span className="dimension-track">
          <motion.i
            initial={{ width: animate ? 0 : `${dimension.score}%` }}
            animate={{ width: `${dimension.score}%` }}
            transition={{ duration: animate ? 0.8 : 0, delay: animate ? 0.25 + index * 0.09 : 0, ease: [0.22, 1, 0.36, 1] }}
          />
        </span>

        <span className="dimension-score">
          <strong>{Math.round(dimension.score)}</strong>
          <em>{dimension.band}</em>
        </span>

        {dimension.assessed && dimension.grounded === false && (
          <span className="dimension-ungrounded" title="Scored on judgement; no evidence cited">
            ungrounded
          </span>
        )}

        {hasDetail && <ChevronDown size={14} className="dimension-caret" strokeWidth={2} />}
      </button>

      <AnimatePresence initial={false}>
        {open && (
          <motion.div
            className="dimension-detail"
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: animate ? 0.28 : 0, ease: [0.22, 1, 0.36, 1] }}
          >
            <div className="dimension-detail-inner">
              <p>{dimension.rationale}</p>

              {Boolean(dimension.signals?.length) && (
                <ul className="signal-list">
                  {dimension.signals!.map((signal) => (
                    <li key={signal.metric}>
                      <span className="signal-metric">{signal.metric}</span>
                      <span className="signal-value">{signal.value}</span>
                      <span className={`signal-score is-${bandOf(signal.score).toLowerCase()}`}>{signal.score}</span>
                      <span className="signal-note">{signal.note}</span>
                    </li>
                  ))}
                </ul>
              )}

              {Boolean(dimension.providers?.length) && (
                <ul className="provider-list">
                  {dimension.providers!.map((provider) => (
                    <li key={provider.id} className={provider.available ? 'is-live' : 'is-off'}>
                      <span className="provider-dot" aria-hidden="true" />
                      <strong>{provider.label}</strong>
                      <span className="provider-status">{provider.status.replace('_', ' ')}</span>
                      <span className="provider-detail">{provider.detail}</span>
                    </li>
                  ))}
                </ul>
              )}

              <CitedEvidence records={cited} />

              {Boolean(dimension.matches?.length) && (
                <ul className="match-list">
                  {dimension.matches!.map((match) => (
                    <li key={`${match.name}-${match.program}`}>
                      <strong>{match.name}</strong>
                      <span className="match-program">{match.program}</span>
                      <span className="match-meta">
                        matched on {match.matched_on} · {Math.round(match.confidence * 100)}% confidence
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

interface VerdictPanelProps {
  assessment?: RiskAssessment | null;
  sanctions?: SanctionsScreening | null;
  /** Plain narrative kept for runs recorded before scoring existed. */
  fallbackReasoning?: string;
}

export function VerdictPanel({ assessment, sanctions, fallbackReasoning }: VerdictPanelProps) {
  const reduceMotion = useReducedMotion();
  const animate = !reduceMotion;

  const score = assessment?.score ?? 0;
  const counted = useCountUp(score, animate && Boolean(assessment));
  const band = assessment?.band ?? bandOf(score);
  const confidence = assessment?.confidence ?? 0;

  // One lookup for the whole panel: every factor resolves its citations against
  // the same pool, so an id that no longer exists simply renders nothing.
  const evidenceIndex = useMemo(() => {
    const pool = assessment?.evidence ?? assessment?.cited_evidence ?? [];
    return new Map(pool.map((record) => [record.id, record]));
  }, [assessment]);

  const ticks = useMemo(
    () => BAND_ORDER.map((name, index) => ({ name, at: index * 20 })),
    [],
  );

  if (!assessment) {
    return (
      <section className="verdict-panel panel cornered is-pending" data-testid="card-supervisor-rationale">
        <div className="eyebrow">/ supervisor synthesis</div>
        <h2>Risk verdict</h2>
        <p className="verdict-pending" data-testid="text-supervisor-reasoning">
          {fallbackReasoning || 'Supervisor evaluating specialist evidence…'}
        </p>
      </section>
    );
  }

  const sanctionsAlert = sanctions && sanctions.status !== 'CLEAR' && sanctions.status !== 'UNAVAILABLE';

  return (
    <section
      className={`verdict-panel panel cornered is-${band.toLowerCase()}`}
      aria-labelledby="verdict-title"
      data-testid="card-supervisor-rationale"
    >
      <div className="verdict-masthead">
        <div>
          <div className="eyebrow">/ supervisor synthesis</div>
          <h2 id="verdict-title">Risk verdict</h2>
        </div>
        <div className="verdict-quality">
          <span>evidence</span>
          <strong>
            {assessment.evidence_quality}
            {evidenceIndex.size > 0 && <em> · {evidenceIndex.size} cited</em>}
          </strong>
        </div>
      </div>

      <div className="verdict-body">
        <div className="verdict-gauge">
          <svg viewBox="0 0 200 132" role="img" aria-label={`Composite risk score ${Math.round(score)} of 100, band ${band}`}>
            <path className="gauge-track" d={arcPath(0, 100)} />

            {/* Band separators, so the score reads against the scale itself. */}
            {ticks.slice(1).map((tick) => {
              const outer = polar(ARC_START - (tick.at / 100) * ARC_SWEEP, RADIUS + 9);
              const inner = polar(ARC_START - (tick.at / 100) * ARC_SWEEP, RADIUS - 9);
              return <line key={tick.name} className="gauge-tick" x1={inner.x} y1={inner.y} x2={outer.x} y2={outer.y} />;
            })}

            <motion.path
              className="gauge-value"
              d={arcPath(0, Math.max(score, 0.5))}
              initial={{ pathLength: animate ? 0 : 1 }}
              animate={{ pathLength: 1 }}
              transition={{ duration: animate ? 1.1 : 0, ease: [0.22, 1, 0.36, 1] }}
            />

            <text className="gauge-score" x={CENTRE.x} y={CENTRE.y + 4} textAnchor="middle">
              {Math.round(counted)}
            </text>
            <text className="gauge-scale" x={CENTRE.x} y={CENTRE.y + 22} textAnchor="middle">
              / 100
            </text>
          </svg>

          <motion.div
            className="verdict-band"
            initial={{ opacity: animate ? 0 : 1, y: animate ? 8 : 0 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: animate ? 0.75 : 0, duration: animate ? 0.4 : 0 }}
          >
            <strong data-testid="text-risk-band">{band}</strong>
            <span>{BAND_COPY[band]}</span>
          </motion.div>

          <div className="verdict-confidence">
            <span>confidence</span>
            <span className="confidence-track">
              <motion.i
                initial={{ width: animate ? 0 : `${confidence * 100}%` }}
                animate={{ width: `${confidence * 100}%` }}
                transition={{ duration: animate ? 0.7 : 0, delay: animate ? 0.6 : 0 }}
              />
            </span>
            <strong>{Math.round(confidence * 100)}%</strong>
          </div>
        </div>

        <div className="verdict-analysis">
          <p className="verdict-summary" data-testid="text-supervisor-reasoning">
            {assessment.summary}
          </p>

          {assessment.escalated && assessment.escalation_note && (
            <div className="verdict-escalation" role="note">
              <ArrowUpRight size={14} strokeWidth={2} />
              <span>{assessment.escalation_note}</span>
            </div>
          )}

          {sanctionsAlert && (
            <div className={`sanctions-alert is-${sanctions!.status.toLowerCase()}`} role="alert">
              <AlertOctagon size={15} strokeWidth={1.9} />
              <div>
                <strong>
                  OFAC {sanctions!.status === 'HIT' ? 'designation matched' : 'possible match'}
                </strong>
                <span>
                  Screened against {sanctions!.list_size.toLocaleString()} SDN entries —
                  {' '}{sanctions!.matches[0]?.name ?? 'match recorded'}
                  {sanctions!.matches[0]?.program ? ` (${sanctions!.matches[0].program})` : ''}
                </span>
              </div>
            </div>
          )}

          <div className="verdict-dimensions">
            {assessment.dimensions.map((dimension, index) => (
              <DimensionRow
                key={dimension.id}
                dimension={dimension}
                index={index}
                animate={animate}
                evidence={evidenceIndex}
              />
            ))}
          </div>
        </div>
      </div>

      {(assessment.key_drivers.length > 0 || assessment.mitigants.length > 0) && (
        <div className="verdict-factors">
          <FactorColumn
            title="Risk drivers"
            tone="driver"
            icon={TrendingUp}
            items={assessment.key_drivers}
            animate={animate}
          />
          <FactorColumn
            title="Mitigating factors"
            tone="mitigant"
            icon={TrendingDown}
            items={assessment.mitigants}
            animate={animate}
          />
        </div>
      )}

      {!assessment.model_ok && assessment.model_error && (
        <div className="verdict-degraded" role="status">
          <Gauge size={13} strokeWidth={1.8} />
          Narrative assessment unavailable ({assessment.model_error}) — the score rests on the computed
          dimensions alone, and confidence has been reduced accordingly.
        </div>
      )}
    </section>
  );
}

function FactorColumn({
  title,
  tone,
  icon: Icon,
  items,
  animate,
}: {
  title: string;
  tone: 'driver' | 'mitigant';
  icon: typeof TrendingUp;
  items: string[];
  animate: boolean;
}) {
  return (
    <div className={`factor-column is-${tone}`}>
      <div className="factor-title">
        <Icon size={13} strokeWidth={2} />
        {title}
        <em>{items.length}</em>
      </div>

      {items.length === 0 ? (
        <div className="factor-empty">
          {tone === 'driver' ? (
            <><ShieldCheck size={13} /> None identified</>
          ) : (
            <><Minus size={13} /> None recorded</>
          )}
        </div>
      ) : (
        <ul>
          {items.map((item, index) => (
            <motion.li
              key={item}
              initial={{ opacity: animate ? 0 : 1, x: animate ? (tone === 'driver' ? -10 : 10) : 0 }}
              animate={{ opacity: 1, x: 0 }}
              transition={{ duration: animate ? 0.35 : 0, delay: animate ? 0.5 + index * 0.07 : 0 }}
            >
              {item}
            </motion.li>
          ))}
        </ul>
      )}
    </div>
  );
}
