import { useEffect, useRef, type ReactNode } from 'react';
import { ArrowLeft, FileClock, History, RotateCcw } from 'lucide-react';
import { useNavigate, useParams } from 'react-router-dom';
import {
  useRun,
  useRunArtifacts,
  useRunEvaluations,
  useRunEvents,
  useRunEvidence,
  useRunTasks,
} from '@/api/runs';
import {
  lifecycleSemantics,
  type Event,
  type PresentationState,
  type Task,
} from '@/api/workbench';
import { RunArtifactInspector } from '@/components/runs/RunArtifactInspector';
import { RunEvaluationPanel } from '@/components/runs/RunEvaluationPanel';
import { RunOperationalLedger } from '@/components/runs/RunOperationalLedger';
import { IssuesBanner } from '@/components/runs/IssuesBanner';
import { OriginValueDisplay } from '@/components/runs/OriginValueDisplay';
import { StatusBadge } from '@/components/runs/StatusBadge';
import { WorkbenchStateNotice } from '@/components/runs/WorkbenchStateNotice';
import {
  describeQueryError,
  formatDurationMs,
  formatTimestamp,
} from '@/components/runs/format';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';

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
  if (query.isError || query.data === undefined) return 'unavailable';
  return query.data.presentation.state;
}

function orderTasks(tasks: readonly Task[]): Task[] {
  return [...tasks].sort((a, b) => (a.startedAt ?? '9999').localeCompare(b.startedAt ?? '9999'));
}

function orderEvents(events: readonly Event[]): Event[] {
  return [...events].sort((a, b) => (a.occurredAt ?? '9999').localeCompare(b.occurredAt ?? '9999'));
}

function RetryNotice({
  tone,
  kicker,
  heading,
  message,
  issues,
  onRetry,
}: {
  tone: 'loading' | 'empty' | 'unavailable' | 'malformed';
  kicker: string;
  heading: string;
  message?: string | null;
  issues?: readonly string[];
  onRetry?: () => void;
}) {
  return (
    <div>
      <WorkbenchStateNotice
        headingLevel={4}
        tone={tone}
        kicker={kicker}
        heading={heading}
        message={message}
        issues={issues}
      />
      {onRetry ? (
        <Button variant="outline" size="sm" className="mt-3 gap-2" onClick={onRetry}>
          <RotateCcw aria-hidden="true" className="h-4 w-4" />
          Retry {kicker.toLowerCase()}
        </Button>
      ) : null}
    </div>
  );
}

function collectionState(
  label: string,
  state: PresentationState,
  query: QueryLike<HasPresentation>,
  retry: () => void,
): ReactNode | null {
  if (state === 'loading') {
    return <RetryNotice tone="loading" kicker={label} heading={`Loading ${label.toLowerCase()}`} />;
  }
  if (state === 'unavailable') {
    return (
      <RetryNotice
        tone="unavailable"
        kicker={label}
        heading={`${label} ${label === 'Evidence' ? 'is' : 'are'} unavailable`}
        message={query.isError ? describeQueryError(query.error) : `No ${label.toLowerCase()} response was received.`}
        onRetry={retry}
      />
    );
  }
  if (state === 'malformed') {
    return (
      <RetryNotice
        tone="malformed"
        kicker={label}
        heading={`The ${label.toLowerCase()} response could not be interpreted`}
        message={query.data?.presentation.message}
        issues={query.data?.presentation.issues}
        onRetry={retry}
      />
    );
  }
  if (state === 'empty') {
    return (
      <RetryNotice
        tone="empty"
        kicker={label}
        heading={`No ${label.toLowerCase()} ${label === 'Evidence' ? 'has' : 'have'} been recorded`}
        message={query.data?.presentation.message}
      />
    );
  }
  return null;
}

