import { AlertCircle, ListChecks, Loader2, RotateCcw } from 'lucide-react';
import { useId } from 'react';
import { Link } from 'react-router-dom';
import type { OriginValue, Run } from '@/api/workbench';
import { lifecycleSemantics } from '@/api/workbench';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { OriginValueDisplay } from './OriginValueDisplay';
import { StatusBadge } from './StatusBadge';
import { formatCost, formatDurationMs, formatTimestamp } from './format';

const modeLabel: Record<string, string> = {
  fixture: 'Fixture',
  live: 'Live',
  unknown: 'Unknown mode',
};

const unavailableEvaluation: OriginValue<string> = {
  value: null,
  origin: 'unavailable',
  unit: null,
};

function EvaluationResult({ run }: { run: Run }) {
  return (
    <div className="space-y-1.5">
      <OriginValueDisplay value={unavailableEvaluation} />
      <p className="text-xs leading-5 text-muted-foreground">
        {run.evaluationIds.length === 0
          ? 'No result was supplied in the run ledger.'
          : `${run.evaluationIds.length} evaluation ${run.evaluationIds.length === 1 ? 'record' : 'records'} linked; inspect the run for results.`}
      </p>
    </div>
  );
}

function Lifecycle({ run, compact = false }: { run: Run; compact?: boolean }) {
  const semantics = lifecycleSemantics[run.status];
  return (
    <div className="space-y-1">
      <StatusBadge semantics={semantics} rawValue={run.rawStatus} />
      {!compact ? (
        <p className="max-w-[14rem] text-xs leading-5 text-muted-foreground">
          {semantics.description}
        </p>
      ) : null}
    </div>
  );
}

function ReopenLink({ run }: { run: Run }) {
  return (
    <Link
      to={`/app/runs/${encodeURIComponent(run.id)}`}
      className="inline-flex min-h-9 items-center gap-2 font-semibold text-primary underline decoration-primary/35 underline-offset-4 hover:decoration-primary focus-visible:outline focus-visible:outline-2 focus-visible:outline-ring"
      aria-label={`Open run: ${run.objective}`}
    >
      <RotateCcw aria-hidden="true" className="h-4 w-4" />
      Open run
    </Link>
  );
}

export interface RunLedgerRecordProps {
  run: Run;
  isCancelling: boolean;
  cancellationPending: boolean;
  cancellationError: string | null;
  onCancel: (run: Run) => void;
}

function CancelControl({
  run,
  isCancelling,
  cancellationPending,
  cancellationError,
  onCancel,
}: RunLedgerRecordProps) {
  const canCancel = run.status === 'pending' || run.status === 'running';
  const cancellationErrorId = useId();

  return (
    <div className="space-y-2">
      {canCancel ? (
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={cancellationPending}
          onClick={() => onCancel(run)}
          aria-describedby={cancellationError ? cancellationErrorId : undefined}
        >
          {isCancelling ? (
            <Loader2 aria-hidden="true" className="animate-spin" />
          ) : (
            <AlertCircle aria-hidden="true" />
          )}
          {isCancelling ? 'Cancelling…' : 'Cancel run'}
        </Button>
      ) : null}
      {canCancel && cancellationError ? (
        <p
          id={cancellationErrorId}
          role="alert"
          className="max-w-xs break-all text-xs leading-5 workbench-status-failed"
        >
          Cancellation failed: {cancellationError}
        </p>
      ) : null}
    </div>
  );
}

