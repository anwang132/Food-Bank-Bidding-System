import { useEffect, useId, useState } from "react";
import type { ReactNode } from "react";

import { errorMessage } from "../api/client";
import type { RoundStatus } from "../api/types";

export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

export function formatDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  // Dates without time are calendar dates; avoid shifting them across time zones.
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(y!, (m ?? 1) - 1, d ?? 1).toLocaleDateString(undefined, { dateStyle: "medium" });
}

/** "5 min ago", "3 days ago"; falls back to a date after a month. */
export function timeAgo(iso: string): string {
  const s = Math.round((Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86_400) return `${Math.floor(s / 3600)} h ago`;
  const days = Math.floor(s / 86_400);
  if (days < 30) return days === 1 ? "yesterday" : `${days} days ago`;
  return new Date(iso).toLocaleDateString(undefined, { dateStyle: "medium" });
}

export function relativeDeadline(iso: string): string {
  const ms = new Date(iso).getTime() - Date.now();
  if (ms <= 0) return "deadline passed";
  const hours = Math.floor(ms / 3_600_000);
  if (hours >= 48) return `closes in ${Math.floor(hours / 24)} days`;
  if (hours >= 1) return `closes in ${hours} h`;
  return `closes in ${Math.max(1, Math.floor(ms / 60_000))} min`;
}

const STATUS_TEXT: Record<RoundStatus, string> = {
  draft: "Draft",
  open: "Open for requests",
  closed: "Closed — awaiting allocation",
  allocated: "Allocated",
  cancelled: "Cancelled",
};

export function StatusBadge({ status }: { status: RoundStatus }) {
  return (
    <span className={`badge badge-${status}`}>
      <span className="badge-dot" aria-hidden="true" />
      {STATUS_TEXT[status]}
    </span>
  );
}

export function Loading({ label = "Loading…" }: { label?: string }) {
  return (
    <p className="muted" role="status" aria-live="polite">
      <span className="spinner" aria-hidden="true" /> {label}
    </p>
  );
}

export function ErrorState({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  return (
    <div className="alert alert-error" role="alert">
      <p>{errorMessage(error)}</p>
      {onRetry && (
        <button type="button" className="btn btn-secondary" onClick={onRetry}>
          Try again
        </button>
      )}
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="empty">{children}</p>;
}

const APP_NAME = "Food Bank Allocation";

export function Logo() {
  return (
    <svg className="logo" viewBox="0 0 32 32" aria-hidden="true" focusable="false">
      <rect width="32" height="32" rx="8" fill="currentColor" />
      <path
        d="M9 15h14l-1.6 8.2a2 2 0 0 1-2 1.6h-6.8a2 2 0 0 1-2-1.6L9 15Z"
        fill="#fff"
        opacity=".95"
      />
      <path
        d="M16 13c0-3 2-5.5 5.5-5.5 0 3.2-2.2 5.5-5.5 5.5Zm0 0c0-2.4-1.6-4.4-4.4-4.4 0 2.6 1.8 4.4 4.4 4.4Z"
        fill="#bfe6cf"
      />
    </svg>
  );
}

/** Page title block; also sets the browser tab title. */
export function PageHeader({
  title,
  subtitle,
  actions,
  breadcrumb,
  badge,
}: {
  title: string;
  subtitle?: ReactNode;
  actions?: ReactNode;
  breadcrumb?: ReactNode;
  badge?: ReactNode;
}) {
  useEffect(() => {
    document.title = `${title} · ${APP_NAME}`;
  }, [title]);
  return (
    <header className="page-header">
      {breadcrumb && (
        <nav aria-label="Breadcrumb" className="breadcrumb">
          {breadcrumb}
        </nav>
      )}
      <div className="page-header-row">
        <div>
          <div className="title-row">
            <h1>{title}</h1>
            {badge}
          </div>
          {subtitle && <p className="subtitle">{subtitle}</p>}
        </div>
        {actions && <div className="actions">{actions}</div>}
      </div>
    </header>
  );
}

export function Card({
  title,
  description,
  actions,
  children,
  id,
  className = "",
}: {
  title?: string;
  description?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  id?: string;
  className?: string;
}) {
  const headingId = id ? `${id}-title` : undefined;
  return (
    <section className={`card ${className}`} aria-labelledby={headingId} id={id}>
      {(title || actions) && (
        <div className="card-header">
          <div>
            {title && <h2 id={headingId}>{title}</h2>}
            {description && <p className="muted card-description">{description}</p>}
          </div>
          {actions && <div className="actions">{actions}</div>}
        </div>
      )}
      {children}
    </section>
  );
}

export function StatCard({
  label,
  value,
  hint,
  tone = "default",
}: {
  label: string;
  value: ReactNode;
  hint?: ReactNode;
  tone?: "default" | "attention" | "good";
}) {
  return (
    <div className={`stat stat-${tone}`}>
      <div className="stat-label">{label}</div>
      <div className="stat-value">{value}</div>
      {hint && <div className="stat-hint">{hint}</div>}
    </div>
  );
}

/** Horizontal bar; the numbers are always shown as text next to it. */
export function Progress({ value, max, label }: { value: number; max: number; label: string }) {
  const pct = max > 0 ? Math.min(100, Math.round((value / max) * 100)) : 0;
  return (
    <div
      className="progress"
      role="progressbar"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={max}
      aria-valuenow={value}
    >
      <span style={{ width: `${pct}%` }} />
    </div>
  );
}

/** Initials in a colored circle; the tone is stable per name. */
export function Avatar({ name, size }: { name: string; size?: "sm" }) {
  const initials = name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((w) => w[0]!.toUpperCase())
    .join("");
  let hash = 0;
  for (const ch of name) hash = (hash * 31 + ch.charCodeAt(0)) >>> 0;
  return (
    <span
      className={size ? `avatar avatar-${size}` : "avatar"}
      data-tone={hash % 5}
      aria-hidden="true"
    >
      {initials || "?"}
    </span>
  );
}

export function EmptyState({
  icon,
  title,
  children,
  action,
}: {
  icon: ReactNode;
  title: string;
  children?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="empty-state">
      <span className="empty-icon">{icon}</span>
      <h3>{title}</h3>
      {children && <p>{children}</p>}
      {action}
    </div>
  );
}

/** "Today", "Yesterday", or a date: used for day separators. */
export function dayLabel(iso: string): string {
  const d = new Date(iso);
  const today = new Date();
  const start = (x: Date) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const days = Math.round((start(today) - start(d)) / 86_400_000);
  if (days === 0) return "Today";
  if (days === 1) return "Yesterday";
  return d.toLocaleDateString(undefined, {
    weekday: days < 7 ? "long" : undefined,
    month: "short",
    day: "numeric",
    year: d.getFullYear() === today.getFullYear() ? undefined : "numeric",
  });
}

export function formatTime(iso: string): string {
  return new Date(iso).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
}

/** Compact timestamp for lists: time today, weekday this week, else date. */
export function shortWhen(iso: string): string {
  const label = dayLabel(iso);
  if (label === "Today") return formatTime(iso);
  if (label === "Yesterday") return "Yesterday";
  const d = new Date(iso);
  const days = (Date.now() - d.getTime()) / 86_400_000;
  return d.toLocaleDateString(
    undefined,
    days < 7 ? { weekday: "short" } : { month: "short", day: "numeric" },
  );
}

export function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(n < 10 * 1024 ? 1 : 0)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

export function Alert({
  kind,
  children,
}: {
  kind: "error" | "success" | "info" | "warning";
  children: ReactNode;
}) {
  // Errors interrupt (role=alert); other messages are announced politely.
  return (
    <div
      className={`alert alert-${kind}`}
      role={kind === "error" ? "alert" : "status"}
      aria-live={kind === "error" ? "assertive" : "polite"}
    >
      {children}
    </div>
  );
}

export function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: (id: string, describedBy: string | undefined) => ReactNode;
}) {
  const id = useId();
  const hintId = hint ? `${id}-hint` : undefined;
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      {children(id, hintId)}
      {hint && (
        <small id={hintId} className="muted">
          {hint}
        </small>
      )}
    </div>
  );
}

