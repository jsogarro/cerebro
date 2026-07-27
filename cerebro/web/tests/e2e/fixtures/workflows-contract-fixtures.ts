const unavailable = (unit: string | null = null) => ({
  value: null,
  origin: 'unavailable',
  unit,
});

const unavailableMetrics = {
  duration_ms: unavailable('ms'),
  cost: unavailable('USD'),
  input_tokens: unavailable('tokens'),
  output_tokens: unavailable('tokens'),
  total_tokens: unavailable('tokens'),
  provider: unavailable(),
  model: unavailable(),
};

export const canonicalWorkflowFixture = {
  workflow_id: 'comparative-research.corporate-revenue-stgnn',
  version: '1.0.0',
  name: 'Comparative Research Brief',
  description:
    'Produces an inspectable research brief, evidence bundle, run manifest, and evaluation summary from a bounded objective and controlled sources.',
  input_schema: {
    objective: { type: 'string' },
    source_pack_id: { type: 'string' },
    audience: { type: 'string' },
  },
  phases: [
    'Intake',
    'Plan',
    'Acquire',
    'Extract',
    'Compare',
    'Synthesize',
    'Verify',
    'Publish',
  ],
  required_tools: ['controlled-source-reader'],
  provider_policy: null,
  evidence_policy: { citations_required: true },
  artifact_schemas: ['Research Brief', 'Evidence Bundle', 'Run Manifest', 'Evaluation Summary'],
  evaluators: ['schema-conformance', 'citation-resolution', 'claim-support'],
  maturity: 'experimental',
  supported_modes: ['fixture', 'live'],
  limitations: [
    'The controlled corpus is fixed for reproducibility.',
    'Fixture mode does not retrieve fresh sources or imply live provider availability.',
    'The workflow evaluates viability; it does not train a model or produce a revenue forecast.',
  ],
  typical_metrics: unavailableMetrics,
};

export const blockedWorkflowFixture = {
  ...canonicalWorkflowFixture,
  workflow_id: 'comparative-research-live-only',
  version: '0.9.0-preview',
  name: 'Comparative Research Brief — live only',
  maturity: 'preview',
  supported_modes: ['live'],
};

export const incompatibleFixtureWorkflowFixture = {
  ...canonicalWorkflowFixture,
  version: '0.9.0',
  name: 'Comparative Research Brief — prior fixture version',
};

export const createdRunFixture = {
  run_id: 'run-controlled-fixture-001',
  workflow_id: canonicalWorkflowFixture.workflow_id,
  workflow_version: canonicalWorkflowFixture.version,
  objective:
    'Assess whether spatiotemporal graph neural networks (ST-GNNs) are viable for predicting corporate revenue, using only the approved source corpus.',
  inputs: {
    source_pack_id: 'golden-run.corporate-revenue-stgnn-viability.v1',
    source_count: 5,
  },
  mode: 'fixture',
  status: 'pending',
  created_at: '2026-07-26T12:00:00Z',
  started_at: null,
  updated_at: '2026-07-26T12:00:00Z',
  completed_at: null,
  provider_policy_snapshot: null,
  task_ids: [],
  artifact_ids: [],
  evidence_ids: [],
  evaluation_ids: [],
  operational_metrics: unavailableMetrics,
  warnings: [],
  failure_summary: null,
};
