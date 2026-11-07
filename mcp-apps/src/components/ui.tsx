/**
 * Shared UI Components for MCP Apps
 */

import React from 'react';

// Loading Spinner
export function Spinner({ size = 'md' }: { size?: 'sm' | 'md' | 'lg' }) {
  const sizeClass = {
    sm: 'w-4 h-4',
    md: 'w-6 h-6',
    lg: 'w-8 h-8',
  }[size];

  return (
    <div
      className={`${sizeClass} border-2 border-[var(--color-border)] border-t-[var(--color-accent)] rounded-full animate-spin`}
    />
  );
}

// Loading State
export function LoadingState({ message = 'Loading...' }: { message?: string }) {
  return (
    <div className="flex flex-col items-center justify-center p-8 text-[var(--color-text-secondary)]">
      <Spinner size="lg" />
      <p className="mt-4">{message}</p>
    </div>
  );
}

// Error State
export function ErrorState({
  error,
  onRetry,
}: {
  error: Error | string;
  onRetry?: () => void;
}) {
  const message = error instanceof Error ? error.message : error;

  return (
    <div className="p-4 bg-[rgba(248,81,73,0.1)] border border-[var(--color-error)] rounded-lg">
      <p className="text-[var(--color-error)] font-medium">Error</p>
      <p className="text-[var(--color-text-secondary)] mt-1">{message}</p>
      {onRetry && (
        <button className="btn btn-secondary mt-3" onClick={onRetry}>
          Retry
        </button>
      )}
    </div>
  );
}

