import { useEffect, type ReactNode } from 'react';
import { AnimatePresence, motion, useReducedMotion } from 'framer-motion';
import { X } from 'lucide-react';
import './ModalShell.css';

export interface ModalStat {
  label: string;
  value: ReactNode;
}

interface ModalShellProps {
  isOpen: boolean;
  onClose: () => void;
  eyebrow: string;
  title: string;
  description?: string;
  /** Summary figures shown in the band under the header. */
  stats?: ModalStat[];
  /** Search, filters and sort controls. */
  controls?: ReactNode;
  footerLeft?: ReactNode;
  children: ReactNode;
  labelledBy: string;
  testId?: string;
  width?: number;
}

/**
 * The overlay surface shared by the reference library and the audit ledger, so
 * both read as the same kind of object rather than two separate designs.
 */
export function ModalShell({
  isOpen,
  onClose,
  eyebrow,
  title,
  description,
  stats,
  controls,
  footerLeft,
  children,
  labelledBy,
  testId,
  width = 940,
}: ModalShellProps) {
  const reduceMotion = useReducedMotion();

  // Escape closes, and the page behind must not scroll while it is open.
  useEffect(() => {
    if (!isOpen) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    window.addEventListener('keydown', onKeyDown);
    return () => {
      document.body.style.overflow = previousOverflow;
      window.removeEventListener('keydown', onKeyDown);
    };
  }, [isOpen, onClose]);

  return (
    <AnimatePresence>
      {isOpen && (
        <motion.div
          className="sentinel-overlay"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={{ duration: reduceMotion ? 0 : 0.2 }}
          onClick={onClose}
          data-testid={testId ? `overlay-${testId}` : undefined}
        >
          <motion.div
            className="sentinel-modal panel cornered"
            style={{ ['--modal-width' as string]: `${width}px` }}
            role="dialog"
            aria-modal="true"
            aria-labelledby={labelledBy}
            initial={{ opacity: 0, y: 22, scale: 0.985 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 14, scale: 0.99 }}
            transition={{ duration: reduceMotion ? 0 : 0.28, ease: [0.22, 1, 0.36, 1] }}
            onClick={(event) => event.stopPropagation()}
          >
            <header className="modal-masthead">
              <div>
                <div className="eyebrow">{eyebrow}</div>
                <h2 id={labelledBy}>{title}</h2>
                {description && <p>{description}</p>}
              </div>
              <button
                type="button"
                className="modal-dismiss focus-ring"
                onClick={onClose}
                aria-label={`Close ${title}`}
                data-testid={testId ? `button-close-${testId}` : undefined}
              >
                <X size={17} strokeWidth={1.6} />
              </button>
            </header>

            {Boolean(stats?.length) && (
              <div className="modal-stats">
                {stats!.map((stat) => (
                  <div key={stat.label}>
                    <span>{stat.label}</span>
                    <strong>{stat.value}</strong>
                  </div>
                ))}
              </div>
            )}

            {controls && <div className="modal-controls">{controls}</div>}

            <div className="modal-body">{children}</div>

            <footer className="modal-foot">
              <span>{footerLeft}</span>
              <span>Press ESC to close</span>
            </footer>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
