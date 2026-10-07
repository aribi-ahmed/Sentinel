import { useMemo, useState } from 'react';
import { AnimatePresence, motion, useReducedMotion } from 'framer-motion';
import { AlertTriangle, ChevronDown, ExternalLink, Globe, Link2, Search } from 'lucide-react';
import { parsePythonLiteral } from '../lib/pythonLiteral';
import { cleanText } from '../lib/text';
import './OsintFindings.css';

export interface OsintFinding {
  title: string;
  url: string;
  content: string;
  score: number | null;
}

interface Sweep {
  query: string;
  findings: OsintFinding[];
  /** Raw text shown when the payload held no structured results. */
  notice: string | null;
}

const PREVIEW_BLOCKS = 2;
/** Longest a single rendered paragraph may run before it is split for reading. */
const PARAGRAPH_BUDGET = 420;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function coerceFinding(value: unknown): OsintFinding | null {
  if (!isRecord(value)) return null;
  const url = String(value.url ?? value.link ?? value.href ?? '');
  const content = String(value.content ?? value.snippet ?? value.body ?? value.summary ?? value.description ?? '');
  const title = String(value.title ?? value.name ?? '');
  if (!url && !content && !title) return null;

  const rawScore = value.score ?? value.relevance ?? value.relevance_score;
  const score = typeof rawScore === 'number' ? rawScore : Number.parseFloat(String(rawScore ?? ''));

  return {
    title: cleanText(title) || 'Untitled source',
    url: url.trim(),
    content: cleanText(content),
    score: Number.isFinite(score) ? score : null,
  };
}

/** Pulls structured findings out of whatever shape the OSINT tool returned. */
function extractFindings(raw: unknown): { findings: OsintFinding[]; notice: string | null } {
  let value = raw;

  if (typeof value === 'string') {
    const text = value.trim();
    let decoded: unknown;
    try {
      decoded = JSON.parse(text);
    } catch {
      decoded = parsePythonLiteral(text);
    }
    if (decoded === undefined) return { findings: [], notice: cleanText(text) };
    value = decoded;
  }

  const items = Array.isArray(value) ? value.flat(Infinity) : [value];
  const findings = items.map(coerceFinding).filter((item): item is OsintFinding => item !== null);

  if (!findings.length) {
    return { findings: [], notice: typeof raw === 'string' ? cleanText(raw) : null };
  }
  return { findings, notice: null };
}

function toSweeps(value: unknown): Sweep[] {
  const entries = Array.isArray(value) ? value : [value];
  return entries
    .map((entry) => {
      if (!isRecord(entry)) {
        const { findings, notice } = extractFindings(entry);
        return { query: '', findings, notice };
      }
      const { findings, notice } = extractFindings(entry.results ?? entry);
      return { query: String(entry.query ?? ''), findings, notice };
    })
    .filter((sweep) => sweep.findings.length > 0 || sweep.notice);
}

function domainOf(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, '');
  } catch {
    return 'unresolved source';
  }
}

function relevanceTier(score: number | null): 'high' | 'medium' | 'low' | 'none' {
  if (score == null) return 'none';
  if (score >= 0.75) return 'high';
  if (score >= 0.5) return 'medium';
  return 'low';
}

type Block = { kind: 'heading' | 'text' | 'gap'; text: string };

/**
 * Search snippets often arrive as one unbroken block. Regrouping them at
 * sentence boundaries gives the cascade real paragraphs to show and to hide
 * behind the expand control, instead of a single wall of text.
 */
function splitLongParagraph(text: string): string[] {
  if (text.length <= PARAGRAPH_BUDGET) return [text];

  const sentences = text.split(/(?<=[.!?:])\s+/);
  const paragraphs: string[] = [];
  let buffer = '';

  for (const sentence of sentences) {
    buffer = buffer ? `${buffer} ${sentence}` : sentence;
    if (buffer.length >= PARAGRAPH_BUDGET) {
      paragraphs.push(buffer);
      buffer = '';
    }
  }
  if (buffer) {
    // Fold a short tail into the previous paragraph rather than orphaning it.
    if (buffer.length < 120 && paragraphs.length) paragraphs[paragraphs.length - 1] += ` ${buffer}`;
    else paragraphs.push(buffer);
  }

  return paragraphs.length ? paragraphs : [text];
}