// Empty State
export function EmptyState({
  icon,
  title,
  description,
  action,
}: {
  icon?: React.ReactNode;
  title: string;
  description?: string;
  action?: React.ReactNode;
}) {
  return (
    <div className="flex flex-col items-center justify-center p-8 text-center">
      {icon && <div className="text-4xl mb-4 opacity-50">{icon}</div>}
      <h3 className="text-lg font-medium text-[var(--color-text-primary)]">{title}</h3>
      {description && (
        <p className="mt-2 text-[var(--color-text-secondary)]">{description}</p>
      )}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

// Card Component
export function Card({
  children,
  className = '',
  padding = 'md',
  onClick,
}: {
  children: React.ReactNode;
  className?: string;
  padding?: 'none' | 'sm' | 'md' | 'lg';
  onClick?: () => void;
}) {
  const paddingClass = {
    none: '',
    sm: 'p-3',
    md: 'p-4',
    lg: 'p-6',
  }[padding];

  return (
    <div
      className={`bg-[var(--color-bg-secondary)] border border-[var(--color-border)] rounded-lg ${paddingClass} ${className} ${onClick ? 'cursor-pointer hover:border-[var(--color-accent)]' : ''}`}
      onClick={onClick}
      role={onClick ? 'button' : undefined}
      tabIndex={onClick ? 0 : undefined}
      onKeyDown={onClick ? (e) => e.key === 'Enter' && onClick() : undefined}
    >
      {children}
    </div>
  );
}

// Badge Component
export function Badge({
  variant = 'info',
  children,
  className = '',
}: {
  variant?: 'success' | 'warning' | 'error' | 'info';
  children: React.ReactNode;
  className?: string;
}) {
  const variantClass = {
    success: 'bg-[rgba(63,185,80,0.2)] text-[var(--color-success)]',
    warning: 'bg-[rgba(210,153,34,0.2)] text-[var(--color-warning)]',
    error: 'bg-[rgba(248,81,73,0.2)] text-[var(--color-error)]',
    info: 'bg-[rgba(88,166,255,0.2)] text-[var(--color-info)]',
  }[variant];

  return (
    <span
      className={`inline-flex items-center px-2 py-0.5 text-xs font-medium rounded-full ${variantClass} ${className}`}
    >
      {children}
    </span>
  );
}

// Status Badge for Jobs
export function StatusBadge({ status }: { status: string }) {
  const variant = {
    pending: 'info' as const,
    queued: 'info' as const,
    submitted: 'info' as const,
    running: 'warning' as const,
    succeeded: 'success' as const,
    failed: 'error' as const,
  }[status] || 'info';

  return <Badge variant={variant}>{status}</Badge>;
}

// Button Component
export function Button({
  variant = 'primary',
  size = 'md',
  disabled = false,
  loading = false,
  onClick,
  children,
  className = '',
}: {
  variant?: 'primary' | 'secondary' | 'danger';
  size?: 'sm' | 'md' | 'lg';
  disabled?: boolean;
  loading?: boolean;
  onClick?: (e?: React.MouseEvent<HTMLButtonElement>) => void;
  children: React.ReactNode;
  className?: string;
}) {
  const variantClass = {
    primary: 'bg-[var(--color-accent)] text-white hover:bg-[var(--color-accent-hover)]',
    secondary:
      'bg-[var(--color-bg-tertiary)] text-[var(--color-text-primary)] border border-[var(--color-border)] hover:bg-[var(--color-bg-hover)]',
    danger: 'bg-[var(--color-error)] text-white hover:opacity-90',
  }[variant];

  const sizeClass = {
    sm: 'px-2 py-1 text-xs',
    md: 'px-3 py-1.5 text-sm',
    lg: 'px-4 py-2 text-base',
  }[size];

  return (
    <button
      className={`inline-flex items-center justify-center font-medium rounded-md transition-colors ${variantClass} ${sizeClass} ${className} ${
        disabled || loading ? 'opacity-50 cursor-not-allowed' : ''
      }`}
      disabled={disabled || loading}
      onClick={onClick}
    >
      {loading && <Spinner size="sm" />}
      {loading && <span className="ml-2">{children}</span>}
      {!loading && children}
    </button>
  );
}

// Input Component
export function Input({
  type = 'text',
  placeholder,
  value,
  onChange,
  className = '',
}: {
  type?: 'text' | 'number' | 'search';
  placeholder?: string;
  value: string;
  onChange: (value: string) => void;
  className?: string;
}) {
  return (
    <input
      type={type}
      className={`w-full px-3 py-2 text-sm bg-[var(--color-bg-tertiary)] border border-[var(--color-border)] rounded-md text-[var(--color-text-primary)] placeholder-[var(--color-text-muted)] focus:outline-none focus:border-[var(--color-accent)] transition-colors ${className}`}
      placeholder={placeholder}
      value={value}
      onChange={(e) => onChange(e.target.value)}
    />
  );
}

// Select Component
export function Select({
  options,
  value,
  onChange,
  placeholder,
  className = '',
}: {
  options: Array<{ value: string; label: string }>;
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  className?: string;
}) {
  return (
    <select
      className={`w-full px-3 py-2 text-sm bg-[var(--color-bg-tertiary)] border border-[var(--color-border)] rounded-md text-[var(--color-text-primary)] focus:outline-none focus:border-[var(--color-accent)] transition-colors ${className}`}
      value={value}
      onChange={(e) => onChange(e.target.value)}
    >
      {placeholder && (
        <option value="" disabled>
          {placeholder}
        </option>
      )}
      {options.map((opt) => (
        <option key={opt.value} value={opt.value}>
          {opt.label}
        </option>
      ))}
    </select>
  );
}

// Slider Component
export function Slider({
  min,
  max,
  step = 1,
  value,
  onChange,
  label,
  showValue = true,
}: {
  min: number;
  max: number;
  step?: number;
  value: number;
  onChange: (value: number) => void;
  label?: string;
  showValue?: boolean;
}) {
  return (
    <div className="w-full">
      {(label || showValue) && (
        <div className="flex justify-between mb-1 text-sm">
          {label && <span className="text-[var(--color-text-secondary)]">{label}</span>}
          {showValue && <span className="text-[var(--color-text-primary)]">{value}</span>}
        </div>
      )}
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="w-full h-2 bg-[var(--color-bg-tertiary)] rounded-lg appearance-none cursor-pointer accent-[var(--color-accent)]"
      />
    </div>
  );
}

// Tabs Component
export function Tabs({
  tabs,
  activeTab,
  onChange,
}: {
  tabs: Array<{ id: string; label: string; icon?: React.ReactNode }>;
  activeTab: string;
  onChange: (id: string) => void;
}) {
  return (
    <div className="flex border-b border-[var(--color-border)]">
      {tabs.map((tab) => (
        <button
          key={tab.id}
          onClick={() => onChange(tab.id)}
          className={`flex items-center px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors ${
            activeTab === tab.id
              ? 'border-[var(--color-accent)] text-[var(--color-accent)]'
              : 'border-transparent text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)]'
          }`}
        >
          {tab.icon && <span className="mr-2">{tab.icon}</span>}
          {tab.label}
        </button>
      ))}
    </div>
  );
}
