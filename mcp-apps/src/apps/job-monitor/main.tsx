/**
 * Job Monitor Dashboard App (MA-201 ~ MA-205)
 *
 * Real-time dashboard for monitoring inference jobs.
 * Features:
 * - Active job listing with status
 * - GPU/memory utilization charts
 * - Log streaming panel
 * - Job lifecycle controls (abort/restart)
 */

import React, { useState, useMemo } from 'react';
import ReactDOM from 'react-dom/client';
import { useJobs, useFetch } from '@/lib/hooks';
import { umrsClient, type Job, type JobAttempt } from '@/lib/umrs-client';
import {
  Card,
  Select,
  Button,
  StatusBadge,
  Tabs,
  LoadingState,
  ErrorState,
  EmptyState,
} from '@/components/ui';
import '@/styles/global.css';

// Job Row Component
function JobRow({
  job,
  isSelected,
  onSelect,
  onAbort,
  onRetry,
}: {
  job: Job;
  isSelected: boolean;
  onSelect: () => void;
  onAbort: () => void;
  onRetry: () => void;
}) {
  const canAbort = ['pending', 'queued', 'submitted', 'running'].includes(job.status);
  const canRetry = job.status === 'failed';

  return (
    <div
      className={`p-4 border-b border-[var(--color-border)] hover:bg-[var(--color-bg-tertiary)] cursor-pointer transition-colors ${
        isSelected ? 'bg-[var(--color-bg-tertiary)]' : ''
      }`}
      onClick={onSelect}
    >
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <StatusBadge status={job.status} />
          <div>
            <p className="text-sm font-medium text-[var(--color-text-primary)]">
              {job.job_id}
            </p>
            <p className="text-xs text-[var(--color-text-muted)]">
              {job.job_type} • {job.project_id}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-xs text-[var(--color-text-muted)]">
            {new Date(job.updated_at).toLocaleTimeString()}
          </span>
          {canAbort && (
            <Button
              size="sm"
              variant="danger"
              onClick={(e) => {
                e?.stopPropagation();
                onAbort();
              }}
            >
              Abort
            </Button>
          )}
          {canRetry && (
            <Button
              size="sm"
              variant="secondary"
              onClick={(e) => {
                e?.stopPropagation();
                onRetry();
              }}
            >
              Retry
            </Button>
          )}
        </div>
      </div>
      {job.error && (
        <p className="mt-2 text-xs text-[var(--color-error)]">{job.error}</p>
      )}
    </div>
  );
}

