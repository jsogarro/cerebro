import type { ReactNode } from 'react';
import { FlaskConical, ScrollText } from 'lucide-react';
import { useWorkflows } from '@/api/workflows';
import type { Workflow } from '@/api/workbench';
import { Badge } from '@/components/ui/badge';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { IssuesBanner } from '@/components/runs/IssuesBanner';
import { WorkbenchStateNotice } from '@/components/runs/WorkbenchStateNotice';
import { describeQueryError } from '@/components/runs/format';

const maturityLabel: Record<Workflow['maturity'], string> = {
  experimental: 'Experimental',
  preview: 'Preview',
  stable: 'Stable',
  unknown: 'Unknown maturity',
};

const modeLabel: Record<string, string> = {
  fixture: 'Fixture',
  live: 'Live',
  unknown: 'Unknown mode',
};

function WorkflowCard({ workflow }: { workflow: Workflow }) {
  return (
    <Card aria-label={`Workflow ${workflow.name}`} className="flex flex-col">
      <CardHeader className="pb-3">
        <div className="flex items-start justify-between gap-3">
          <div className="flex items-center gap-3">
            <div className="flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-md bg-primary/10">
              <FlaskConical aria-hidden="true" className="h-4 w-4 text-primary" />
            </div>
            <div>
              <CardTitle className="text-base">{workflow.name}</CardTitle>
              <div className="mt-0.5 font-mono text-xs text-muted-foreground">
                {workflow.id} · v{workflow.version}
              </div>
            </div>
          </div>
          <Badge variant={workflow.maturity === 'unknown' ? 'outline' : 'secondary'}>
            {maturityLabel[workflow.maturity]}
            {workflow.maturity === 'unknown' && workflow.rawMaturity
              ? ` (${workflow.rawMaturity})`
              : ''}
          </Badge>
        </div>
      </CardHeader>
      <CardContent className="flex flex-1 flex-col gap-4">
        <CardDescription className="text-sm leading-6 text-foreground/80">
          {workflow.description}
        </CardDescription>

        <div>
          <p className="workbench-kicker">Supported modes</p>
          <div className="mt-1.5 flex flex-wrap gap-1.5">
            {workflow.supportedModes.length === 0 ? (
              <span className="text-sm text-muted-foreground">No modes reported.</span>
            ) : (
              workflow.supportedModes.map((mode) => (
                <Badge key={mode} variant="outline">
                  {modeLabel[mode] ?? mode}
                </Badge>
              ))
            )}
          </div>
        </div>

        <div className="mt-auto">
          <p className="workbench-kicker">Limitations</p>
          {workflow.limitations.length === 0 ? (
            <p className="mt-1.5 text-sm text-muted-foreground">
              No documented limitations were reported.
            </p>
          ) : (
            <ul className="mt-1.5 list-disc space-y-1 pl-5 text-sm leading-6 text-muted-foreground">
              {workflow.limitations.map((limitation) => (
                <li key={limitation}>{limitation}</li>
              ))}
            </ul>
          )}
        </div>
      </CardContent>
    </Card>
  );
}

export function Workflows() {
  const query = useWorkflows();
  const contract = query.data;

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
      <WorkbenchStateNotice
        tone="unavailable"
        icon={ScrollText}
        kicker="Catalog status"
        heading="Workflow catalog is unavailable"
        message={describeQueryError(query.error)}
      />
    );
  } else if (contract === undefined) {
    body = (
      <WorkbenchStateNotice
        tone="unavailable"
        icon={ScrollText}
        kicker="Catalog status"
        heading="Workflow catalog is unavailable"
        message="No response was received for the workflow catalog."
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
        heading="No workflow catalog has been loaded"
        message={
          contract.presentation.message ??
          'This route is ready for the catalog connection. Workflow availability, maturity, and execution requirements will remain unreported until the service supplies them.'
        }
      />
    );
  } else {
    body = (
      <div className="space-y-4">
        {contract.presentation.state === 'partial' ? (
          <IssuesBanner
            heading="Some workflow fields could not be interpreted."
            issues={contract.presentation.issues}
          />
        ) : null}
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {contract.items.map((workflow) => (
            <WorkflowCard key={`${workflow.id}@${workflow.version}`} workflow={workflow} />
          ))}
        </div>
      </div>
    );
  }

  return (
    <section aria-labelledby="workflow-catalog-heading" className="workbench-page">
      <div className="workbench-page-intro">
        <p className="workbench-kicker">Choose a research protocol</p>
        <h2 id="workflow-catalog-heading" className="font-editorial text-3xl tracking-[-0.025em] md:text-4xl">
          Workflow catalog
        </h2>
        <p className="workbench-lede">
          Review a protocol's outcome, version, operating limits, and source requirements before
          beginning a controlled research run.
        </p>
      </div>

      <div className="workbench-rule" />

      {body}
    </section>
  );
}