export function TableRegion({ label, children }: { label: string; children: ReactNode }) {
  // Focusable so keyboard users can scroll wide tables on narrow screens.
  return (
    <div className="table-wrap" role="region" aria-label={label} tabIndex={0}>
      {children}
    </div>
  );
}

/** Two-step confirmation for consequential actions, without a modal. */
export function ConfirmButton({
  label,
  confirmLabel,
  prompt,
  onConfirm,
  busy,
  className = "btn btn-primary",
  disabled,
}: {
  label: string;
  confirmLabel: string;
  prompt: string;
  onConfirm: () => void;
  busy?: boolean;
  className?: string;
  disabled?: boolean;
}) {
  const [asking, setAsking] = useState(false);
  if (!asking) {
    return (
      <button
        type="button"
        className={className}
        disabled={disabled || busy}
        onClick={() => setAsking(true)}
      >
        {label}
      </button>
    );
  }
  return (
    <span className="confirm" role="group" aria-label={prompt}>
      <span>{prompt}</span>
      <button
        type="button"
        className={className}
        disabled={busy}
        autoFocus
        onClick={() => {
          onConfirm();
          setAsking(false);
        }}
      >
        {busy ? "Working…" : confirmLabel}
      </button>
      <button type="button" className="btn btn-secondary" onClick={() => setAsking(false)}>
        Cancel
      </button>
    </span>
  );
}
