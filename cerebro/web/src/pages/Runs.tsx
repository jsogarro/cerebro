import { useEffect, useRef, useState, type ReactNode } from 'react';
import axios from 'axios';
import { FileClock, RefreshCw } from 'lucide-react';
import { useCancelRun, useRuns } from '@/api/runs';
import { lifecycleSemantics } from '@/api/workbench';
import { IssuesBanner } from '@/components/runs/IssuesBanner';
import { RunLedgerCards, RunLedgerTable } from '@/components/runs/RunLedgerRecord';
import { WorkbenchStateNotice } from '@/components/runs/WorkbenchStateNotice';
import { describeQueryError } from '@/components/runs/format';
import { Button } from '@/components/ui/button';

function describeCancellationError(error: unknown): string {
  if (axios.isAxiosError(error)) {
    const data: unknown = error.response?.data;
    if (
      typeof data === 'object' &&
      data !== null &&
      'detail' in data &&
      typeof data.detail === 'string'
    ) {
      return data.detail;
    }
  }
  return describeQueryError(error);
}

export function Runs() {
  const query = useRuns();
  const cancelMutation = useCancelRun();
  const contract = query.data;
  const [cancellingRunId, setCancellingRunId] = useState<string | null>(null);
  const [cancellationError, setCancellationError] = useState<{
    runId: string;
    message: string;
  } | null>(null);
  const cancellationInFlight = useRef(false);
  const previousLifecycle = useRef<Map<string, string> | null>(null);
  const lifecycleAnnouncer = useRef<HTMLParagraphElement>(null);

  useEffect(() => {
    if (!contract || (contract.presentation.state !== 'ready' && contract.presentation.state !== 'partial')) {
      return;
    }

    const currentLifecycle = new Map(
      contract.items.map((run) => [run.id, lifecycleSemantics[run.status].label]),
    );
    if (previousLifecycle.current !== null) {
      const updates = contract.items
        .map((run) => {
          const label = lifecycleSemantics[run.status].label;
          const previousLabel = previousLifecycle.current?.get(run.id);
          return previousLabel !== undefined && previousLabel !== label
            ? `${run.objective} is now ${label}.`
            : null;
        })
        .filter((update): update is string => update !== null);
      if (updates.length > 0) {
        if (lifecycleAnnouncer.current) {
          lifecycleAnnouncer.current.textContent = `Run status updated. ${updates.join(' ')}`;
        }
      }
    }
    previousLifecycle.current = currentLifecycle;
  }, [contract]);

  const handleCancel = async (run: { id: string }) => {
    if (cancellationInFlight.current) return;
    cancellationInFlight.current = true;
    setCancellingRunId(run.id);
    setCancellationError(null);
    try {
      await cancelMutation.mutateAsync(run.id);
    } catch (error) {
      setCancellationError({ runId: run.id, message: describeCancellationError(error) });
    } finally {
      cancellationInFlight.current = false;
      setCancellingRunId(null);
    }
  };

  let body: ReactNode;

  if (query.isPending) {
    body = (
      <WorkbenchStateNotice
        tone="loading"
        kicker="Ledger status"
        heading="Loading the run ledger"
        message="Fetching run states, timings, evaluations, and costs."
      />
    );
  } else if (query.isError) {
    body = (
      <div className="space-y-4">
        <WorkbenchStateNotice
          announcementRole="alert"
          tone="unavailable"
          icon={FileClock}
          kicker="Ledger status"
          heading="Run ledger is unavailable"
          message={`${describeQueryError(query.error)} No run history has been substituted.`}
        />
        <Button
          type="button"
          variant="outline"
          disabled={query.isFetching}
          onClick={() => void query.refetch()}
          className="min-h-11 w-full sm:w-auto"
        >
          <RefreshCw
            aria-hidden="true"
            className={query.isFetching ? 'h-4 w-4 animate-spin' : 'h-4 w-4'}
          />
          {query.isFetching ? 'Retrying run ledger…' : 'Retry run ledger'}
        </Button>
      </div>
    );
  } else if (contract === undefined) {
    body = (
      <WorkbenchStateNotice
        tone="unavailable"
        icon={FileClock}
        kicker="Ledger status"
        heading="Run ledger is unavailable"
        message="No response was received for the run ledger."
      />
    );
  } else if (contract.presentation.state === 'malformed') {
    body = (
      <WorkbenchStateNotice
        tone="malformed"
        kicker="Ledger status"
        heading="The run ledger response could not be interpreted"
        message={contract.presentation.message}
        issues={contract.presentation.issues}
      />
    );
  } else if (contract.presentation.state === 'empty') {
    body = (
      <WorkbenchStateNotice
        tone="empty"
        icon={FileClock}
        kicker="Ledger status"
        heading="No run history has been loaded"
        message={
          contract.presentation.message ??
          'This route is ready for the run service. States, timings, evaluations, and costs will remain unreported until a response establishes their values and origins.'
        }
      />
    );
  } else {
    body = (
      <div className="space-y-4">
        {contract.presentation.state === 'partial' ? (
          <IssuesBanner
            heading="Some run fields could not be interpreted."
            issues={contract.presentation.issues}
          />
        ) : null}
        <RunLedgerTable
          runs={contract.items}
          cancellingRunId={cancellingRunId}
          cancellationPending={cancellingRunId !== null}
          cancellationError={cancellationError}
          onCancel={(run) => void handleCancel(run)}
        />
        <RunLedgerCards
          runs={contract.items}
          cancellingRunId={cancellingRunId}
          cancellationPending={cancellingRunId !== null}
          cancellationError={cancellationError}
          onCancel={(run) => void handleCancel(run)}
        />
      </div>
    );
  }

  return (
    <section aria-labelledby="run-ledger-heading" className="workbench-page">
      <div className="workbench-page-intro">
        <p className="workbench-kicker">Observe and reopen research</p>
        <h2 id="run-ledger-heading" className="font-editorial text-3xl tracking-[-0.025em] md:text-4xl">
          Run ledger
        </h2>
        <p className="workbench-lede">
          Scan execution state and reopen prior work without losing its workflow version, source
          provenance, evaluation, or operating context.
        </p>
      </div>

      <div className="workbench-rule" />

      {body}
      <p ref={lifecycleAnnouncer} className="sr-only" role="status" aria-atomic="true" />
    </section>
  );
}
