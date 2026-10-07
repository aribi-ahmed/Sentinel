import { motion, useReducedMotion } from 'framer-motion';
import {
  AlertOctagon,
  Gavel,
  Info,
  ShieldAlert,
  ShieldCheck,
  TrendingDown,
  Wrench,
} from 'lucide-react';
import type { Challenge, ChallengeSeverity, CriticReview, ReviewVerdict } from '../services/api';
import './CriticPanel.css';

const VERDICT: Record<ReviewVerdict, { label: string; icon: typeof ShieldCheck; blurb: string }> = {
  endorsed: {
    label: 'Endorsed',
    icon: ShieldCheck,
    blurb: 'Every factor cites evidence that resolves. The review found nothing to contest.',
  },
  qualified: {
    label: 'Qualified',
    icon: ShieldAlert,
    blurb: 'The verdict stands, but parts of it rest on less than they claim.',
  },
  rejected: {
    label: 'Rejected',
    icon: AlertOctagon,
    blurb: 'The review found defects that make this verdict unsafe to rely on as written.',
  },
};

const SEVERITY_ICON: Record<ChallengeSeverity, typeof Info> = {
  blocking: AlertOctagon,
  material: Gavel,
  advisory: Info,
};

/** Plain-language names — the officer reading this is not a developer. */
const KIND_LABEL: Record<string, string> = {
  dangling_citation: 'Citation does not resolve',
  ungrounded_factor: 'Scored without evidence',
  band_mismatch: 'Band contradicts the score',
  unverified_scope: 'Source never consulted',
  thin_evidence: 'Evidence too weak for the verdict',
  overconfident: 'Confidence outruns the evidence',
  unsupported_driver: 'Claim the evidence does not support',
};

/**
 * The critic's review, shown next to the verdict it examined.
 *
 * Deliberately not collapsed by default when something was found: an objection
 * behind a disclosure triangle is an objection nobody reads, and the entire
 * point of the critic is that the officer sees the disagreement.
 */
export function CriticPanel({
  review,
  confidence,
}: {
  review?: CriticReview | null;
  confidence?: number;
}) {
  const reduceMotion = useReducedMotion();

  if (!review) return null;

  const verdict = VERDICT[review.verdict] ?? VERDICT.qualified;
  const VerdictIcon = verdict.icon;
  const penalty = review.confidence_penalty ?? 0;

  return (
    <section className={`critic-panel is-${review.verdict}`} data-testid="panel-critic">
      <header className="critic-head">
        <div className="critic-title">
          <VerdictIcon size={17} strokeWidth={1.9} />
          <div>
            <div className="eyebrow">// independent review</div>
            <h4>Critic {verdict.label.toLowerCase()} this assessment</h4>
          </div>
        </div>

        {penalty > 0 && (
          <span className="critic-penalty" title="Confidence deducted by the review">
            <TrendingDown size={12} strokeWidth={1.9} />
            −{Math.round(penalty * 100)} pts confidence
            {typeof confidence === 'number' && <em>now {Math.round(confidence * 100)}%</em>}
          </span>
        )}
      </header>

      <p className="critic-blurb">{verdict.blurb}</p>

      {review.revision > 0 && (
        <div className="critic-revision" role="status">
          This verdict was returned to the supervisor and re-scored{' '}
          {review.revision === 1 ? 'once' : `${review.revision} times`} before reaching you.
        </div>
      )}

      {!review.model_ok && (
        <div className="critic-degraded" role="status">
          The narrative check could not run ({review.model_error || 'provider unavailable'}). The
          structural checks below still applied.
        </div>
      )}

      {review.challenges.length === 0 ? (
        <div className="critic-clean">
          <ShieldCheck size={14} strokeWidth={1.9} />
          No unsupported factors, no dangling citations, every consulted source returned data.
        </div>
      ) : (
        <ul className="critic-challenges">
          {review.challenges.map((challenge: Challenge, index: number) => {
            const Icon = SEVERITY_ICON[challenge.severity] ?? Info;
            return (
              <motion.li
                key={challenge.id}
                className={`critic-challenge is-${challenge.severity}`}
                initial={{ opacity: 0, y: 8 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{
                  duration: reduceMotion ? 0 : 0.3,
                  delay: reduceMotion ? 0 : index * 0.07,
                }}
              >
                <div className="critic-challenge-head">
                  <Icon size={13} strokeWidth={1.9} />
                  <strong>{KIND_LABEL[challenge.kind] ?? challenge.kind.replace(/_/g, ' ')}</strong>
                  <span className="critic-severity">{challenge.severity}</span>
                </div>

                <p className="critic-detail">{challenge.detail}</p>

                {challenge.remedy && (
                  <p className="critic-remedy">
                    <Wrench size={11} strokeWidth={1.9} />
                    {challenge.remedy}
                  </p>
                )}
              </motion.li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