export function RunLedgerTable({
  runs,
  cancellingRunId,
  cancellationPending,
  cancellationError,
  onCancel,
}: {
  runs: readonly Run[];
  cancellingRunId: string | null;
  cancellationPending: boolean;
  cancellationError: { runId: string; message: string } | null;
  onCancel: (run: Run) => void;
}) {
  return (
    <div className="hidden overflow-x-auto md:block">
      <table className="w-full min-w-[70rem] border-collapse text-left">
        <caption className="sr-only">Runs, most recently reported first</caption>
        <thead>
          <tr className="workbench-kicker border-b border-border">
            <th scope="col" className="py-2 pr-4 font-semibold">State</th>
            <th scope="col" className="py-2 pr-4 font-semibold">Objective / workflow</th>
            <th scope="col" className="py-2 pr-4 font-semibold">Mode</th>
            <th scope="col" className="py-2 pr-4 font-semibold">Start / duration</th>
            <th scope="col" className="py-2 pr-4 font-semibold">Evaluation result</th>
            <th scope="col" className="py-2 pr-4 font-semibold">Cost</th>
            <th scope="col" className="py-2 font-semibold">Actions</th>
          </tr>
        </thead>
        <tbody>
          {runs.map((run) => (
            <tr key={run.id} className="border-b border-border/70 last:border-b-0">
              <td className="py-4 pr-4 align-top"><Lifecycle run={run} /></td>
              <td className="max-w-sm py-4 pr-4 align-top">
                <p className="font-editorial text-base font-semibold leading-6">{run.objective}</p>
                <p className="mt-1 break-all font-mono text-xs text-muted-foreground">{run.id}</p>
                <p className="mt-2 text-sm">{run.workflowId}</p>
                <p className="font-mono text-xs text-muted-foreground">version {run.workflowVersion}</p>
                {run.taskIds.length > 0 ? (
                  <p className="mt-2 flex items-center gap-1.5 text-xs text-muted-foreground">
                    <ListChecks aria-hidden="true" className="h-3.5 w-3.5" />
                    {run.taskIds.length} reported task {run.taskIds.length === 1 ? 'record' : 'records'}
                  </p>
                ) : null}
              </td>
              <td className="py-4 pr-4 align-top">
                <Badge variant="outline">{modeLabel[run.mode] ?? run.mode}</Badge>
              </td>
              <td className="py-4 pr-4 align-top text-sm">
                <p>{formatTimestamp(run.startedAt)}</p>
                <div className="mt-2">
                  <OriginValueDisplay value={run.metrics.durationMs} format={formatDurationMs} />
                </div>
              </td>
              <td className="py-4 pr-4 align-top"><EvaluationResult run={run} /></td>
              <td className="py-4 pr-4 align-top text-sm">
                <OriginValueDisplay value={run.metrics.cost} format={formatCost} />
              </td>
              <td className="py-4 align-top">
                <div className="space-y-3">
                  <ReopenLink run={run} />
                  <CancelControl
                    run={run}
                    isCancelling={cancellingRunId === run.id}
                    cancellationPending={cancellationPending}
                    cancellationError={
                      cancellationError?.runId === run.id ? cancellationError.message : null
                    }
                    onCancel={onCancel}
                  />
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function MobileField({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <dt className="workbench-kicker">{label}</dt>
      <dd className="mt-1.5 min-w-0">{children}</dd>
    </div>
  );
}

export function RunLedgerCards({
  runs,
  cancellingRunId,
  cancellationPending,
  cancellationError,
  onCancel,
}: {
  runs: readonly Run[];
  cancellingRunId: string | null;
  cancellationPending: boolean;
  cancellationError: { runId: string; message: string } | null;
  onCancel: (run: Run) => void;
}) {
  return (
    <div className="space-y-5 md:hidden">
      {runs.map((run) => (
        <article
          key={run.id}
          aria-label={`Run: ${run.objective}`}
          className="min-w-0 border-y border-border bg-card/40 px-4 py-5"
        >
          <Lifecycle run={run} compact />
          <h3 className="font-editorial mt-4 break-words text-xl font-semibold leading-7">
            {run.objective}
          </h3>
          <p className="mt-1 break-all font-mono text-xs text-muted-foreground">{run.id}</p>

          <dl className="mt-5 grid min-w-0 gap-5">
            <MobileField label="Workflow / version">
              <p className="break-words text-sm">{run.workflowId}</p>
              <p className="break-all font-mono text-xs text-muted-foreground">
                version {run.workflowVersion}
              </p>
            </MobileField>
            <MobileField label="Mode">
              <Badge variant="outline">{modeLabel[run.mode] ?? run.mode}</Badge>
            </MobileField>
            <MobileField label="Start time">
              <p className="text-sm">{formatTimestamp(run.startedAt)}</p>
            </MobileField>
            <MobileField label="Duration">
              <OriginValueDisplay value={run.metrics.durationMs} format={formatDurationMs} />
            </MobileField>
            <MobileField label="Evaluation result">
              <EvaluationResult run={run} />
            </MobileField>
            <MobileField label="Cost">
              <OriginValueDisplay value={run.metrics.cost} format={formatCost} />
            </MobileField>
            <MobileField label="Lifecycle observation">
              <p className="text-sm leading-6 text-muted-foreground">
                {lifecycleSemantics[run.status].description}{' '}
                {run.taskIds.length > 0
                  ? `${run.taskIds.length} task ${run.taskIds.length === 1 ? 'record is' : 'records are'} reported.`
                  : 'No task records are reported in this ledger entry.'}
              </p>
            </MobileField>
          </dl>

          <div className="mt-5 flex flex-col items-start gap-3 border-t border-border pt-4">
            <ReopenLink run={run} />
            <CancelControl
              run={run}
              isCancelling={cancellingRunId === run.id}
              cancellationPending={cancellationPending}
              cancellationError={
                cancellationError?.runId === run.id ? cancellationError.message : null
              }
              onCancel={onCancel}
            />
          </div>
        </article>
      ))}
    </div>
  );
}
