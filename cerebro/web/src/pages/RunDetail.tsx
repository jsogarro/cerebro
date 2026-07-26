import type { ReactNode } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { ArrowLeft, FileClock, History } from 'lucide-react';
import { useRun, useRunEvents, useRunTasks } from '@/api/runs';
import { lifecycleSemantics, type Event, type PresentationState, type Task } from '@/api/workbench';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { IssuesBanner } from '@/components/runs/IssuesBanner';
import { OriginValueDisplay } from '@/components/runs/OriginValueDisplay';
import { StatusBadge } from '@/components/runs/StatusBadge';
import { WorkbenchStateNotice } from '@/components/runs/WorkbenchStateNotice';
import {
  describeQueryError,
  formatCost,
  formatCount,
  formatDurationMs,
  formatTimestamp,
} from '@/components/runs/format';

const modeLabel: Record<string, string> = {
  fixture: 'Fixture',
  live: 'Live',
  unknown: 'Unknown mode',
};

interface HasPresentation {
  presentation: { state: PresentationState; message: string | null; issues: readonly string[] };
}

interface QueryLike<T extends HasPresentation> {
  isPending: boolean;
  isError: boolean;
  error: unknown;
  data: T | undefined;
}

function resolveState<T extends HasPresentation>(query: QueryLike<T>): PresentationState {
  if (query.isPending) return 'loading';
  if (query.isError) return 'unavailable';
  if (query.data === undefined) return 'unavailable';
  return query.data.presentation.state;
}

function orderTasksByStart(tasks: readonly Task[]): Task[] {
  return [...tasks].sort((a, b) => {
    if (a.startedAt === null && b.startedAt === null) return 0;
    if (a.startedAt === null) return 1;
    if (b.startedAt === null) return -1;
    return a.startedAt.localeCompare(b.startedAt);
  });
}

function orderEventsByRecency(events: readonly Event[]): Event[] {
  return [...events].sort((a, b) => {
    if (a.occurredAt === null && b.occurredAt === null) return 0;
    if (a.occurredAt === null) return 1;
    if (b.occurredAt === null) return -1;
    return b.occurredAt.localeCompare(a.occurredAt);
  });
}

function TaskItem({ task }: { task: Task }) {
  return (
    <li className="border-b border-border/70 py-4 last:border-b-0">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <p className="font-medium">{task.purpose}</p>
          <p className="workbench-kicker mt-0.5">{task.phase}</p>
        </div>
        <StatusBadge semantics={lifecycleSemantics[task.status]} rawValue={task.rawStatus} />
      </div>
      <dl className="mt-2 flex flex-wrap gap-x-6 gap-y-1 text-sm text-muted-foreground">
        <div className="flex items-center gap-1.5">
          <dt className="font-semibold">Attempt</dt>
          <dd>{task.attempt === null ? 'Not reported' : task.attempt}</dd>
        </div>
        <div className="flex items-center gap-1.5">
          <dt className="font-semibold">Duration</dt>
          <dd>
            <OriginValueDisplay value={task.metrics.durationMs} format={formatDurationMs} />
          </dd>
        </div>
      </dl>
      {task.errorSummary ? (
        <p className="workbench-status-failed mt-2 text-sm">{task.errorSummary}</p>
      ) : task.outputSummary ? (
        <p className="mt-2 text-sm text-muted-foreground">{task.outputSummary}</p>
      ) : null}
    </li>
  );
}

function EventItem({ event }: { event: Event }) {
  return (
    <li className="border-b border-border/70 py-3 last:border-b-0">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm">{event.summary}</p>
        <span className="text-xs text-muted-foreground">{formatTimestamp(event.occurredAt)}</span>
      </div>
      {event.priorState !== null || event.newState !== null ? (
        <div className="mt-1.5 flex flex-wrap items-center gap-2 text-xs">
          {event.priorState !== null ? (
            <StatusBadge semantics={lifecycleSemantics[event.priorState]} rawValue={event.rawPriorState} />
          ) : null}
          {event.priorState !== null && event.newState !== null ? <span aria-hidden="true">→</span> : null}
          {event.newState !== null ? (
            <StatusBadge semantics={lifecycleSemantics[event.newState]} rawValue={event.rawNewState} />
          ) : null}
        </div>
      ) : null}
    </li>
  );
}