/** Splits snippet prose into renderable blocks, keeping markdown-ish headings. */
function toBlocks(content: string): Block[] {
  const blocks: Block[] = [];
  for (const chunk of content.split(/\n{2,}/)) {
    const trimmed = chunk.trim();
    if (!trimmed) continue;

    // Tavily marks elided passages with a bare "[...]".
    if (/^\[\.\.\.\]$/.test(trimmed)) {
      blocks.push({ kind: 'gap', text: '' });
      continue;
    }

    const heading = trimmed.match(/^#{1,6}\s+(.+)$/);
    if (heading) {
      blocks.push({ kind: 'heading', text: heading[1].trim() });
      continue;
    }

    const withoutGaps = trimmed.replace(/\s*\[\.\.\.\]\s*/g, ' … ').replace(/\s{2,}/g, ' ');
    for (const paragraph of splitLongParagraph(withoutGaps)) {
      blocks.push({ kind: 'text', text: paragraph });
    }
  }
  return blocks;
}

function FindingBlocks({ blocks }: { blocks: Block[] }) {
  return (
    <>
      {blocks.map((block, index) => {
        if (block.kind === 'gap') return <div className="finding-gap" key={index} aria-hidden="true" />;
        if (block.kind === 'heading') return <h5 key={index}>{block.text}</h5>;
        return <p key={index}>{block.text}</p>;
      })}
    </>
  );
}

function FindingCard({ finding, rank, total }: { finding: OsintFinding; rank: number; total: number }) {
  const reduceMotion = useReducedMotion();
  const [open, setOpen] = useState(false);

  const blocks = useMemo(() => toBlocks(finding.content), [finding.content]);
  const preview = blocks.slice(0, PREVIEW_BLOCKS);
  const rest = blocks.slice(PREVIEW_BLOCKS);
  const tier = relevanceTier(finding.score);
  const percent = finding.score != null ? Math.round(finding.score * 100) : null;
  const domain = domainOf(finding.url);

  return (
    <motion.li
      className={`osint-finding is-${tier} ${open ? 'is-open' : ''}`}
      initial={{ opacity: 0, x: -22 }}
      animate={{ opacity: 1, x: 0 }}
      transition={{
        duration: reduceMotion ? 0 : 0.45,
        delay: reduceMotion ? 0 : rank * 0.09,
        ease: [0.22, 1, 0.36, 1],
      }}
      data-testid={`osint-finding-${rank}`}
    >
      <div className="finding-spine" aria-hidden="true">
        <span className="finding-rank">{String(rank + 1).padStart(2, '0')}</span>
        <span className="finding-thread" />
      </div>

      <div className="finding-card">
        <div className="finding-top">
          <span className="finding-domain">
            <Globe size={11} strokeWidth={1.7} />
            {domain}
          </span>
          {percent != null && (
            <div className="finding-relevance" title={`Relevance ${percent}%`}>
              <span className="relevance-label">relevance</span>
              <span className="relevance-track">
                <motion.i
                  initial={{ width: 0 }}
                  animate={{ width: `${percent}%` }}
                  transition={{ duration: reduceMotion ? 0 : 0.7, delay: reduceMotion ? 0 : rank * 0.09 + 0.2 }}
                />
              </span>
              <strong>{percent}%</strong>
            </div>
          )}
        </div>

        <h4 className="finding-title">
          {finding.url ? (
            <a href={finding.url} target="_blank" rel="noopener noreferrer">
              <span>{finding.title}</span>
              <ExternalLink size={13} strokeWidth={1.8} />
            </a>
          ) : (
            <span>{finding.title}</span>
          )}
        </h4>

        <div className="finding-content">
          <FindingBlocks blocks={preview} />

          <AnimatePresence initial={false}>
            {open && rest.length > 0 && (
              <motion.div
                key="rest"
                className="finding-rest"
                initial={{ height: 0, opacity: 0 }}
                animate={{ height: 'auto', opacity: 1 }}
                exit={{ height: 0, opacity: 0 }}
                transition={{ duration: reduceMotion ? 0 : 0.35, ease: [0.22, 1, 0.36, 1] }}
              >
                <FindingBlocks blocks={rest} />
              </motion.div>
            )}
          </AnimatePresence>
        </div>

        <div className="finding-foot">
          {rest.length > 0 ? (
            <button
              type="button"
              className="finding-toggle focus-ring"
              onClick={() => setOpen((value) => !value)}
              aria-expanded={open}
              data-testid={`osint-toggle-${rank}`}
            >
              <ChevronDown size={13} strokeWidth={2} />
              {open ? 'Collapse extract' : `Read full extract · ${rest.length} more passages`}
            </button>
          ) : (
            <span className="finding-meta">source {rank + 1} of {total}</span>
          )}

          {finding.url && (
            <a
              className="finding-url focus-ring"
              href={finding.url}
              target="_blank"
              rel="noopener noreferrer"
              title={finding.url}
            >
              <Link2 size={11} strokeWidth={1.8} />
              <span>{finding.url}</span>
            </a>
          )}
        </div>
      </div>
    </motion.li>
  );
}

export function OsintFindings({ value }: { value: unknown }) {
  const sweeps = useMemo(() => toSweeps(value), [value]);

  if (!sweeps.length) {
    return <div className="empty-data">No OSINT findings returned.</div>;
  }

  return (
    <div className="osint-panel" data-testid="data-osint">
      {sweeps.map((sweep, sweepIndex) => {
        const scored = sweep.findings.filter((finding) => finding.score != null);
        const average = scored.length
          ? Math.round((scored.reduce((total, finding) => total + (finding.score ?? 0), 0) / scored.length) * 100)
          : null;

        return (
          <section className="osint-sweep" key={sweepIndex}>
            <header className="osint-brief">
              <div className="osint-brief-copy">
                <div className="eyebrow">// open-source sweep</div>
                {sweep.query && (
                  <div className="osint-query">
                    <Search size={13} strokeWidth={1.8} />
                    <span>{sweep.query}</span>
                  </div>
                )}
              </div>
              <div className="osint-brief-stats">
                <div>
                  <span>sources</span>
                  <strong>{String(sweep.findings.length).padStart(2, '0')}</strong>
                </div>
                {average != null && (
                  <div>
                    <span>avg relevance</span>
                    <strong>{average}%</strong>
                  </div>
                )}
              </div>
            </header>

            {sweep.notice && (
              <div className="osint-notice" role="status">
                <AlertTriangle size={14} strokeWidth={1.8} />
                <p>{sweep.notice}</p>
              </div>
            )}

            {sweep.findings.length > 0 && (
              <ol className="osint-cascade">
                {sweep.findings.map((finding, index) => (
                  <FindingCard
                    key={`${finding.url}-${index}`}
                    finding={finding}
                    rank={index}
                    total={sweep.findings.length}
                  />
                ))}
              </ol>
            )}
          </section>
        );
      })}
    </div>
  );
}
