import type { Run } from '@/api/workbench';
import { OriginValueDisplay } from './OriginValueDisplay';
import { formatCost, formatCount, formatDurationMs } from './format';

export function RunOperationalLedger({ run }: { run: Run }) {
  return (
    <div>
      <dl className="run-operational-ledger">
        <div><dt>Provider</dt><dd><OriginValueDisplay value={run.metrics.provider} /></dd></div>
        <div><dt>Model</dt><dd><OriginValueDisplay value={run.metrics.model} /></dd></div>
        <div><dt>Prompt ID</dt><dd><OriginValueDisplay value={run.metrics.promptId} /></dd></div>
        <div><dt>Prompt version</dt><dd><OriginValueDisplay value={run.metrics.promptVersion} /></dd></div>
        <div><dt>Input tokens</dt><dd><OriginValueDisplay value={run.metrics.inputTokens} format={formatCount} /></dd></div>
        <div><dt>Output tokens</dt><dd><OriginValueDisplay value={run.metrics.outputTokens} format={formatCount} /></dd></div>
        <div><dt>Total tokens</dt><dd><OriginValueDisplay value={run.metrics.totalTokens} format={formatCount} /></dd></div>
        <div><dt>Cost</dt><dd><OriginValueDisplay value={run.metrics.cost} format={formatCost} /></dd></div>
        <div><dt>Latency</dt><dd><OriginValueDisplay value={run.metrics.latencyMs} format={formatDurationMs} /></dd></div>
        <div><dt>Duration</dt><dd><OriginValueDisplay value={run.metrics.durationMs} format={formatDurationMs} /></dd></div>
      </dl>
      <details className="run-developer-detail">
        <summary>Prompts, policy, and developer metadata</summary>
        <p className="mt-3 text-sm leading-6 text-muted-foreground">
          Raw prompts are intentionally collapsed. They are shown only when the run transport supplies them.
        </p>
        <pre>{run.providerPolicySnapshot ? JSON.stringify(run.providerPolicySnapshot, null, 2) : 'Provider policy snapshot not recorded.'}</pre>
        <p className="mt-3 font-mono text-xs">Run ID: {run.id}</p>
      </details>
    </div>
  );
}