// Job Detail Panel
function JobDetail({ job }: { job: Job }) {
  const [activeTab, setActiveTab] = useState('details');
  const { data: attempts } = useFetch<JobAttempt[]>(
    () => umrsClient.getJobAttempts(job.job_id),
    [job.job_id]
  );

  return (
    <div className="h-full flex flex-col">
      <Tabs
        tabs={[
          { id: 'details', label: 'Details' },
          { id: 'attempts', label: `Attempts (${attempts?.length || 0})` },
          { id: 'logs', label: 'Logs' },
        ]}
        activeTab={activeTab}
        onChange={setActiveTab}
      />

      <div className="flex-1 overflow-y-auto p-4">
        {activeTab === 'details' && (
          <div className="space-y-4">
            <div>
              <h4 className="text-xs font-medium text-[var(--color-text-secondary)] mb-1">
                Job ID
              </h4>
              <p className="text-sm font-mono">{job.job_id}</p>
            </div>
            <div>
              <h4 className="text-xs font-medium text-[var(--color-text-secondary)] mb-1">
                Type
              </h4>
              <p className="text-sm">{job.job_type}</p>
            </div>
            <div>
              <h4 className="text-xs font-medium text-[var(--color-text-secondary)] mb-1">
                Status
              </h4>
              <StatusBadge status={job.status} />
            </div>
            <div>
              <h4 className="text-xs font-medium text-[var(--color-text-secondary)] mb-1">
                Created
              </h4>
              <p className="text-sm">{new Date(job.created_at).toLocaleString()}</p>
            </div>
            <div>
              <h4 className="text-xs font-medium text-[var(--color-text-secondary)] mb-1">
                Spec
              </h4>
              <pre className="text-xs p-2 bg-[var(--color-bg-tertiary)] rounded overflow-x-auto">
                {JSON.stringify(job.spec, null, 2)}
              </pre>
            </div>
          </div>
        )}

        {activeTab === 'attempts' && (
          <div className="space-y-3">
            {!attempts || attempts.length === 0 ? (
              <EmptyState title="No attempts yet" />
            ) : (
              attempts.map((attempt) => (
                <Card key={attempt.attempt_id} padding="sm">
                  <div className="flex items-center justify-between mb-2">
                    <span className="text-sm font-medium">
                      Attempt #{attempt.attempt_no}
                    </span>
                    <StatusBadge status={attempt.status} />
                  </div>
                  {attempt.backend_ref && (
                    <p className="text-xs text-[var(--color-text-muted)]">
                      Backend: {attempt.backend_ref}
                    </p>
                  )}
                  {attempt.status_detail && (
                    <p className="text-xs text-[var(--color-text-secondary)] mt-1">
                      {attempt.status_detail}
                    </p>
                  )}
                  <p className="text-xs text-[var(--color-text-muted)] mt-2">
                    Started: {attempt.started_at ? new Date(attempt.started_at).toLocaleString() : 'N/A'}
                  </p>
                </Card>
              ))
            )}
          </div>
        )}

        {activeTab === 'logs' && (
          <div className="h-full flex flex-col">
            <div className="flex-1 bg-[var(--color-bg-tertiary)] rounded p-3 font-mono text-xs overflow-y-auto">
              <p className="text-[var(--color-text-muted)]">
                Log streaming not yet implemented.
              </p>
              <p className="text-[var(--color-text-muted)] mt-2">
                Connect to: /jobs/{job.job_id}/logs
              </p>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

// Stats Card
function StatsCard({ label, value, subtext }: { label: string; value: string | number; subtext?: string }) {
  return (
    <Card padding="sm">
      <p className="text-xs text-[var(--color-text-secondary)]">{label}</p>
      <p className="text-2xl font-semibold text-[var(--color-text-primary)]">{value}</p>
      {subtext && <p className="text-xs text-[var(--color-text-muted)]">{subtext}</p>}
    </Card>
  );
}

// Main App Component
function JobMonitor() {
  const [statusFilter, setStatusFilter] = useState('');
  const [selectedJob, setSelectedJob] = useState<Job | null>(null);

  const { jobs, loading, error, abortJob, retryJob } = useJobs(
    statusFilter ? { status: statusFilter } : undefined,
    5000
  );

  // Compute stats
  const stats = useMemo(() => {
    if (!jobs) return { total: 0, running: 0, pending: 0, failed: 0 };
    return {
      total: jobs.length,
      running: jobs.filter((j) => j.status === 'running').length,
      pending: jobs.filter((j) => ['pending', 'queued', 'submitted'].includes(j.status)).length,
      failed: jobs.filter((j) => j.status === 'failed').length,
    };
  }, [jobs]);

  return (
    <div className="h-screen flex flex-col bg-[var(--color-bg-primary)]">
      {/* Header */}
      <header className="border-b border-[var(--color-border)] p-4">
        <h1 className="text-xl font-semibold text-[var(--color-text-primary)]">
          Job Monitor Dashboard
        </h1>
        <p className="text-sm text-[var(--color-text-secondary)]">
          Real-time monitoring of inference and processing jobs
        </p>
      </header>

      {/* Stats Bar */}
      <div className="p-4 border-b border-[var(--color-border)] bg-[var(--color-bg-secondary)]">
        <div className="grid grid-cols-4 gap-4 max-w-2xl">
          <StatsCard label="Total Jobs" value={stats.total} />
          <StatsCard label="Running" value={stats.running} />
          <StatsCard label="Pending" value={stats.pending} />
          <StatsCard label="Failed" value={stats.failed} />
        </div>
      </div>

      {/* Main Content */}
      <div className="flex-1 flex overflow-hidden">
        {/* Job List */}
        <div className="w-1/2 border-r border-[var(--color-border)] flex flex-col">
          {/* Filter Bar */}
          <div className="p-3 border-b border-[var(--color-border)]">
            <Select
              options={[
                { value: '', label: 'All Statuses' },
                { value: 'pending', label: 'Pending' },
                { value: 'running', label: 'Running' },
                { value: 'succeeded', label: 'Succeeded' },
                { value: 'failed', label: 'Failed' },
              ]}
              value={statusFilter}
              onChange={setStatusFilter}
            />
          </div>

          {/* Job List */}
          <div className="flex-1 overflow-y-auto">
            {loading && <LoadingState message="Loading jobs..." />}
            {error && <ErrorState error={error} />}
            {!loading && !error && (!jobs || jobs.length === 0) && (
              <EmptyState
                title="No jobs"
                description="No jobs match the current filter"
              />
            )}
            {jobs &&
              jobs.map((job) => (
                <JobRow
                  key={job.job_id}
                  job={job}
                  isSelected={selectedJob?.job_id === job.job_id}
                  onSelect={() => setSelectedJob(job)}
                  onAbort={() => abortJob(job.job_id)}
                  onRetry={() => retryJob(job.job_id)}
                />
              ))}
          </div>
        </div>

        {/* Detail Panel */}
        <div className="w-1/2">
          {selectedJob ? (
            <JobDetail job={selectedJob} />
          ) : (
            <div className="h-full flex items-center justify-center">
              <EmptyState
                title="Select a job"
                description="Click on a job to view details"
              />
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// Mount app
ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <JobMonitor />
  </React.StrictMode>
);
