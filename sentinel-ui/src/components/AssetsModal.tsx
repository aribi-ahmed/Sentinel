import { useEffect, useMemo, useState } from 'react';
import { motion, useReducedMotion } from 'framer-motion';
import {
  ArrowDownWideNarrow,
  BookOpen,
  Braces,
  Database,
  Download,
  ExternalLink,
  File,
  FileSpreadsheet,
  FileText,
  Layers,
  Search,
  X,
} from 'lucide-react';
import type { FileMetadata } from '../services/api';
import { assetUrl, fetchAssetFiles } from '../services/api';
import { ModalShell } from './ModalShell';
import './AssetsModal.css';

interface AssetsModalProps {
  isOpen: boolean;
  onClose: () => void;
}

type Filter = 'all' | 'dataset' | 'doc';
type SortKey = 'name' | 'size' | 'recent';

const SORTS: { id: SortKey; label: string }[] = [
  { id: 'name', label: 'A→Z' },
  { id: 'size', label: 'Largest' },
  { id: 'recent', label: 'Newest' },
];

function formatBytes(bytes: number): string {
  if (!bytes) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB'];
  const index = Math.min(units.length - 1, Math.floor(Math.log(bytes) / Math.log(1024)));
  const value = bytes / 1024 ** index;
  return `${value >= 100 || index === 0 ? Math.round(value) : value.toFixed(1)} ${units[index]}`;
}

function formatAge(iso: string): string {
  const stamp = Date.parse(iso);
  if (Number.isNaN(stamp)) return 'unknown';
  const days = Math.floor((Date.now() - stamp) / 86_400_000);
  if (days <= 0) return 'today';
  if (days === 1) return 'yesterday';
  if (days < 30) return `${days}d ago`;
  if (days < 365) return `${Math.floor(days / 30)}mo ago`;
  return `${Math.floor(days / 365)}y ago`;
}

/** Extension-driven glyph so a CSV never looks like a policy PDF at a glance. */
function glyphFor(ext: string) {
  if (ext === 'csv' || ext === 'tsv') return FileSpreadsheet;
  if (ext === 'pdf') return BookOpen;
  if (ext === 'json' || ext === 'yaml' || ext === 'yml') return Braces;
  if (ext === 'txt' || ext === 'md') return FileText;
  return File;
}

const PREVIEWABLE = new Set(['pdf', 'txt', 'md', 'csv', 'json']);

