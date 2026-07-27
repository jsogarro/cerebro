import { useEffect, useMemo, useRef, useState, type FormEvent } from 'react';
import axios from 'axios';
import {
  AlertTriangle,
  CheckCircle2,
  CircleDashed,
  Clock3,
  Database,
  Loader2,
  LockKeyhole,
  XCircle,
} from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { useCreateRun } from '@/api/runs';
import type { Workflow } from '@/api/workbench';
import { describeQueryError } from '@/components/runs/format';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';

const FIXED_OBJECTIVE =
  'Assess whether spatiotemporal graph neural networks (ST-GNNs) are viable for predicting corporate revenue, using only the approved source corpus.';
const APPROVED_SOURCE_PACK = 'golden-run.corporate-revenue-stgnn-viability.v1';
const APPROVED_SOURCE_COUNT = 5;
const CANONICAL_WORKFLOW_ID = 'comparative-research.corporate-revenue-stgnn';
const CANONICAL_WORKFLOW_VERSION = '1.0.0';

interface PreflightCheck {
  id: string;
  label: string;
  detail: string;
  passed: boolean;
}

function describeSubmissionError(error: unknown): string {
  if (axios.isAxiosError(error)) {
    const data: unknown = error.response?.data;
    if (
      typeof data === 'object' &&
      data !== null &&
      'detail' in data &&
      typeof data.detail === 'string' &&
      data.detail.trim().length > 0
    ) {
      return data.detail;
    }
  }
  return describeQueryError(error);
}

function PreflightRow({ check }: { check: PreflightCheck }) {
  const Icon = check.passed ? CheckCircle2 : XCircle;
  return (
    <li className="flex gap-3 border-t py-3 first:border-t-0">
      <Icon
        aria-hidden="true"
        className={check.passed ? 'mt-0.5 h-5 w-5 flex-none text-[hsl(var(--status-success))]' : 'mt-0.5 h-5 w-5 flex-none text-destructive'}
      />
      <div>
        <p className="text-sm font-semibold">
          {check.passed ? 'Ready' : 'Blocked'} — {check.label}
        </p>
        <p className="mt-0.5 text-xs leading-5 text-muted-foreground">{check.detail}</p>
      </div>
    </li>
  );
}

