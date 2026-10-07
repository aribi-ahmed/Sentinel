import { motion, useReducedMotion } from 'framer-motion';
import { Building2, CalendarClock, ExternalLink, Landmark, ShieldCheck, AlertTriangle } from 'lucide-react';
import type { EdgarProfile } from '../services/api';
import './FilingsPanel.css';

const SEVERITY_LABEL: Record<string, string> = {
  severe: 'Severe',
  elevated: 'Elevated',
  moderate: 'Moderate',
  low: 'Routine',
};

function annualStatus(days?: number | null): { label: string; tone: string } {
  if (typeof days !== 'number') return { label: 'unknown', tone: 'unknown' };
  if (days > 460) return { label: `${days}d — overdue`, tone: 'alert' };
  if (days > 380) return { label: `${days}d — due soon`, tone: 'watch' };
  return { label: `${days}d ago`, tone: 'safe' };
}

/**
 * SEC EDGAR is the one source in the file the entity wrote itself, so its
 * filings are shown as disclosures rather than folded into narrative evidence.
 */
export function FilingsPanel({ profile }: { profile?: EdgarProfile | null }) {
  const reduceMotion = useReducedMotion();

  if (!profile) return null;

  if (!profile.matched) {
    return (
      <section className="filings-panel is-unmatched" data-testid="panel-filings">
        <header className="filings-head">
          <div className="eyebrow">// sec edgar</div>
          <h4>Regulatory filings</h4>
        </header>
        <p className="filings-note">{profile.reason || 'No SEC registrant matched this entity.'}</p>
      </section>
    );
  }

  const annual = annualStatus(profile.annual_age_days);
  const flags = profile.flags || [];

  const facts = [
    { icon: Landmark, label: 'CIK', value: profile.cik },
    { icon: Building2, label: 'Industry', value: profile.sic_description || `SIC ${profile.sic}` },
    { icon: Building2, label: 'Incorporated', value: profile.state_of_incorporation || '—' },
    { icon: CalendarClock, label: 'Last annual report', value: annual.label, tone: annual.tone },
  ];

  return (
    <section className="filings-panel" data-testid="panel-filings">
      <header className="filings-head">
        <div>
          <div className="eyebrow">// sec edgar</div>
          <h4>{profile.company}</h4>
          <span className="filings-sub">
            {profile.filings_reviewed?.toLocaleString()} filings reviewed
            {profile.exchanges?.length ? ` · ${profile.exchanges.join(', ')}` : ''}
          </span>
        </div>
        {profile.source_url && (
          <a
            className="filings-link focus-ring"
            href={profile.source_url}
            target="_blank"
            rel="noopener noreferrer"
            title="Open the filing index on sec.gov"
          >
            EDGAR <ExternalLink size={12} strokeWidth={1.9} />
          </a>
        )}
      </header>

      <dl className="filings-facts">
        {facts.map(({ icon: Icon, label, value, tone }) => (
          <div key={label} className={tone ? `is-${tone}` : undefined}>
            <dt>
              <Icon size={11} strokeWidth={1.8} />
              {label}
            </dt>
            <dd>{value || '—'}</dd>
          </div>
        ))}
      </dl>

      <div className="filings-events">
        <div className="filings-events-head">
          Supervisory events disclosed · last 24 months
          <span>{flags.length}</span>
        </div>

        {flags.length === 0 ? (
          <div className="filings-clean">
            <ShieldCheck size={14} strokeWidth={1.9} />
            No restatements, delisting notices, auditor changes or late filings on record.
          </div>
        ) : (
          <ul>
            {flags.map((flag, index) => (
              <motion.li
                key={`${flag.code}-${flag.date}-${index}`}
                className={`is-${flag.severity}`}
                initial={{ opacity: 0, x: -10 }}
                animate={{ opacity: 1, x: 0 }}
                transition={{ duration: reduceMotion ? 0 : 0.3, delay: reduceMotion ? 0 : index * 0.06 }}
              >
                <AlertTriangle size={13} strokeWidth={1.9} />
                <div>
                  <strong>{flag.label}</strong>
                  <span>
                    {flag.form} · item {flag.code} · {flag.date}
                  </span>
                </div>
                <span className="filings-severity">{SEVERITY_LABEL[flag.severity] ?? flag.severity}</span>
              </motion.li>
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}