export function RunDetail() {
  const { id = '' } = useParams();
  const navigate = useNavigate();

  const runQuery = useRun(id);
  const runState = resolveState(runQuery);
  const run = runQuery.data?.value ?? null;

  const tasksQuery = useRunTasks(id, { runStatus: run?.status });
  const tasksState = resolveState(tasksQuery);

  const eventsQuery = useRunEvents(id, { runStatus: run?.status });
  const eventsState = resolveState(eventsQuery);

  let overview: ReactNode;
  if (runState === 'loading') {
    overview = (
      <WorkbenchStateNotice
        tone="loading"
        kicker="Run status"
        heading="Loading the run"
        message="Fetching run state, objective, and operational metrics."
      />
    );
  } else if (runState === 'unavailable') {
    overview = (
      <WorkbenchStateNotice
        tone="unavailable"
        icon={FileClock}
        kicker="Run status"
        heading="This run is unavailable"
        message={runQuery.isError ? describeQueryError(runQuery.error) : 'No response was received for this run.'}
      />
    );
  } else if (runState === 'malformed') {
    overview = (
      <WorkbenchStateNotice
        tone="malformed"
        kicker="Run status"
        heading="The run response could not be interpreted"
        message={runQuery.data?.presentation.message}
        issues={runQuery.data?.presentation.issues}
      />
    );
  } else if (run === null) {
    overview = (
      <WorkbenchStateNotice
        tone="unavailable"
        icon={FileClock}
        kicker="Run status"
        heading="This run is unavailable"
        message="No run record was returned."
      />
    );
  } else {
    overview = (
      <div className="space-y-4">
        {runState === 'partial' ? (
          <IssuesBanner
            heading="Some run fields could not be interpreted."
            issues={runQuery.data?.presentation.issues ?? []}
          />
        ) : null}
        <div className="flex flex-wrap items-center gap-3">
          <StatusBadge semantics={lifecycleSemantics[run.status]} rawValue={run.rawStatus} />
          <Badge variant="outline">{modeLabel[run.mode] ?? run.mode}</Badge>
        </div>
        <dl className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          <div>
            <dt className="workbench-kicker">Workflow</dt>
            <dd className="mt-1 text-sm">
              {run.workflowId} <span className="font-mono text-xs text-muted-foreground">v{run.workflowVersion}</span>
            </dd>
          </div>
          <div>
            <dt className="workbench-kicker">Created</dt>
            <dd className="mt-1 text-sm">{formatTimestamp(run.createdAt)}</dd>
          </div>
          <div>
            <dt className="workbench-kicker">Started</dt>
            <dd className="mt-1 text-sm">{formatTimestamp(run.startedAt)}</dd>
          </div>
          <div>
            <dt className="workbench-kicker">Completed</dt>
            <dd className="mt-1 text-sm">{formatTimestamp(run.completedAt)}</dd>
          </div>
          <div>
            <dt className="workbench-kicker">Duration</dt>
            <dd className="mt-1 text-sm">
              <OriginValueDisplay value={run.metrics.durationMs} format={formatDurationMs} />
            </dd>
          </div>
          <div>
            <dt className="workbench-kicker">Cost</dt>
            <dd className="mt-1 text-sm">
              <OriginValueDisplay value={run.metrics.cost} format={formatCost} />
            </dd>
          </div>
          <div>
            <dt className="workbench-kicker">Tokens</dt>
            <dd className="mt-1 text-sm">
              <OriginValueDisplay value={run.metrics.totalTokens} format={formatCount} />
            </dd>
          </div>
          <div>
            <dt className="workbench-kicker">Provider</dt>
            <dd className="mt-1 text-sm">
              <OriginValueDisplay value={run.metrics.provider} />
            </dd>
          </div>
          <div>
            <dt className="workbench-kicker">Model</dt>
            <dd className="mt-1 text-sm">
              <OriginValueDisplay value={run.metrics.model} />
            </dd>
          </div>
        </dl>
        {run.warnings.length > 0 ? (
          <div>
            <p className="workbench-kicker">Warnings</p>
            <ul className="workbench-status-warning mt-1.5 list-disc space-y-1 pl-5 text-sm leading-6">
              {run.warnings.map((warning) => (
                <li key={warning}>{warning}</li>
              ))}
            </ul>
          </div>
        ) : null}
        {run.failureSummary !== null ? (
          <div>
            <p className="workbench-kicker">Failure summary</p>
            <p className="workbench-status-failed mt-1.5 text-sm leading-6">{run.failureSummary}</p>
          </div>
        ) : null}
      </div>
    );
  }

  let taskTimeline: ReactNode;
  if (tasksState === 'loading') {
    taskTimeline = (
      <WorkbenchStateNotice tone="loading" kicker="Task timeline" heading="Loading tasks" />
    );
  } else if (tasksState === 'unavailable') {
    taskTimeline = (
      <WorkbenchStateNotice
        tone="unavailable"
        kicker="Task timeline"
        heading="Tasks are unavailable"
        message={tasksQuery.isError ? describeQueryError(tasksQuery.error) : 'No response was received for this run’s tasks.'}
      />
    );
  } else if (tasksState === 'malformed') {
    taskTimeline = (
      <WorkbenchStateNotice
        tone="malformed"
        kicker="Task timeline"
        heading="The task response could not be interpreted"
        message={tasksQuery.data?.presentation.message}
        issues={tasksQuery.data?.presentation.issues}
      />
    );
  } else if (tasksState === 'empty') {
    taskTimeline = (
      <WorkbenchStateNotice
        tone="empty"
        kicker="Task timeline"
        heading="No tasks have been recorded"
        message={tasksQuery.data?.presentation.message}
      />
    );
  } else {
    const tasks = orderTasksByStart(tasksQuery.data?.items ?? []);
    taskTimeline = (
      <div className="space-y-4">
        {tasksState === 'partial' ? (
          <IssuesBanner
            heading="Some task fields could not be interpreted."
            issues={tasksQuery.data?.presentation.issues ?? []}
          />
        ) : null}
        <ol>
          {tasks.map((task) => (
            <TaskItem key={task.id} task={task} />
          ))}
        </ol>
      </div>
    );
  }

  let recentActivity: ReactNode;
  if (eventsState === 'loading') {
    recentActivity = (
      <WorkbenchStateNotice tone="loading" kicker="Recent activity" heading="Loading events" />
    );
  } else if (eventsState === 'unavailable') {
    recentActivity = (
      <WorkbenchStateNotice
        tone="unavailable"
        icon={History}
        kicker="Recent activity"
        heading="Recent activity is unavailable"
        message={eventsQuery.isError ? describeQueryError(eventsQuery.error) : 'No response was received for this run’s events.'}
      />
    );
  } else if (eventsState === 'malformed') {
    recentActivity = (
      <WorkbenchStateNotice
        tone="malformed"
        kicker="Recent activity"
        heading="The event response could not be interpreted"
        message={eventsQuery.data?.presentation.message}
        issues={eventsQuery.data?.presentation.issues}
      />
    );
  } else if (eventsState === 'empty') {
    recentActivity = (
      <WorkbenchStateNotice
        tone="empty"
        icon={History}
        kicker="Recent activity"
        heading="No events have been recorded"
        message={eventsQuery.data?.presentation.message}
      />
    );
  } else {
    const events = orderEventsByRecency(eventsQuery.data?.items ?? []);
    recentActivity = (
      <div className="space-y-4">
        {eventsState === 'partial' ? (
          <IssuesBanner
            heading="Some event fields could not be interpreted."
            issues={eventsQuery.data?.presentation.issues ?? []}
          />
        ) : null}
        <ul>
          {events.map((event) => (
            <EventItem key={event.id} event={event} />
          ))}
        </ul>
      </div>
    );
  }

  return (
    <section aria-labelledby="run-detail-heading" className="workbench-page">
      <div className="workbench-page-intro">
        <Button
          aria-label="Back to run ledger"
          variant="ghost"
          size="sm"
          className="mb-3 -ml-2 gap-1.5"
          onClick={() => navigate('/app/runs')}
        >
          <ArrowLeft aria-hidden="true" className="h-4 w-4" />
          Back to runs
        </Button>
        <p className="workbench-kicker">Run detail</p>
        <h2 id="run-detail-heading" className="font-editorial text-3xl tracking-[-0.025em] md:text-4xl">
          {run?.objective ?? 'Run detail'}
        </h2>
        <p className="workbench-lede font-mono text-sm">{id}</p>
      </div>

      <div className="workbench-rule" />

      <div className="space-y-8">
        <section aria-labelledby="run-overview-heading">
          <h3 id="run-overview-heading" className="font-editorial text-xl">
            Overview
          </h3>
          <div className="mt-3">{overview}</div>
        </section>

        <section aria-labelledby="run-tasks-heading">
          <h3 id="run-tasks-heading" className="font-editorial text-xl">
            Task timeline
          </h3>
          <div className="mt-3">{taskTimeline}</div>
        </section>

        <section aria-labelledby="run-events-heading">
          <h3 id="run-events-heading" className="font-editorial text-xl">
            Recent activity
          </h3>
          <div className="mt-3">{recentActivity}</div>
        </section>
      </div>
    </section>
  );
}