function TaskItem({ task }: { task: Task }) {
  return (
    <li className="run-log-record">
      <div className="run-log-marker" aria-hidden="true" />
      <div className="min-w-0">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <p className="workbench-kicker">{task.phase}</p>
            <h4 className="mt-1 font-editorial text-lg">{task.purpose}</h4>
          </div>
          <StatusBadge semantics={lifecycleSemantics[task.status]} rawValue={task.rawStatus} />
        </div>
        <dl className="mt-3 flex flex-wrap gap-x-6 gap-y-2 text-sm">
          <div><dt className="inline font-semibold">Attempt </dt><dd className="inline">{task.attempt ?? 'Not reported'}</dd></div>
          <div><dt className="inline font-semibold">Started </dt><dd className="inline">{formatTimestamp(task.startedAt)}</dd></div>
          <div><dt className="inline font-semibold">Duration </dt><dd className="inline"><OriginValueDisplay value={task.metrics.durationMs} format={formatDurationMs} /></dd></div>
        </dl>
        {task.outputSummary ? (
          <div className="mt-3"><p className="workbench-kicker">Output summary</p><p className="mt-1 text-sm leading-6">{task.outputSummary}</p></div>
        ) : null}
        {task.degradationReason ? (
          <div className="mt-3 workbench-status-warning"><p className="workbench-kicker">Degradation detail</p><p className="mt-1 text-sm leading-6">{task.degradationReason}</p></div>
        ) : null}
        {task.errorSummary ? (
          <div className="mt-3 workbench-status-failed"><p className="workbench-kicker">Failure detail</p><p className="mt-1 text-sm leading-6">{task.errorSummary}</p></div>
        ) : null}
        <details className="run-developer-detail">
          <summary>Task output and tool detail</summary>
          <dl>
            <div><dt>Task ID</dt><dd className="font-mono">{task.id}</dd></div>
            <div><dt>Capability</dt><dd>{task.capability ?? 'Not recorded'}</dd></div>
            <div><dt>Tool invocations</dt><dd>{task.toolInvocations.length || 'None recorded'}</dd></div>
            <div><dt>Dependencies</dt><dd>{task.dependencyIds.join(', ') || 'None recorded'}</dd></div>
          </dl>
        </details>
      </div>
    </li>
  );
}

function EventItem({ event }: { event: Event }) {
  return (
    <li className="run-event-record">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="workbench-kicker">{event.type} · event {event.version}</p>
          <p className="mt-1 text-sm leading-6">{event.summary}</p>
        </div>
        <time className="text-xs text-muted-foreground">{formatTimestamp(event.occurredAt)}</time>
      </div>
      {event.priorState !== null || event.newState !== null ? (
        <div className="mt-2 flex flex-wrap items-center gap-2 text-xs">
          {event.priorState ? <StatusBadge semantics={lifecycleSemantics[event.priorState]} rawValue={event.rawPriorState} /> : null}
          {event.priorState && event.newState ? <span aria-hidden="true">→</span> : null}
          {event.newState ? <StatusBadge semantics={lifecycleSemantics[event.newState]} rawValue={event.rawNewState} /> : null}
        </div>
      ) : null}
      <details className="run-developer-detail">
        <summary>Trace and event metadata</summary>
        <dl>
          <div><dt>Event ID</dt><dd className="font-mono">{event.id}</dd></div>
          <div><dt>Trace ID</dt><dd className="font-mono">{event.traceId ?? 'Not recorded'}</dd></div>
          <div><dt>Task ID</dt><dd className="font-mono">{event.taskId ?? 'Not recorded'}</dd></div>
        </dl>
      </details>
    </li>
  );
}

