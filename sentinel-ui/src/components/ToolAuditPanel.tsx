import { useState } from 'react';
import { AnimatePresence, motion, useReducedMotion } from 'framer-motion';
import {
  AlertTriangle,
  CheckCircle2,
  ChevronRight,
  CircleSlash,
  Clock,
  Link2,
  PlugZap,
  SearchX,
} from 'lucide-react';
import type { ToolAudit, ToolInvocation, ToolOutcome } from '../services/api';
import './ToolAuditPanel.css';

/**
 * Every outcome gets its own treatment, because the whole point of the four-state
 * model is that they are *not* interchangeable. "Found nothing" supports a clean
 * verdict; "never ran" does not, and an officer must be able to tell them apart
 * at a glance rather than by reading the digest.
 */
const OUTCOME: Record<ToolOutcome, { label: string; icon: typeof CheckCircle2; hint: string }> = {
  ok: {
    label: 'returned data',
    icon: CheckCircle2,
    hint: 'The tool ran and returned usable data.',
  },
  empty: {
    label: 'found nothing',
    icon: SearchX,
    hint: 'The tool ran and found nothing. That is an answer about the entity.',
  },
  failed: {
    label: 'failed',
    icon: AlertTriangle,
    hint: 'The tool ran and errored. The evidence base is thinner than it looks.',
  },
  unavailable: {
    label: 'did not run',
    icon: PlugZap,
    hint: 'The tool never ran — a missing key or dataset. It found nothing because it looked at nothing.',
  },
};

const AGENT_LABEL: Record<string, string> = {
  research_analyst: 'Research',
  financial_analyst: 'Financial',
  news_analyst: 'News',
  compliance_analyst: 'Compliance',
  memory: 'Memory',
  fraud_analyst: 'Fraud',
  graph_analyst: 'Network',
  critic: 'Critic',
};

function duration(ms: number): string {
  // An in-process derivation finishes in a fraction of a millisecond. Rendering
  // that as "0ms" reads as though the step never ran.
  if (ms < 1) return '<1ms';
  if (ms < 1000) return `${Math.round(ms)}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

function CallRow({ call, index }: { call: ToolInvocation; index: number }) {
  const reduceMotion = useReducedMotion();
  const [open, setOpen] = useState(false);

  const outcome = OUTCOME[call.outcome] ?? OUTCOME.ok;
  const Icon = outcome.icon;
  const args = Object.entries(call.arguments || {});
  const detail = call.error || call.result_digest;

  return (
    <motion.li
      className={`tool-call is-${call.outcome}`}
      initial={{ opacity: 0, x: -10 }}
      animate={{ opacity: 1, x: 0 }}
      transition={{ duration: reduceMotion ? 0 : 0.28, delay: reduceMotion ? 0 : index * 0.05 }}
    >
      <button
        type="button"
        className="tool-call-head focus-ring"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
        title={outcome.hint}
      >
        <ChevronRight className={`tool-chevron ${open ? 'is-open' : ''}`} size={13} strokeWidth={2} />
        <Icon className="tool-outcome-icon" size={14} strokeWidth={1.9} />

        <code className="tool-name">{call.tool}</code>

        <span className="tool-caller">{AGENT_LABEL[call.caller] ?? call.caller}</span>

        <span className="tool-outcome-label">{outcome.label}</span>

        <span className="tool-duration">
          <Clock size={10} strokeWidth={1.9} />
          {duration(call.duration_ms)}
        </span>
      </button>

      <AnimatePresence initial={false}>
        {open && (
          <motion.div
            className="tool-call-body"
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: reduceMotion ? 0 : 0.24, ease: 'easeOut' }}
          >
            <div className="tool-call-inner">
              {/* The arguments are the part that makes a screening disputable
                  rather than merely asserted. */}
              <div className="tool-block">
                <span className="tool-block-label">Called with</span>
                {args.length ? (
                  <div className="tool-args">
                    {args.map(([key, value]) => (
                      <span key={key} className="tool-arg">
                        <em>{key}</em>
                        {String(value)}
                      </span>
                    ))}
                  </div>
                ) : (
                  <p className="tool-block-text">No arguments.</p>
                )}
              </div>

              {detail && (
                <div className="tool-block">
                  <span className="tool-block-label">{call.error ? 'Reason' : 'Result'}</span>
                  <p className="tool-block-text">{detail}</p>
                </div>
              )}

              <div className="tool-foot">
                {call.evidence_ids?.length ? (
                  <span className="tool-evidence" title="Evidence records produced by this call">
                    <Link2 size={11} strokeWidth={1.9} />
                    {call.evidence_ids.length} evidence record
                    {call.evidence_ids.length === 1 ? '' : 's'} filed
                  </span>
                ) : (
                  <span className="tool-evidence is-none">
                    <CircleSlash size={11} strokeWidth={1.9} />
                    No evidence filed from this call
                  </span>
                )}
                <time dateTime={call.called_at}>{new Date(call.called_at).toLocaleTimeString()}</time>
              </div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </motion.li>
  );
}

/**
 * The provenance view: every external call the investigation made.
 *
 * The evidence panels answer "what did we find?". This one answers the question
 * a reviewer asks when they doubt the verdict — "what did you actually check,
 * with what inputs, and what came back?" A clean screening that was never run
 * looks identical to a clean screening in the verdict; here it does not.
 */
export function ToolAuditPanel({ audit }: { audit?: ToolAudit | null }) {
  const calls = audit?.invocations ?? [];

  if (!calls.length) {
    return (
      <div className="tool-audit-empty" data-testid="panel-provenance-empty">
        No external tool calls recorded for this investigation.
      </div>
    );
  }

  const counts = calls.reduce<Record<string, number>>((totals, call) => {
    totals[call.outcome] = (totals[call.outcome] ?? 0) + 1;
    return totals;
  }, {});

  const stats = [
    { key: 'ok', label: 'returned data' },
    { key: 'empty', label: 'found nothing' },
    { key: 'failed', label: 'failed' },
    { key: 'unavailable', label: 'did not run' },
  ].filter((stat) => counts[stat.key]);

  return (
    <section className="tool-audit" data-testid="panel-provenance">
      <header className="tool-audit-head">
        <div>
          <div className="eyebrow">// tool audit trail</div>
          <h4>
            {calls.length} call{calls.length === 1 ? '' : 's'} across{' '}
            {audit?.tools_used?.length ?? 0} tools
          </h4>
        </div>
        <span className="tool-audit-total">
          <Clock size={11} strokeWidth={1.9} />
          {duration(audit?.total_duration_ms ?? 0)}
        </span>
      </header>

      <div className="tool-audit-stats">
        {stats.map((stat) => (
          <div key={stat.key} className={`tool-stat is-${stat.key}`}>
            <strong>{counts[stat.key]}</strong>
            <span>{stat.label}</span>
          </div>
        ))}
      </div>

      <ul className="tool-call-list">
        {calls.map((call, index) => (
          <CallRow key={call.id} call={call} index={index} />
        ))}
      </ul>

      <p className="tool-audit-note">
        Arguments are recorded so a screening can be reproduced and disputed. Values matching a
        secret-looking name are masked before they reach the record.
      </p>
    </section>
  );
}
