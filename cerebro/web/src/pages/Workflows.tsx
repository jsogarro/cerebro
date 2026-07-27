import { useState, type ReactNode } from 'react';
import { RefreshCw, ScrollText } from 'lucide-react';
import { useWorkflows } from '@/api/workflows';
import type { Workflow } from '@/api/workbench';
import { IssuesBanner } from '@/components/runs/IssuesBanner';
import { WorkbenchStateNotice } from '@/components/runs/WorkbenchStateNotice';
import { describeQueryError } from '@/components/runs/format';
import { ControlledRunSetup } from '@/components/workflows/ControlledRunSetup';
import { WorkflowCard } from '@/components/workflows/WorkflowCard';
import { Button } from '@/components/ui/button';

export function Workflows() {
  const query = useWorkflows();
  const contract = query.data;
  const [selectedWorkflow, setSelectedWorkflow] = useState<
    Pick<Workflow, 'id' | 'version'> | null
  >(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  const resolvedWorkflow =
    contract?.items.find(
      (workflow) =>
        workflow.id === selectedWorkflow?.id &&
        workflow.version === selectedWorkflow.version,
    ) ??
    contract?.items.find((workflow) => workflow.supportedModes.includes('fixture')) ??
    contract?.items[0] ??
    null;

  let body: ReactNode;

  if (query.isPending) {
    body = (
      <WorkbenchStateNotice
        tone="loading"
        kicker="Catalog status"
        heading="Loading the workflow catalog"
        message="Fetching runnable protocols, their outcomes, and their operating limits."
      />
    );
  } else if (query.isError) {
    body = (
      <div className="space-y-4">
        <WorkbenchStateNotice
          announcementRole="alert"
          tone="unavailable"
          icon={ScrollText}
          kicker="Catalog status"
          heading="Workflow service is unavailable"
          message={`${describeQueryError(query.error)} No workflow or run data has been substituted.`}
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
          {query.isFetching ? 'Retrying workflow service…' : 'Retry workflow service'}
        </Button>
      </div>
    );
  } else if (contract === undefined) {
    body = (
      <WorkbenchStateNotice
        tone="unavailable"
        icon={ScrollText}
        kicker="Catalog status"
        heading="Workflow catalog is unavailable"
        message="No response was received for the workflow catalog. No workflow data has been substituted."
      />
    );
  } else if (contract.presentation.state === 'malformed') {
    body = (
      <WorkbenchStateNotice
        tone="malformed"
        kicker="Catalog status"
        heading="The workflow catalog response could not be interpreted"
        message={contract.presentation.message}
        issues={contract.presentation.issues}
      />
    );
  } else if (contract.presentation.state === 'empty') {
    body = (
      <WorkbenchStateNotice
        tone="empty"
        icon={ScrollText}
        kicker="Catalog status"
        heading="No runnable workflows are available"
        message={
          contract.presentation.message ??
          'Workflow outcomes, maturity, modes, and execution requirements remain unavailable until the service supplies them.'
        }
      />
    );
  } else {
    body = (
      <div className="space-y-8">
        {contract.presentation.state === 'partial' ? (
          <IssuesBanner
            heading="Some workflow fields could not be interpreted."
            issues={contract.presentation.issues}
          />
        ) : null}

        <div className="space-y-4">
          {contract.items.map((workflow) => (
            <WorkflowCard
              key={`${workflow.id}@${workflow.version}`}
              workflow={workflow}
              disabled={isSubmitting}
              isSelected={
                workflow.id === resolvedWorkflow?.id &&
                workflow.version === resolvedWorkflow.version
              }
              onConfigure={(selected) =>
                setSelectedWorkflow({ id: selected.id, version: selected.version })
              }
            />
          ))}
        </div>

        {resolvedWorkflow ? (
          <ControlledRunSetup
            key={`${resolvedWorkflow.id}@${resolvedWorkflow.version}`}
            workflow={resolvedWorkflow}
            onSubmissionStateChange={setIsSubmitting}
          />
        ) : null}
      </div>
    );
  }

  return (
    <section
      aria-labelledby="workflow-catalog-heading"
      className="workbench-page workflow-dossier"
    >
      <div className="workbench-page-intro">
        <p className="workbench-kicker">Choose a research protocol</p>
        <h2
          id="workflow-catalog-heading"
          className="font-editorial text-3xl tracking-[-0.025em] md:text-4xl"
        >
          Workflow catalog
        </h2>
        <p className="workbench-lede">
          Review what a versioned protocol produces, what it requires, and what remains
          unavailable before starting a controlled research run.
        </p>
      </div>

      <div className="workbench-rule" />

      {body}
    </section>
  );
}