export function RunDetail() {
  const { id = '' } = useParams();
  const navigate = useNavigate();
  const runQuery = useRun(id);
  const runState = resolveState(runQuery);
  const run = runQuery.data?.value ?? null;
  const subresourceOptions = { runStatus: run?.status };
  const tasksQuery = useRunTasks(id, subresourceOptions);
  const eventsQuery = useRunEvents(id, subresourceOptions);
  const evidenceQuery = useRunEvidence(id, subresourceOptions);
  const artifactsQuery = useRunArtifacts(id, subresourceOptions);
  const evaluationsQuery = useRunEvaluations(id, subresourceOptions);

  const tasksState = resolveState(tasksQuery);
  const eventsState = resolveState(eventsQuery);
  const evidenceState = resolveState(evidenceQuery);
  const artifactsState = resolveState(artifactsQuery);
  const evaluationsState = resolveState(evaluationsQuery);
  const previousLifecycle = useRef<{ runId: string; label: string } | null>(null);
  const lifecycleAnnouncer = useRef<HTMLParagraphElement>(null);

  useEffect(() => {
    if (!run) return;
    const label = lifecycleSemantics[run.status].label;
    if (
      previousLifecycle.current?.runId === run.id &&
      previousLifecycle.current.label !== label
    ) {
      if (lifecycleAnnouncer.current) {
        lifecycleAnnouncer.current.textContent = `Run status updated to ${label}.`;
      }
    }
    previousLifecycle.current = { runId: run.id, label };
  }, [run]);

  let overview: ReactNode;
  if (runState === 'loading') {
    overview = <RetryNotice tone="loading" kicker="Run overview" heading="Loading the run" message="Fetching run state, objective, configuration, and operational provenance." />;
  } else if (runState === 'unavailable') {
    overview = (
      <RetryNotice
        tone="unavailable"
        kicker="Run overview"
        heading="This run is unavailable"
        message={runQuery.isError ? describeQueryError(runQuery.error) : 'No response was received for this run.'}
        onRetry={() => void runQuery.refetch()}
      />
    );
  } else if (runState === 'malformed') {
    overview = (
      <RetryNotice
        tone="malformed"
        kicker="Run overview"
        heading="The run response could not be interpreted"
        message={runQuery.data?.presentation.message}
        issues={runQuery.data?.presentation.issues}
        onRetry={() => void runQuery.refetch()}
      />
    );
  } else if (run === null) {
    overview = <RetryNotice tone="unavailable" kicker="Run overview" heading="This run is unavailable" message="No run record was returned." onRetry={() => void runQuery.refetch()} />;
  } else {
    overview = (
      <div className="space-y-5">
        {runState === 'partial' ? <IssuesBanner heading="Some run fields could not be interpreted." issues={runQuery.data?.presentation.issues ?? []} /> : null}
        <div className="flex flex-wrap items-center gap-3">
          <StatusBadge semantics={lifecycleSemantics[run.status]} rawValue={run.rawStatus} />
          <Badge variant="outline">
            {modeLabel[run.mode] ?? run.mode}
            {run.rawMode ? ` (reported as "${run.rawMode}")` : ''}
          </Badge>
          {run.mode === 'fixture' ? <span className="workbench-provenance workbench-provenance-fixture">Controlled fixture context</span> : null}
        </div>
        <dl className="run-overview-grid">
          <div><dt>Run ID</dt><dd className="font-mono">{run.id}</dd></div>
          <div><dt>Workflow / version</dt><dd>{run.workflowId} <span className="font-mono text-xs">v{run.workflowVersion}</span></dd></div>
          <div><dt>Mode</dt><dd>{modeLabel[run.mode] ?? run.mode}</dd></div>
          <div><dt>Created</dt><dd>{formatTimestamp(run.createdAt)}</dd></div>
          <div><dt>Started</dt><dd>{formatTimestamp(run.startedAt)}</dd></div>
          <div><dt>Updated</dt><dd>{formatTimestamp(run.updatedAt)}</dd></div>
          <div><dt>Completed</dt><dd>{formatTimestamp(run.completedAt)}</dd></div>
        </dl>
        {run.warnings.length > 0 ? <div><p className="workbench-kicker">Warnings</p><ul className="workbench-status-warning mt-2 list-disc space-y-1 pl-5 text-sm leading-6">{run.warnings.map((warning) => <li key={warning}>{warning}</li>)}</ul></div> : null}
        {run.failureSummary ? <div><p className="workbench-kicker">Failure summary</p><p className="workbench-status-failed mt-2 text-sm leading-6">{run.failureSummary}</p></div> : null}
      </div>
    );
  }

  const artifactStateNotice = collectionState('Artifacts', artifactsState, artifactsQuery, () => void artifactsQuery.refetch());
  const evidenceStateNotice = collectionState('Evidence', evidenceState, evidenceQuery, () => void evidenceQuery.refetch());
  const evaluationStateNotice = collectionState('Evaluations', evaluationsState, evaluationsQuery, () => void evaluationsQuery.refetch());
  const taskStateNotice = collectionState('Tasks', tasksState, tasksQuery, () => void tasksQuery.refetch());
  const eventStateNotice = collectionState('Events', eventsState, eventsQuery, () => void eventsQuery.refetch());
  const artifactIntegrityIssues =
    run === null
      ? []
      : (artifactsQuery.data?.items ?? [])
          .filter((artifact) => artifact.runId !== run.id || !run.artifactIds.includes(artifact.id))
          .map((artifact) => `Artifact ${artifact.id} is not owned and referenced by this run.`);
  const primaryArtifact =
    run === null
      ? null
      : (artifactsQuery.data?.items ?? []).find(
          (artifact) => artifact.runId === run.id && run.artifactIds.includes(artifact.id),
        ) ?? null;
  const referencedEvidenceIds = new Set([
    ...(primaryArtifact?.evidenceIds ?? []),
    ...(primaryArtifact?.claims.flatMap((claim) => claim.evidenceIds) ?? []),
  ]);
  const evidenceIntegrityIssues =
    run === null
      ? []
      : (evidenceQuery.data?.items ?? [])
          .filter(
            (item) =>
              item.runId !== run.id ||
              (primaryArtifact !== null && !referencedEvidenceIds.has(item.id)),
          )
          .map((item) => `Evidence ${item.id} is not owned by this run or referenced by the artifact.`);
  const inspectableEvidence = (evidenceQuery.data?.items ?? []).filter(
    (item) =>
      run !== null &&
      item.runId === run.id &&
      (primaryArtifact === null || referencedEvidenceIds.has(item.id)),
  );
  const taskIntegrityIssues =
    run === null
      ? []
      : (tasksQuery.data?.items ?? [])
          .filter((task) => task.runId !== run.id)
          .map((task) => `Task ${task.id} is not owned by this run.`);
  const inspectableTasks = (tasksQuery.data?.items ?? []).filter(
    (task) => run !== null && task.runId === run.id,
  );
  const eventIntegrityIssues =
    run === null
      ? []
      : (eventsQuery.data?.items ?? [])
          .filter((event) => event.runId !== run.id)
          .map((event) => `Event ${event.id} is not owned by this run.`);
  const inspectableEvents = (eventsQuery.data?.items ?? []).filter(
    (event) => run !== null && event.runId === run.id,
  );
  const evaluationIntegrityIssues =
    run === null
      ? []
      : (evaluationsQuery.data?.items ?? [])
          .filter(
            (evaluation) =>
              evaluation.runId !== run.id || !run.evaluationIds.includes(evaluation.id),
          )
          .map((evaluation) => `Evaluation ${evaluation.id} is not owned and referenced by this run.`);
  const inspectableEvaluations = (evaluationsQuery.data?.items ?? []).filter(
    (evaluation) =>
      run !== null &&
      evaluation.runId === run.id &&
      run.evaluationIds.includes(evaluation.id),
  );
  const inspectorEvidenceIdentity = inspectableEvidence
    .map(
      (evidence) =>
        `${evidence.id}:${evidence.availability}:${evidence.excerpt === null ? 'absent' : 'present'}`,
    )
    .join('|');

  return (
    <section aria-labelledby="run-detail-heading" className="workbench-page run-detail-page">
      <div className="workbench-page-intro">
        <Button aria-label="Back to run ledger" variant="ghost" size="sm" className="mb-3 -ml-2 gap-1.5" onClick={() => navigate('/app/runs')}>
          <ArrowLeft aria-hidden="true" className="h-4 w-4" />
          Back to runs
        </Button>
        <p className="workbench-kicker">Inspectable run dossier</p>
        <h2 id="run-detail-heading" className="font-editorial text-3xl tracking-[-0.025em] md:text-4xl">{run?.objective ?? 'Run detail'}</h2>
        <p className="workbench-lede">Read the artifact first, then independently inspect every material claim, exact evidence excerpt, evaluation, and operational origin.</p>
      </div>
      <div className="workbench-rule" />

      <div className="space-y-10">
        <section aria-labelledby="run-overview-heading">
          <h3 id="run-overview-heading" className="font-editorial text-xl">Run overview and configuration</h3>
          <div className="mt-3">{overview}</div>
        </section>

        <section aria-labelledby="run-artifact-heading">
          <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
            <div><p className="workbench-kicker">Reading surface</p><h3 id="run-artifact-heading" className="mt-1 font-editorial text-2xl">Research artifact</h3></div>
            {artifactsQuery.data && artifactsQuery.data.items.length > 1 ? <p className="text-sm text-muted-foreground">{artifactsQuery.data.items.length} artifact records · first referenced record shown</p> : null}
          </div>
          {artifactStateNotice}
          {artifactsState === 'partial' ? <IssuesBanner heading="Some artifact fields could not be interpreted." issues={artifactsQuery.data?.presentation.issues ?? []} className="mb-4" /> : null}
          <IssuesBanner heading="Some artifact records failed run-integrity checks." issues={artifactIntegrityIssues} className="mb-4" />
          {evidenceStateNotice ? <div className="mb-4">{evidenceStateNotice}</div> : null}
          {evidenceState === 'partial' ? <IssuesBanner heading="Some evidence fields could not be interpreted." issues={evidenceQuery.data?.presentation.issues ?? []} className="mb-4" /> : null}
          <IssuesBanner heading="Some evidence records failed run-integrity checks." issues={evidenceIntegrityIssues} className="mb-4" />
          {primaryArtifact || inspectableEvidence.length > 0 ? (
            <RunArtifactInspector
              key={`${id}:${primaryArtifact?.id ?? 'evidence-only'}:${inspectorEvidenceIdentity}`}
              artifact={primaryArtifact}
              evidence={inspectableEvidence}
              evidenceState={evidenceState}
              fixtureMode={run?.mode === 'fixture'}
            />
          ) : null}
        </section>

        <section aria-labelledby="run-log-heading">
          <p className="workbench-kicker">Execution history</p>
          <h3 id="run-log-heading" className="mt-1 font-editorial text-2xl">Ordered research log</h3>
          <div className="mt-4">
            {taskStateNotice}
            {tasksState === 'partial' ? <IssuesBanner heading="Some task fields could not be interpreted." issues={tasksQuery.data?.presentation.issues ?? []} className="mb-4" /> : null}
            <IssuesBanner heading="Some task records failed run-integrity checks." issues={taskIntegrityIssues} className="mb-4" />
            {tasksState === 'ready' || tasksState === 'partial' ? <ol className="run-task-log">{orderTasks(inspectableTasks).map((task) => <TaskItem key={task.id} task={task} />)}</ol> : null}
          </div>
          <div className="mt-8 border-t border-border pt-6">
            <div className="flex items-center gap-2"><History aria-hidden="true" className="h-4 w-4 text-muted-foreground" /><h4 className="font-editorial text-lg">Lifecycle events</h4></div>
            <div className="mt-3">
              {eventStateNotice}
              {eventsState === 'partial' ? <IssuesBanner heading="Some event fields could not be interpreted." issues={eventsQuery.data?.presentation.issues ?? []} className="mb-4" /> : null}
              <IssuesBanner heading="Some event records failed run-integrity checks." issues={eventIntegrityIssues} className="mb-4" />
              {eventsState === 'ready' || eventsState === 'partial' ? <ol>{orderEvents(inspectableEvents).map((event) => <EventItem key={event.id} event={event} />)}</ol> : null}
            </div>
          </div>
        </section>

        <section aria-labelledby="run-evaluation-heading">
          <p className="workbench-kicker">Independent assessment</p>
          <h3 id="run-evaluation-heading" className="mt-1 font-editorial text-2xl">Evaluation ledger</h3>
          <div className="mt-4">
            {evaluationStateNotice}
            {evaluationsState === 'partial' ? <IssuesBanner heading="Some evaluation fields could not be interpreted." issues={evaluationsQuery.data?.presentation.issues ?? []} className="mb-4" /> : null}
            <IssuesBanner heading="Some evaluation records failed run-integrity checks." issues={evaluationIntegrityIssues} className="mb-4" />
            {(evaluationsState === 'ready' || evaluationsState === 'partial') && evaluationsQuery.data ? <RunEvaluationPanel evaluations={inspectableEvaluations} /> : null}
          </div>
        </section>

        <section aria-labelledby="run-operations-heading">
          <p className="workbench-kicker">Operational provenance</p>
          <h3 id="run-operations-heading" className="mt-1 font-editorial text-2xl">Provider and cost ledger</h3>
          <p className="mt-2 max-w-2xl text-sm leading-6 text-muted-foreground">Every value is labeled measured, fixture-derived, or unavailable. Missing values are never interpreted as zero.</p>
          <div className="mt-4">{run ? <RunOperationalLedger run={run} /> : <RetryNotice tone="unavailable" kicker="Operational ledger" heading="Operational provenance is unavailable" message="A valid run overview is required before operational values can be inspected." />}</div>
        </section>
      </div>
      <p ref={lifecycleAnnouncer} className="sr-only" role="status" aria-atomic="true" />
      {runState === 'unavailable' ? <FileClock aria-hidden="true" className="sr-only" /> : null}
    </section>
  );
}
