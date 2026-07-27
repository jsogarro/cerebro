import {
  ArrowRight,
  CircleDashed,
  FileOutput,
  FlaskConical,
  Wrench,
} from 'lucide-react';
import { useId } from 'react';
import type { Workflow } from '@/api/workbench';
import { OriginValueDisplay } from '@/components/runs/OriginValueDisplay';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { cn } from '@/lib/utils';

const maturityLabel: Record<Workflow['maturity'], string> = {
  experimental: 'Experimental',
  preview: 'Preview',
  stable: 'Stable',
  unknown: 'Maturity unavailable',
};

const modeLabel: Record<string, string> = {
  fixture: 'Fixture',
  live: 'Live',
  unknown: 'Mode unavailable',
};

function UnavailableValue({ children = 'Not available' }: { children?: string }) {
  return (
    <span className="inline-flex items-center gap-1.5 text-sm text-muted-foreground">
      <CircleDashed aria-hidden="true" className="h-3.5 w-3.5" />
      {children}
    </span>
  );
}

function ListOrUnavailable({
  values,
  unavailableLabel,
}: {
  values: readonly string[];
  unavailableLabel: string;
}) {
  if (values.length === 0) return <UnavailableValue>{unavailableLabel}</UnavailableValue>;

  return (
    <ul className="space-y-1 text-sm leading-6 text-foreground">
      {values.map((value) => (
        <li key={value} className="flex gap-2">
          <span aria-hidden="true" className="mt-[0.65rem] h-1 w-1 flex-none rounded-full bg-accent" />
          <span className="min-w-0 break-words">{value}</span>
        </li>
      ))}
    </ul>
  );
}

export function WorkflowCard({
  workflow,
  isSelected,
  disabled,
  onConfigure,
}: {
  workflow: Workflow;
  isSelected: boolean;
  disabled: boolean;
  onConfigure: (workflow: Workflow) => void;
}) {
  const headingId = useId();
  const inputRequirements = Object.keys(workflow.inputSchema);
  const requirements = [
    ...inputRequirements.map((requirement) => `Input: ${requirement}`),
    ...workflow.requiredTools.map((tool) => `Tool: ${tool}`),
  ];

  return (
    <Card
      role="article"
      aria-labelledby={headingId}
      className={isSelected ? 'border-primary shadow-[0_0_0_1px_hsl(var(--primary))]' : ''}
    >
      <CardHeader className="pb-4">
        <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
          <div className="flex min-w-0 items-start gap-3">
            <div className="flex h-9 w-9 flex-none items-center justify-center rounded-full border border-primary/25 bg-primary/5">
              <FlaskConical aria-hidden="true" className="h-4 w-4 text-primary" />
            </div>
            <div className="min-w-0">
              <CardTitle>
                <h3 id={headingId} className="font-editorial text-xl leading-tight">
                  {workflow.name}
                </h3>
              </CardTitle>
              <p className="mt-1 break-all font-mono text-xs text-muted-foreground">
                {workflow.id} · version {workflow.version}
              </p>
            </div>
          </div>
          <span
            className={cn('wb-maturity', workflow.maturity === 'unknown' && 'wb-maturity--muted')}
            title={`Maturity: ${maturityLabel[workflow.maturity]}`}
          >
            <span aria-hidden="true" className="wb-maturity-pip" />
            {maturityLabel[workflow.maturity]}
            {workflow.maturity === 'unknown' && workflow.rawMaturity
              ? ` (${workflow.rawMaturity})`
              : ''}
          </span>
        </div>
      </CardHeader>

      <CardContent className="space-y-5">
        <section aria-label="Workflow outcome">
          <p className="workbench-kicker">Outcome</p>
          <p className="mt-2 max-w-3xl text-sm leading-6 text-foreground">
            {workflow.description}
          </p>
        </section>

        <div className="workflow-attr-grid">
          <section>
            <p className="workbench-kicker">Supported modes</p>
            <div className="mt-2 flex flex-wrap gap-1.5">
              {workflow.supportedModes.length === 0 ? (
                <UnavailableValue>No modes reported</UnavailableValue>
              ) : (
                workflow.supportedModes.map((mode) => (
                  <Badge key={mode} variant="outline">
                    {modeLabel[mode] ?? mode}
                  </Badge>
                ))
              )}
            </div>
          </section>

          <section>
            <p className="workbench-kicker">Requirements</p>
            <div className="mt-2">
              <ListOrUnavailable values={requirements} unavailableLabel="Not reported" />
            </div>
          </section>

          <section>
            <p className="workbench-kicker">Produces</p>
            <div className="mt-2">
              <ListOrUnavailable
                values={workflow.artifactSchemas}
                unavailableLabel="Artifact schemas not reported"
              />
            </div>
          </section>

          <section>
            <p className="workbench-kicker">Latest evaluation</p>
            <div className="mt-2">
              <UnavailableValue>No catalog result reported</UnavailableValue>
            </div>
            {workflow.evaluators.length > 0 ? (
              <p className="mt-2 text-xs leading-5 text-muted-foreground">
                Configured evaluators: {workflow.evaluators.join(', ')}
              </p>
            ) : null}
          </section>
        </div>

        <div className="workflow-limits-grid">
          <section>
            <div className="flex items-center gap-2">
              <Wrench aria-hidden="true" className="h-4 w-4 text-muted-foreground" />
              <p className="workbench-kicker">Operating limits</p>
            </div>
            <div className="mt-2">
              <ListOrUnavailable
                values={workflow.limitations}
                unavailableLabel="No limitations were reported"
              />
            </div>
          </section>

          <section>
            <div className="flex items-center gap-2">
              <FileOutput aria-hidden="true" className="h-4 w-4 text-muted-foreground" />
              <p className="workbench-kicker">Typical operation</p>
            </div>
            <div className="mt-2 grid gap-2">
              <OriginValueDisplay value={workflow.typicalMetrics.durationMs} label="Latency" />
              <OriginValueDisplay value={workflow.typicalMetrics.cost} label="Cost" />
              <OriginValueDisplay value={workflow.typicalMetrics.provider} label="Provider" />
            </div>
          </section>
        </div>

        <Button
          type="button"
          disabled={disabled}
          variant="outline"
          aria-pressed={isSelected}
          className="w-full justify-between sm:w-auto"
          onClick={() => onConfigure(workflow)}
        >
          {isSelected ? 'Selected for preflight' : 'Configure controlled run'}
          <ArrowRight aria-hidden="true" className="h-4 w-4" />
        </Button>
      </CardContent>
    </Card>
  );
}
