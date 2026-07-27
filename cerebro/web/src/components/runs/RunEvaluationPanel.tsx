import { OriginValueDisplay } from './OriginValueDisplay';
import type { Evaluation } from '@/api/workbench';
import { formatTimestamp } from './format';

const statusClass: Record<Evaluation['status'], string> = {
  passed: 'workbench-status-success',
  warning: 'workbench-status-warning',
  failed: 'workbench-status-failed',
  'not-run': 'workbench-status-muted',
  unknown: 'workbench-status-warning',
};

export function RunEvaluationPanel({ evaluations }: { evaluations: readonly Evaluation[] }) {
  return (
    <div className="space-y-5">
      {evaluations.map((evaluation) => (
        <article
          key={evaluation.id}
          className="run-audit-record"
          aria-labelledby={`evaluation-${evaluation.id}`}
        >
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <p className="workbench-kicker">Evaluator · {evaluation.method}</p>
              <h4 id={`evaluation-${evaluation.id}`} className="mt-1 font-editorial text-lg">
                {evaluation.evaluatorId}
                <span className="ml-2 font-mono text-xs text-muted-foreground">
                  v{evaluation.evaluatorVersion}
                </span>
              </h4>
            </div>
            <span className={`text-sm font-semibold ${statusClass[evaluation.status]}`}>
              {evaluation.status.replace('-', ' ')} · severity {evaluation.severity}
            </span>
          </div>
          <p className="mt-3 text-sm leading-6">{evaluation.explanation}</p>
          <dl className="run-audit-meta">
            <div><dt>Evaluated</dt><dd>{formatTimestamp(evaluation.evaluatedAt)}</dd></div>
            <div><dt>Artifact references</dt><dd>{evaluation.artifactIds.join(', ') || 'None recorded'}</dd></div>
            <div><dt>Evidence references</dt><dd>{evaluation.evidenceIds.join(', ') || 'None recorded'}</dd></div>
          </dl>
          {Object.keys(evaluation.metrics).length > 0 ? (
            <div className="mt-4 flex flex-wrap gap-x-6 gap-y-3">
              {Object.entries(evaluation.metrics).map(([name, value]) => (
                <OriginValueDisplay key={name} label={name.replaceAll('_', ' ')} value={value} />
              ))}
            </div>
          ) : null}
          <details className="run-developer-detail">
            <summary>Evaluation developer detail</summary>
            <dl>
              <div><dt>Evaluation ID</dt><dd className="font-mono">{evaluation.id}</dd></div>
              <div><dt>Raw method</dt><dd>{evaluation.rawMethod ?? 'Not applicable'}</dd></div>
              <div><dt>Raw status</dt><dd>{evaluation.rawStatus ?? 'Not applicable'}</dd></div>
              <div><dt>Raw severity</dt><dd>{evaluation.rawSeverity ?? 'Not applicable'}</dd></div>
            </dl>
          </details>
        </article>
      ))}
    </div>
  );
}
