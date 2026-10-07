import { useMemo, useState } from 'react';
import { motion, useReducedMotion } from 'framer-motion';
import { Check, Database, FileDown, FileText, Minus, RotateCcw, Search, X } from 'lucide-react';
import type { AuditRecord } from '../services/api';
import { reportUrl } from '../services/api';
import { ModalShell } from './ModalShell';
import './LedgerModal.css';

type Filter = 'all' | 'reported' | 'pending';

const BAND_TONE: Record<string, string> = {
  MINIMAL: 'safe',
  LOW: 'safe',
  MODERATE: 'watch',
  ELEVATED: 'alert',
  SEVERE: 'critical',
};

function shortId(value: string) {
  return value.length > 13 ? `${value.slice(0, 8)}…${value.slice(-4)}` : value;
}

function decisionOf(record: AuditRecord): 'approved' | 'rejected' | 'pending' {
  const raw = String(record.Approved || '').toLowerCase();
  if (raw.includes('yes')) return 'approved';
  if (raw.includes('no')) return 'rejected';
  return 'pending';
}

interface LedgerModalProps {
  isOpen: boolean;
  onClose: () => void;
  records: AuditRecord[];
  loading: boolean;
  error: string | null;
  onReload: () => void;
}

export function LedgerModal({ isOpen, onClose, records, loading, error, onReload }: LedgerModalProps) {
  const reduceMotion = useReducedMotion();
  const [query, setQuery] = useState('');
  const [filter, setFilter] = useState<Filter>('all');

  // Reset from the event that closes the modal rather than from an effect, so
  // the next opening starts clean without an extra render pass.
  const handleClose = () => {
    setQuery('');
    setFilter('all');
    onClose();
  };

  const reported = records.filter((record) => record['Has Report']).length;

  const visible = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return records.filter((record) => {
      if (filter === 'reported' && !record['Has Report']) return false;
      if (filter === 'pending' && decisionOf(record) !== 'pending') return false;
      if (!needle) return true;
      return `${record.Company} ${record.Ticker} ${record['Database ID']} ${record['Risk Level']}`
        .toLowerCase()
        .includes(needle);
    });
  }, [records, filter, query]);

  const filters: { id: Filter; label: string; count: number }[] = [
    { id: 'all', label: 'All records', count: records.length },
    { id: 'reported', label: 'With report', count: reported },
    { id: 'pending', label: 'Undecided', count: records.filter((r) => decisionOf(r) === 'pending').length },
  ];

  const controls = (
    <>
      <div className="modal-search">
        <Search size={14} strokeWidth={1.6} aria-hidden="true" />
        <input
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="Filter by company, ticker or id…"
          aria-label="Search the audit ledger"
          className="focus-ring"
          data-testid="input-ledger-search"
        />
        {query && (
          <button type="button" onClick={() => setQuery('')} aria-label="Clear search">
            <X size={12} />
          </button>
        )}
      </div>

      <div className="modal-chips" role="group" aria-label="Ledger filter">
        {filters.map(({ id, label, count }) => (
          <button
            key={id}
            type="button"
            className={`modal-chip focus-ring ${filter === id ? 'is-active' : ''}`}
            onClick={() => setFilter(id)}
            aria-pressed={filter === id}
            data-testid={`ledger-filter-${id}`}
          >
            {label}
            <span>{count}</span>
          </button>
        ))}
      </div>

      <button type="button" className="modal-chip focus-ring" onClick={onReload} data-testid="button-retry-ledger">
        <RotateCcw size={13} strokeWidth={1.8} />
        Reload
      </button>
    </>
  );

  return (
    <ModalShell
      isOpen={isOpen}
      onClose={handleClose}
      eyebrow="// immutable activity trail"
      title="SQL audit ledger"
      description="Every investigation committed to the database, with the executive report exactly as it was approved."
      labelledBy="ledger-title"
      testId="ledger"
      width={1080}
      stats={[
        { label: 'Records', value: records.length },
        { label: 'With report', value: reported },
        { label: 'Approved', value: records.filter((r) => decisionOf(r) === 'approved').length },
        { label: 'Rejected', value: records.filter((r) => decisionOf(r) === 'rejected').length },
      ]}
      controls={controls}
      footerLeft={`${visible.length} of ${records.length} records shown`}
    >
      {loading && (
        <div className="modal-loading" data-testid="status-ledger-loading">
          {[0, 1, 2, 3, 4].map((index) => (
            <div className="skeleton" key={index} style={{ height: 46 }} />
          ))}
        </div>
      )}

      {!loading && error && (
        <div className="modal-error" role="alert" data-testid="status-ledger-error">
          Unable to read ledger: {error}
        </div>
      )}

      {!loading && !error && visible.length === 0 && (
        <div className="modal-empty" data-testid="status-ledger-empty">
          <Database size={18} />
          <div>{query ? `Nothing matches “${query}”.` : 'No investigations have been committed yet.'}</div>
        </div>
      )}

      {!loading && !error && visible.length > 0 && (
        <div className="ledger-scroll">
          <table className="ledger-grid">
            <thead>
              <tr>
                <th>Investigation</th>
                <th>Company</th>
                <th>Ticker</th>
                <th>Risk</th>
                <th>Decision</th>
                <th>Committed</th>
                <th className="is-actions">Report</th>
              </tr>
            </thead>
            <tbody>
              {visible.map((record, index) => {
                const id = String(record['Database ID'] || '');
                const decision = decisionOf(record);
                const band = String(record['Risk Level'] || '').toUpperCase();
                const tone = BAND_TONE[band] ?? 'unknown';

                return (
                  <motion.tr
                    key={`${id}-${index}`}
                    initial={{ opacity: 0, y: 6 }}
                    animate={{ opacity: 1, y: 0 }}
                    transition={{
                      duration: reduceMotion ? 0 : 0.28,
                      delay: reduceMotion ? 0 : Math.min(index * 0.015, 0.25),
                    }}
                    data-testid={`row-ledger-${id}`}
                  >
                    <td className="is-id" title={id}>{shortId(id)}</td>
                    <td className="is-company">{record.Company || 'Unknown entity'}</td>
                    <td className="is-ticker">{record.Ticker || 'N/A'}</td>
                    <td>
                      <span className={`ledger-band is-${tone}`}>{band || 'N/A'}</span>
                    </td>
                    <td>
                      <span className={`ledger-decision is-${decision}`}>
                        {decision === 'approved' && <Check size={11} strokeWidth={2.5} />}
                        {decision === 'rejected' && <X size={11} strokeWidth={2.5} />}
                        {decision === 'pending' && <Minus size={11} strokeWidth={2.5} />}
                        {decision}
                      </span>
                    </td>
                    <td className="is-date">{record['Created At'] || '—'}</td>
                    <td className="is-actions">
                      {record['Has Report'] ? (
                        <div className="ledger-downloads">
                          <a
                            className="ledger-download focus-ring"
                            href={reportUrl(id, 'md')}
                            title={`Download Markdown report for ${record.Company}`}
                            data-testid={`download-md-${id}`}
                          >
                            <FileText size={12} strokeWidth={1.9} />
                            MD
                          </a>
                          <a
                            className="ledger-download is-primary focus-ring"
                            href={reportUrl(id, 'pdf')}
                            title={`Download PDF report for ${record.Company}`}
                            data-testid={`download-pdf-${id}`}
                          >
                            <FileDown size={12} strokeWidth={1.9} />
                            PDF
                          </a>
                        </div>
                      ) : (
                        <span className="ledger-noreport">not generated</span>
                      )}
                    </td>
                  </motion.tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </ModalShell>
  );
}
