'use client';

// The app's building blocks. Pages compose these instead of styling their own
// cards, buttons and tables, so every screen looks and behaves the same.

import styles from './ui.module.css';

const cx = (...parts) => parts.filter(Boolean).join(' ');

/** Vertical stack for a page body. `actions` renders a toolbar row on top. */
export function Page({ actions, children, className }) {
  return (
    <div className={cx(styles.page, className)}>
      {actions && <div className={styles.pageActions}>{actions}</div>}
      {children}
    </div>
  );
}

export function Card({ title, description, actions, children, flush = false, className }) {
  return (
    <section className={cx(styles.card, className)}>
      {(title || actions) && (
        <header className={styles.cardHead}>
          <div className={styles.cardTitles}>
            {title && <h2 className={styles.cardTitle}>{title}</h2>}
            {description && <p className={styles.cardDesc}>{description}</p>}
          </div>
          {actions && <div className={styles.cardActions}>{actions}</div>}
        </header>
      )}
      <div className={flush ? styles.cardFlush : styles.cardBody}>{children}</div>
    </section>
  );
}

/** Two-column layout that collapses to one column on narrow screens. */
export function Columns({ children, side = 'right', className }) {
  return <div className={cx(styles.columns, side === 'left' ? styles.columnsLeft : '', className)}>{children}</div>;
}

export function Button({ variant = 'secondary', size = 'md', loading = false, children, className, disabled, ...rest }) {
  return (
    <button
      type="button"
      className={cx(styles.btn, styles[`btn_${variant}`], size === 'sm' && styles.btnSm, className)}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      {...rest}
    >
      {loading && <Spinner small />}
      {children}
    </button>
  );
}

export function Badge({ tone = 'neutral', children, className }) {
  return <span className={cx(styles.badge, styles[`tone_${tone}`], className)}>{children}</span>;
}

export function StatGrid({ children }) {
  return <div className={styles.statGrid}>{children}</div>;
}

export function Stat({ label, value, hint, tone }) {
  return (
    <div className={styles.stat} title={hint}>
      <span className={cx(styles.statValue, tone && styles[`text_${tone}`])}>{value ?? '–'}</span>
      <span className={styles.statLabel}>{label}</span>
      {hint && <span className={styles.statHint}>{hint}</span>}
    </div>
  );
}

export function Tabs({ tabs, value, onChange }) {
  return (
    <div className={styles.tabs} role="tablist">
      {tabs.map((t) => (
        <button
          key={t.key}
          type="button"
          role="tab"
          aria-selected={value === t.key}
          className={cx(styles.tab, value === t.key && styles.tabOn)}
          onClick={() => onChange(t.key)}
        >
          {t.label}
          {t.count != null && <span className={styles.tabCount}>{t.count}</span>}
        </button>
      ))}
    </div>
  );
}

/**
 * columns: [{ key, label, align?: 'right', render?: (row) => node, width? }]
 * Scrolls sideways inside its card on small screens instead of breaking the page.
 */
export function DataTable({ columns, rows, rowKey = (r, i) => r.id ?? i, empty = 'Nothing to show yet.', onRowClick }) {
  if (!rows || rows.length === 0) return <EmptyState title={empty} />;
  return (
    <div className={styles.tableWrap}>
      <table className={styles.table}>
        <thead>
          <tr>
            {columns.map((c) => (
              <th key={c.key} style={{ width: c.width, textAlign: c.align }}>{c.label}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr
              key={rowKey(row, i)}
              onClick={onRowClick ? () => onRowClick(row) : undefined}
              className={onRowClick ? styles.rowClickable : undefined}
            >
              {columns.map((c) => (
                <td key={c.key} style={{ textAlign: c.align }}>{c.render ? c.render(row) : row[c.key]}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function EmptyState({ title, description, action }) {
  return (
    <div className={styles.empty}>
      <p className={styles.emptyTitle}>{title}</p>
      {description && <p className={styles.emptyDesc}>{description}</p>}
      {action}
    </div>
  );
}

export function Notice({ tone = 'info', title, children, action }) {
  return (
    <div className={cx(styles.notice, styles[`tone_${tone}`])} role={tone === 'danger' ? 'alert' : 'status'}>
      <div className={styles.noticeText}>
        {title && <strong>{title}</strong>}
        {children && <span>{children}</span>}
      </div>
      {action}
    </div>
  );
}

export function Field({ label, hint, children, className }) {
  return (
    <label className={cx(styles.field, className)}>
      <span className={styles.fieldLabel}>{label}</span>
      {children}
      {hint && <span className={styles.fieldHint}>{hint}</span>}
    </label>
  );
}

export function Input(props) {
  return <input className={styles.input} {...props} />;
}

export function Textarea(props) {
  return <textarea className={cx(styles.input, styles.textarea)} {...props} />;
}

export function Select({ options, ...props }) {
  return (
    <select className={styles.input} {...props}>
      {options.map((o) => (
        <option key={o.value} value={o.value}>{o.label}</option>
      ))}
    </select>
  );
}

/** A row of controls that wraps on small screens. */
export function Toolbar({ children, className }) {
  return <div className={cx(styles.toolbar, className)}>{children}</div>;
}

/** Label/value pairs, e.g. an order's details. items: [[label, value], ...] */
export function DetailList({ items }) {
  return (
    <dl className={styles.details}>
      {items.map(([label, value]) => (
        <div key={label} className={styles.detailRow}>
          <dt>{label}</dt>
          <dd>{value ?? '–'}</dd>
        </div>
      ))}
    </dl>
  );
}

export function Spinner({ small = false }) {
  return <span className={cx(styles.spinner, small && styles.spinnerSm)} aria-hidden="true" />;
}

export function Skeleton({ lines = 3 }) {
  return (
    <div className={styles.skeleton} aria-label="Loading">
      {Array.from({ length: lines }, (_, i) => (
        <span key={i} className={styles.skeletonLine} />
      ))}
    </div>
  );
}

/** Money with thousands separators; currency code optional. */
export function money(value, currency = 'USD') {
  const n = Number(value);
  if (!Number.isFinite(n)) return '–';
  return n.toLocaleString(undefined, { style: 'currency', currency, maximumFractionDigits: 2 });
}

export function dateTime(iso) {
  if (!iso) return '–';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return String(iso);
  return d.toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}