export function ControlledRunSetup({
  workflow,
  onSubmissionStateChange,
}: {
  workflow: Workflow;
  onSubmissionStateChange: (isPending: boolean) => void;
}) {
  const navigate = useNavigate();
  const createRun = useCreateRun();
  const submissionGeneration = useRef(0);
  const [maxDurationSeconds, setMaxDurationSeconds] = useState('600');
  const [maxCost, setMaxCost] = useState('0');

  useEffect(
    () => () => {
      submissionGeneration.current += 1;
      onSubmissionStateChange(false);
    },
    [onSubmissionStateChange],
  );

  const duration = Number(maxDurationSeconds);
  const cost = Number(maxCost);
  const durationValid =
    maxDurationSeconds.trim().length > 0 && Number.isInteger(duration) && duration > 0;
  const costValid = maxCost.trim().length > 0 && Number.isFinite(cost) && cost >= 0;
  const budgetValid = durationValid && costValid;
  const supportsFixture = workflow.supportedModes.includes('fixture');
  const isCanonicalWorkflow =
    workflow.id === CANONICAL_WORKFLOW_ID &&
    workflow.version === CANONICAL_WORKFLOW_VERSION;

  const checks = useMemo<PreflightCheck[]>(
    () => [
      {
        id: 'workflow',
        label: 'Canonical workflow version',
        detail: isCanonicalWorkflow
          ? 'The selected immutable workflow version matches this controlled corpus.'
          : `This source pack requires ${CANONICAL_WORKFLOW_ID}@${CANONICAL_WORKFLOW_VERSION}.`,
        passed: isCanonicalWorkflow,
      },
      {
        id: 'objective',
        label: 'Fixed objective',
        detail: 'The bounded objective is locked to this reproducible execution context.',
        passed: FIXED_OBJECTIVE.length > 0,
      },
      {
        id: 'source-pack',
        label: 'Approved source pack',
        detail: isCanonicalWorkflow
          ? `${APPROVED_SOURCE_COUNT} controlled sources are identified by the pinned corpus manifest.`
          : 'The pinned corpus is unavailable for the selected workflow version.',
        passed: isCanonicalWorkflow,
      },
      {
        id: 'mode',
        label: 'Fixture execution',
        detail: supportsFixture
          ? 'This workflow version reports fixture support; no external provider credentials are required.'
          : 'This workflow version does not report fixture support.',
        passed: supportsFixture,
      },
      {
        id: 'budget',
        label: 'Hard budgets',
        detail: budgetValid
          ? `Time is capped at ${duration} seconds and cost at ${cost.toFixed(2)} USD.`
          : 'Enter a positive whole-number time limit and a non-negative cost limit.',
        passed: budgetValid,
      },
    ],
    [budgetValid, cost, duration, isCanonicalWorkflow, supportsFixture],
  );

  const preflightReady = checks.every((check) => check.passed);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!preflightReady || createRun.isPending) return;
    const submission = ++submissionGeneration.current;
    onSubmissionStateChange(true);

    try {
      const contract = await createRun.mutateAsync({
        workflowId: workflow.id,
        workflowVersion: workflow.version,
        objective: FIXED_OBJECTIVE,
        inputs: {
          source_pack_id: APPROVED_SOURCE_PACK,
          source_count: APPROVED_SOURCE_COUNT,
        },
        mode: 'fixture',
        budget: {
          maxDurationSeconds: duration,
          maxCost: cost,
          currency: 'USD',
        },
      });

      if (submission === submissionGeneration.current && contract.value !== null) {
        navigate(`/app/runs/${encodeURIComponent(contract.value.id)}`);
      }
    } catch {
      // The mutation error is rendered in this form's local status region.
    } finally {
      if (submission === submissionGeneration.current) {
        onSubmissionStateChange(false);
      }
    }
  }

  const malformedSubmission =
    createRun.isSuccess && createRun.data.value === null ? createRun.data.presentation : null;

  return (
    <section
      aria-labelledby="controlled-run-heading"
      className="overflow-hidden rounded-md border bg-card"
    >
      <div className="border-b border-accent/40 bg-accent/[0.06] px-4 py-4 sm:px-6">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
          <div>
            <p className="workbench-kicker text-accent">Controlled execution context</p>
            <h3 id="controlled-run-heading" className="font-editorial mt-1 text-2xl">
              Start a fixture-backed research run
            </h3>
            <p className="mt-2 max-w-3xl text-sm leading-6 text-muted-foreground">
              Fixture mode runs against a pinned, approved corpus for reproducibility. It is a
              controlled execution path—not a claim that live providers, fresh retrieval, or
              measured operational results are available.
            </p>
          </div>
          <span className="workbench-provenance workbench-provenance-fixture">
            <LockKeyhole aria-hidden="true" className="h-3.5 w-3.5" />
            Fixture context
          </span>
        </div>
      </div>

      <form onSubmit={handleSubmit} className="grid gap-0 lg:grid-cols-[minmax(0,1.25fr)_minmax(18rem,0.75fr)]">
        <div className="space-y-6 p-4 sm:p-6 lg:border-r">
          <div>
            <label htmlFor="controlled-objective" className="workbench-kicker">
              Fixed objective
            </label>
            <textarea
              id="controlled-objective"
              readOnly
              value={FIXED_OBJECTIVE}
              rows={4}
              className="mt-2 min-h-28 w-full resize-none rounded-md border bg-muted/25 px-3 py-3 text-sm leading-6 shadow-sm"
            />
            <p className="mt-1.5 text-xs leading-5 text-muted-foreground">
              Locked so fixture outputs can be evaluated against one bounded question.
            </p>
          </div>

          <div className="grid gap-4 sm:grid-cols-2">
            <div>
              <label htmlFor="source-pack" className="workbench-kicker">
                Approved source pack
              </label>
              <div className="relative mt-2">
                <Database aria-hidden="true" className="absolute left-3 top-2.5 h-4 w-4 text-muted-foreground" />
                <Input
                  id="source-pack"
                  readOnly
                  value={APPROVED_SOURCE_PACK}
                  className="pl-9 font-mono text-xs"
                />
              </div>
              <p className="mt-1.5 text-xs text-muted-foreground">
                Pinned manifest · {APPROVED_SOURCE_COUNT} approved sources
              </p>
            </div>

            <div>
              <label htmlFor="execution-mode" className="workbench-kicker">
                Execution mode
              </label>
              <div className="relative mt-2">
                <LockKeyhole aria-hidden="true" className="absolute left-3 top-2.5 h-4 w-4 text-muted-foreground" />
                <Input
                  id="execution-mode"
                  readOnly
                  value="Fixture — controlled and reproducible"
                  className="pl-9"
                />
              </div>
              <p className="mt-1.5 text-xs text-muted-foreground">
                Provider and model: not selected or implied by this mode.
              </p>
            </div>
          </div>

          <fieldset>
            <legend className="workbench-kicker">Hard run budgets</legend>
            <div className="mt-2 grid gap-4 sm:grid-cols-2">
              <div>
                <label htmlFor="time-budget" className="text-sm font-semibold">
                  Time limit
                </label>
                <div className="relative mt-1.5">
                  <Clock3 aria-hidden="true" className="absolute left-3 top-2.5 h-4 w-4 text-muted-foreground" />
                  <Input
                    id="time-budget"
                    type="number"
                    inputMode="numeric"
                    min="1"
                    step="1"
                    required
                    disabled={createRun.isPending}
                    value={maxDurationSeconds}
                    onChange={(event) => {
                      setMaxDurationSeconds(event.target.value);
                      createRun.reset();
                    }}
                    className="pl-9 pr-20"
                    aria-invalid={!durationValid}
                    aria-describedby={
                      durationValid ? 'time-budget-unit' : 'time-budget-unit time-budget-error'
                    }
                  />
                  <span id="time-budget-unit" className="pointer-events-none absolute right-3 top-2 text-sm text-muted-foreground">
                    seconds
                  </span>
                </div>
                {!durationValid ? (
                  <p id="time-budget-error" className="mt-1.5 text-xs leading-5 text-destructive">
                    Enter a positive whole number of seconds.
                  </p>
                ) : null}
              </div>
              <div>
                <label htmlFor="cost-budget" className="text-sm font-semibold">
                  Cost limit
                </label>
                <div className="relative mt-1.5">
                  <span aria-hidden="true" className="absolute left-3 top-2 text-sm text-muted-foreground">$</span>
                  <Input
                    id="cost-budget"
                    type="number"
                    inputMode="decimal"
                    min="0"
                    step="0.01"
                    required
                    disabled={createRun.isPending}
                    value={maxCost}
                    onChange={(event) => {
                      setMaxCost(event.target.value);
                      createRun.reset();
                    }}
                    className="pl-7 pr-14"
                    aria-invalid={!costValid}
                    aria-describedby={
                      costValid ? 'cost-budget-unit' : 'cost-budget-unit cost-budget-error'
                    }
                  />
                  <span id="cost-budget-unit" className="pointer-events-none absolute right-3 top-2 text-sm text-muted-foreground">
                    USD
                  </span>
                </div>
                {!costValid ? (
                  <p id="cost-budget-error" className="mt-1.5 text-xs leading-5 text-destructive">
                    Enter a non-negative cost limit.
                  </p>
                ) : null}
              </div>
            </div>
          </fieldset>
        </div>

        <aside aria-labelledby="preflight-heading" className="bg-muted/20 p-4 sm:p-6">
          <div className="flex items-start justify-between gap-3">
            <div>
              <p className="workbench-kicker">Preflight</p>
              <h4 id="preflight-heading" className="font-editorial mt-1 text-xl">
                {preflightReady ? 'Ready to submit' : 'Start is blocked'}
              </h4>
            </div>
            {preflightReady ? (
              <CheckCircle2 aria-label="Preflight ready" className="h-6 w-6 text-[hsl(var(--status-success))]" />
            ) : (
              <AlertTriangle aria-label="Preflight blocked" className="h-6 w-6 text-[hsl(var(--status-warning))]" />
            )}
          </div>

          <ul className="mt-3">
            {checks.map((check) => (
              <PreflightRow key={check.id} check={check} />
            ))}
          </ul>

          {createRun.isError ? (
            <div role="alert" className="mt-4 border-l-2 border-destructive bg-destructive/5 p-3">
              <p className="text-sm font-semibold">Run submission failed</p>
              <p className="mt-1 text-xs leading-5 text-muted-foreground">
                {describeSubmissionError(createRun.error)} Review the preflight and try again.
              </p>
            </div>
          ) : null}

          {malformedSubmission ? (
            <div role="alert" className="mt-4 border-l-2 border-destructive bg-destructive/5 p-3">
              <p className="text-sm font-semibold">Run was not created</p>
              <p className="mt-1 text-xs leading-5 text-muted-foreground">
                {malformedSubmission.message ?? 'The service did not return a valid run identifier.'}
              </p>
            </div>
          ) : null}

          <div className="mt-5">
            <Button
              type="submit"
              disabled={!preflightReady || createRun.isPending}
              className="min-h-11 w-full whitespace-normal"
            >
              {createRun.isPending ? (
                <>
                  <Loader2 aria-hidden="true" className="h-4 w-4 animate-spin" />
                  Submitting controlled run…
                </>
              ) : preflightReady ? (
                'Start controlled run'
              ) : (
                'Resolve blocked checks'
              )}
            </Button>
            <p className="mt-2 flex items-start gap-1.5 text-xs leading-5 text-muted-foreground">
              <CircleDashed aria-hidden="true" className="mt-0.5 h-3.5 w-3.5 flex-none" />
              Cost, latency, provider, model, and evaluation remain unavailable until the run
              service reports them.
            </p>
            <p className="sr-only" role="status" aria-atomic="true">
              {createRun.isPending ? 'Submitting controlled run.' : ''}
            </p>
          </div>
        </aside>
      </form>
    </section>
  );
}
