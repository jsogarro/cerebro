import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { FileClock } from 'lucide-react';
import { useRuns } from '@/api/runs';
import { lifecycleSemantics, type Run } from '@/api/workbench';
import { Badge } from '@/components/ui/badge';
import { IssuesBanner } from '@/components/runs/IssuesBanner';
import { OriginValueDisplay } from '@/components/runs/OriginValueDisplay';
import { StatusBadge } from '@/components/runs/StatusBadge';
import { WorkbenchStateNotice } from '@/components/runs/WorkbenchStateNotice';
import { describeQueryError, formatCost, formatDurationMs, formatTimestamp } from '@/components/runs/format';

const modeLabel: Record<string, string> = {
  fixture: 'Fixture',
  live: 'Live',
  unknown: 'Unknown mode',
};

function evaluationSummary(run: Run): string {
  if (run.evaluationIds.length === 0) return 'None recorded';
  return `${run.evaluationIds.length} recorded`;
}

function RunRow({ run }: { run: Run }) {
  return (
    <tr className="border-b border-border/70 last:border-b-0">
      <td className="py-3 pr-4 align-top">
        <StatusBadge semantics={lifecycleSemantics[run.status]} rawValue={run.rawStatus} />
      </td>
      <td className="py-3 pr-4 align-top">
        <Link
          to={`/app/runs/${encodeURIComponent(run.id)}`}
          className="font-medium text-foreground underline-offset-4 hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-ring"
        >
          {run.objective}
        </Link>
        <div className="mt-0.5 font-mono text-xs text-muted-foreground">{run.id}</div>
      </td>
      <td className="py-3 pr-4 align-top text-sm">
        <div>{run.workflowId}</div>
        <div className="font-mono text-xs text-muted-foreground">v{run.workflowVersion}</div>
      </td>
      <td className="py-3 pr-4 align-top">
        <Badge variant="outline">{modeLabel[run.mode] ?? run.mode}</Badge>
      </td>
      <td className="py-3 pr-4 align-top text-sm">
        <div>{formatTimestamp(run.startedAt)}</div>
        <div className="mt-1">
          <OriginValueDisplay value={run.metrics.durationMs} format={formatDurationMs} />
        </div>
      </td>
      <td className="py-3 pr-4 align-top text-sm text-muted-foreground">{evaluationSummary(run)}</td>
      <td className="py-3 align-top text-sm">
        <OriginValueDisplay value={run.metrics.cost} format={formatCost} />
      </td>
    </tr>
  );
}

export function Runs() {
  const query = useRuns();
  const contract = query.data;

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
      <WorkbenchStateNotice
        tone="unavailable"
        icon={FileClock}
        kicker="Ledger status"
        heading="Run ledger is unavailable"
        message={describeQueryError(query.error)}
      />
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
        <div className="overflow-x-auto">
          <table className="w-full min-w-[52rem] border-collapse text-left">
            <caption className="sr-only">Runs, most recently reported first</caption>
            <thead>
              <tr className="workbench-kicker border-b border-border">
                <th scope="col" className="py-2 pr-4 font-semibold">
                  State
                </th>
                <th scope="col" className="py-2 pr-4 font-semibold">
                  Objective
                </th>
                <th scope="col" className="py-2 pr-4 font-semibold">
                  Workflow
                </th>
                <th scope="col" className="py-2 pr-4 font-semibold">
                  Mode
                </th>
                <th scope="col" className="py-2 pr-4 font-semibold">
                  Start / duration
                </th>
                <th scope="col" className="py-2 pr-4 font-semibold">
                  Evaluations
                </th>
                <th scope="col" className="py-2 font-semibold">
                  Cost
                </th>
              </tr>
            </thead>
            <tbody>
              {contract.items.map((run) => (
                <RunRow key={run.id} run={run} />
              ))}
            </tbody>
          </table>
        </div>
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
    </section>
  );
}