export function AssetsModal({ isOpen, onClose }: AssetsModalProps) {
  const reduceMotion = useReducedMotion();
  const [files, setFiles] = useState<FileMetadata[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<Filter>('all');
  const [sort, setSort] = useState<SortKey>('name');
  const [query, setQuery] = useState('');

  useEffect(() => {
    if (!isOpen) return;

    const loadFiles = async () => {
      setLoading(true);
      setError(null);
      try {
        setFiles(await fetchAssetFiles());
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to load files');
      } finally {
        setLoading(false);
      }
    };

    void loadFiles();
  }, [isOpen]);

  const counts = useMemo(
    () => ({
      all: files.length,
      dataset: files.filter((file) => file.type === 'dataset').length,
      doc: files.filter((file) => file.type === 'doc').length,
      bytes: files.reduce((total, file) => total + file.size, 0),
    }),
    [files],
  );

  const visible = useMemo(() => {
    const needle = query.trim().toLowerCase();
    const rows = files.filter((file) => {
      if (filter !== 'all' && file.type !== filter) return false;
      if (!needle) return true;
      return `${file.name} ${file.filename} ${file.ext}`.toLowerCase().includes(needle);
    });

    return rows.sort((a, b) => {
      if (sort === 'size') return b.size - a.size;
      if (sort === 'recent') return Date.parse(b.uploaded_at) - Date.parse(a.uploaded_at);
      return a.name.localeCompare(b.name);
    });
  }, [files, filter, query, sort]);

  const largest = Math.max(1, ...visible.map((file) => file.size));

  const filters: { id: Filter; label: string; icon: typeof Layers; count: number }[] = [
    { id: 'all', label: 'All assets', icon: Layers, count: counts.all },
    { id: 'dataset', label: 'Datasets', icon: Database, count: counts.dataset },
    { id: 'doc', label: 'Documents', icon: BookOpen, count: counts.doc },
  ];

  const controls = (
    <>
      <div className="modal-search">
        <Search size={14} strokeWidth={1.6} aria-hidden="true" />
        <input
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="Filter by name or extension…"
          aria-label="Search reference assets"
          className="focus-ring"
          data-testid="input-asset-search"
        />
        {query && (
          <button type="button" onClick={() => setQuery('')} aria-label="Clear search">
            <X size={12} />
          </button>
        )}
      </div>

      <div className="modal-chips" role="group" aria-label="Asset type">
        {filters.map(({ id, label, icon: Icon, count }) => (
          <button
            key={id}
            type="button"
            className={`modal-chip focus-ring ${filter === id ? 'is-active' : ''}`}
            onClick={() => setFilter(id)}
            aria-pressed={filter === id}
            data-testid={`filter-${id}`}
          >
            <Icon size={13} strokeWidth={1.6} />
            {label}
            <span>{count}</span>
          </button>
        ))}
      </div>

      <div className="asset-sort" role="group" aria-label="Sort order">
        <ArrowDownWideNarrow size={13} strokeWidth={1.6} aria-hidden="true" />
        {SORTS.map(({ id, label }) => (
          <button
            key={id}
            type="button"
            className={`sort-option focus-ring ${sort === id ? 'is-active' : ''}`}
            onClick={() => setSort(id)}
            aria-pressed={sort === id}
          >
            {label}
          </button>
        ))}
      </div>
    </>
  );

  return (
    <ModalShell
      isOpen={isOpen}
      onClose={onClose}
      eyebrow="// reference intelligence library"
      title="Docs &amp; datasets"
      description="Source material the compliance agents retrieve against — enforcement manuals, control frameworks and sanctions lists. Every file is downloadable."
      labelledBy="asset-library-title"
      testId="assets"
      stats={[
        { label: 'Indexed', value: counts.all },
        { label: 'Datasets', value: counts.dataset },
        { label: 'Documents', value: counts.doc },
        { label: 'Volume', value: formatBytes(counts.bytes) },
      ]}
      controls={controls}
      footerLeft={`${visible.length} of ${counts.all} assets shown`}
    >
      {loading && (
        <div className="modal-loading" data-testid="status-assets-loading">
          {[0, 1, 2, 3].map((index) => (
            <div className="skeleton" key={index} style={{ height: 74 }} />
          ))}
        </div>
      )}

      {!loading && error && (
        <div className="modal-error" role="alert" data-testid="status-assets-error">
          Library unreachable: {error}
        </div>
      )}

      {!loading && !error && visible.length === 0 && (
        <div className="modal-empty" data-testid="status-assets-empty">
          <Search size={18} />
          <div>{query ? `Nothing matches “${query}”.` : 'No reference files are indexed yet.'}</div>
        </div>
      )}

      {!loading && !error && visible.length > 0 && (
        <div className="asset-grid">
          {visible.map((file, index) => {
            const Glyph = glyphFor(file.ext);
            const href = assetUrl(file.path);
            return (
              <motion.article
                className={`asset-card is-${file.type}`}
                key={file.id}
                initial={{ opacity: 0, y: 10 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{
                  duration: reduceMotion ? 0 : 0.3,
                  delay: reduceMotion ? 0 : Math.min(index * 0.03, 0.3),
                }}
                data-testid={`asset-card-${file.id}`}
              >
                <div className="asset-glyph" aria-hidden="true">
                  <Glyph size={19} strokeWidth={1.5} />
                </div>

                <div className="asset-detail">
                  <h3 title={file.filename}>{file.name}</h3>
                  <div className="asset-tags">
                    <span className="tag-ext">{file.ext || 'file'}</span>
                    <span className="tag-kind">{file.type === 'dataset' ? 'dataset' : 'document'}</span>
                    <span className="tag-size">{formatBytes(file.size)}</span>
                    <span className="tag-age">{formatAge(file.uploaded_at)}</span>
                  </div>
                  <div className="asset-meter" aria-hidden="true">
                    <span style={{ width: `${Math.max(3, (file.size / largest) * 100)}%` }} />
                  </div>
                </div>

                <div className="asset-actions">
                  {PREVIEWABLE.has(file.ext) && (
                    <a
                      href={href}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="asset-action focus-ring"
                      title={`Open ${file.filename}`}
                    >
                      <ExternalLink size={14} />
                    </a>
                  )}
                  <a
                    href={href}
                    download={file.filename}
                    className="asset-action is-primary focus-ring"
                    title={`Download ${file.filename}`}
                    data-testid={`button-download-${file.id}`}
                  >
                    <Download size={14} />
                  </a>
                </div>
              </motion.article>
            );
          })}
        </div>
      )}
    </ModalShell>
  );
}
