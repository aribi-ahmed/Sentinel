import { useState } from 'react';
import { AnimatePresence, motion, useReducedMotion } from 'framer-motion';
import { AlertTriangle, BookOpen, ChevronDown, ExternalLink, FileText, Globe, Scale } from 'lucide-react';
import type { ComplianceBrief, ComplianceObligation } from '../services/api';
import './ComplianceBriefPanel.css';

const SEVERITY_RANK: Record<string, number> = { core: 0, standard: 1, advisory: 2 };

const SEVERITY_COPY: Record<string, string> = {
  core: 'Core control',
  standard: 'Standard control',
  advisory: 'Advisory',
};

const RETRIEVAL_COPY: Record<string, string> = {
  frameworks: 'Indexed regulatory frameworks',
  web: 'External regulatory sources',
  unavailable: 'No corpus available',
};

function isBrief(value: unknown): value is ComplianceBrief {
  return typeof value === 'object' && value !== null && 'obligations' in (value as Record<string, unknown>);
}

function citation(obligation: ComplianceObligation): string {
  return obligation.page ? `${obligation.framework} · p.${obligation.page}` : obligation.framework;
}

function ObligationCard({ obligation, index }: { obligation: ComplianceObligation; index: number }) {
  const reduceMotion = useReducedMotion();
  const [open, setOpen] = useState(false);
  const severity = SEVERITY_RANK[obligation.severity] !== undefined ? obligation.severity : 'standard';

  return (
    <motion.article
      className={`obligation-card is-${severity} ${open ? 'is-open' : ''}`}
      initial={{ opacity: 0, y: 14 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{
        duration: reduceMotion ? 0 : 0.4,
        delay: reduceMotion ? 0 : index * 0.08,
        ease: [0.22, 1, 0.36, 1],
      }}
      data-testid={`obligation-${index}`}
    >
      <header className="obligation-head">
        <span className="obligation-index">{String(index + 1).padStart(2, '0')}</span>
        <div>
          <h4>{obligation.control}</h4>
          <span className="obligation-severity">{SEVERITY_COPY[severity]}</span>
        </div>
      </header>

      <p className="obligation-requirement">{obligation.requirement}</p>

      {obligation.applies_because && (
        <div className="obligation-why">
          <Scale size={12} strokeWidth={1.9} />
          <span>{obligation.applies_because}</span>
        </div>
      )}

      <footer className="obligation-foot">
        <span className="obligation-cite">
          <BookOpen size={11} strokeWidth={1.8} />
          {citation(obligation)}
        </span>

        {obligation.excerpt && (
          <button
            type="button"
            className="obligation-toggle focus-ring"
            onClick={() => setOpen((value) => !value)}
            aria-expanded={open}
          >
            <ChevronDown size={12} strokeWidth={2} />
            {open ? 'Hide source' : 'Source passage'}
          </button>
        )}

        {obligation.url && (
          <a className="obligation-link focus-ring" href={obligation.url} target="_blank" rel="noopener noreferrer">
            <ExternalLink size={11} strokeWidth={1.8} />
          </a>
        )}
      </footer>

      <AnimatePresence initial={false}>
        {open && obligation.excerpt && (
          <motion.div
            className="obligation-source"
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: reduceMotion ? 0 : 0.3, ease: [0.22, 1, 0.36, 1] }}
          >
            <div className="obligation-source-inner">
              <div className="source-label">
                <FileText size={11} strokeWidth={1.8} />
                Retrieved verbatim — the synthesis above is written from this
              </div>
              <blockquote>{obligation.excerpt}</blockquote>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </motion.article>
  );
}

export function ComplianceBriefPanel({ value }: { value: unknown }) {
  const reduceMotion = useReducedMotion();
  const [showSources, setShowSources] = useState(false);

  if (!isBrief(value)) {
    return <div className="empty-data">No compliance obligations were retrieved.</div>;
  }

  const brief = value;
  const obligations = [...brief.obligations].sort(
    (a, b) => (SEVERITY_RANK[a.severity] ?? 1) - (SEVERITY_RANK[b.severity] ?? 1),
  );

  return (
    <div className="compliance-brief" data-testid="data-compliance">
      <header className="brief-head">
        <div>
          <div className="eyebrow">// synthesised control set</div>
          {brief.summary && <p className="brief-summary">{brief.summary}</p>}
        </div>
        <div className="brief-provenance">
          <span>retrieved from</span>
          <strong>
            {brief.retrieval === 'web' ? <Globe size={12} /> : <BookOpen size={12} />}
            {RETRIEVAL_COPY[brief.retrieval] ?? brief.retrieval}
          </strong>
        </div>
      </header>

      {brief.error && (
        <div className="brief-error" role="status">
          <AlertTriangle size={14} strokeWidth={1.9} />
          <span>{brief.error}</span>
        </div>
      )}

      {obligations.length === 0 ? (
        <div className="empty-data">
          {brief.retrieval === 'unavailable'
            ? 'No policy corpus is indexed. Run `python ingest_compliance.py` to build it.'
            : 'The frameworks returned no obligation that applies to this entity.'}
        </div>
      ) : (
        <div className="obligation-grid">
          {obligations.map((obligation, index) => (
            <ObligationCard key={`${obligation.control}-${index}`} obligation={obligation} index={index} />
          ))}
        </div>
      )}

      {brief.sources.length > 0 && (
        <div className="brief-sources">
          <button
            type="button"
            className="sources-toggle focus-ring"
            onClick={() => setShowSources((value_) => !value_)}
            aria-expanded={showSources}
            data-testid="button-toggle-passages"
          >
            <ChevronDown size={13} strokeWidth={2} />
            {showSources ? 'Hide retrieved passages' : `All ${brief.sources.length} retrieved passages`}
          </button>

          <AnimatePresence initial={false}>
            {showSources && (
              <motion.ol
                className="passage-list"
                initial={{ height: 0, opacity: 0 }}
                animate={{ height: 'auto', opacity: 1 }}
                exit={{ height: 0, opacity: 0 }}
                transition={{ duration: reduceMotion ? 0 : 0.32, ease: [0.22, 1, 0.36, 1] }}
              >
                {brief.sources.map((passage, index) => (
                  <li key={index}>
                    <div className="passage-head">
                      <span>
                        {passage.framework}
                        {passage.page ? ` · p.${passage.page}` : ''}
                      </span>
                      {typeof passage.relevance === 'number' && (
                        <span className="passage-relevance">match {Math.round(passage.relevance * 100)}%</span>
                      )}
                    </div>
                    <p>{passage.excerpt}</p>
                  </li>
                ))}
              </motion.ol>
            )}
          </AnimatePresence>
        </div>
      )}
    </div>
  );
}
