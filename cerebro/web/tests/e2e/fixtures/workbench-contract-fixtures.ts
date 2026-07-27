const unavailable = (unit: string | null = null) => ({
  value: null,
  origin: 'unavailable',
  unit,
});

const fixtureNumber = (value: number, unit: string | null = null) => ({
  value,
  origin: 'fixture-derived',
  unit,
});

const measuredNumber = (value: number, unit: string | null = null) => ({
  value,
  origin: 'measured',
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

const fixtureMetrics = {
  duration_ms: fixtureNumber(1_250, 'ms'),
  cost: fixtureNumber(0, 'USD'),
  input_tokens: fixtureNumber(18, 'tokens'),
  output_tokens: fixtureNumber(12, 'tokens'),
  total_tokens: fixtureNumber(30, 'tokens'),
  provider: {
    value: 'fixture-provider',
    origin: 'fixture-derived',
    unit: null,
  },
  model: {
    value: 'fixture-model',
    origin: 'fixture-derived',
    unit: null,
  },
};

const baseRun = {
  workflow_id: 'workflow-contract-fixture',
  workflow_version: '1.0.0-test',
  objective: 'Classify invented fixture tokens using only the supplied test records.',
  inputs: {
    source_ids: ['source-fixture-a', 'source-fixture-b'],
  },
  mode: 'fixture',
  created_at: '2026-01-01T00:00:00Z',
  started_at: null,
  updated_at: '2026-01-01T00:00:00Z',
  completed_at: null,
  provider_policy_snapshot: null,
  task_ids: [],
  artifact_ids: [],
  evidence_ids: [],
  evaluation_ids: [],
  warnings: [],
  failure_summary: null,
};

export const pendingRunFixture = {
  ...baseRun,
  run_id: 'run-pending-fixture',
  status: 'pending',
  operational_metrics: unavailableMetrics,
};

export const runningRunFixture = {
  ...baseRun,
  run_id: 'run-running-fixture',
  status: 'running',
  started_at: '2026-01-01T00:00:01Z',
  updated_at: '2026-01-01T00:00:02Z',
  task_ids: ['task-running-fixture'],
  operational_metrics: {
    ...unavailableMetrics,
    duration_ms: measuredNumber(1_000, 'ms'),
  },
};

export const warningRunFixture = {
  ...baseRun,
  run_id: 'run-warning-fixture',
  status: 'completed_with_warnings',
  started_at: '2026-01-01T00:00:01Z',
  completed_at: '2026-01-01T00:00:03Z',
  updated_at: '2026-01-01T00:00:03Z',
  warnings: ['One invented fixture record was intentionally unavailable.'],
  artifact_ids: ['artifact-mixed-support-fixture'],
  evidence_ids: ['evidence-available-fixture', 'evidence-unavailable-fixture'],
  operational_metrics: fixtureMetrics,
};

export const failedRunFixture = {
  ...baseRun,
  run_id: 'run-failed-fixture',
  status: 'failed',
  started_at: '2026-01-01T00:00:01Z',
  completed_at: '2026-01-01T00:00:02Z',
  updated_at: '2026-01-01T00:00:02Z',
  failure_summary: 'The contract fixture intentionally stopped at its validation step.',
  operational_metrics: unavailableMetrics,
};

export const cancelledRunFixture = {
  ...baseRun,
  run_id: 'run-cancelled-fixture',
  status: 'cancelled',
  started_at: '2026-01-01T00:00:01Z',
  completed_at: '2026-01-01T00:00:02Z',
  updated_at: '2026-01-01T00:00:02Z',
  operational_metrics: unavailableMetrics,
};

export const completedRunFixture = {
  ...baseRun,
  run_id: 'run-completed-fixture',
  status: 'completed',
  started_at: '2026-01-01T00:00:01Z',
  completed_at: '2026-01-01T00:00:03Z',
  updated_at: '2026-01-01T00:00:03Z',
  artifact_ids: ['artifact-mixed-support-fixture'],
  evidence_ids: ['evidence-available-fixture'],
  evaluation_ids: ['evaluation-passed-fixture'],
  operational_metrics: fixtureMetrics,
};

export const unavailableEvidenceFixture = {
  evidence_id: 'evidence-unavailable-fixture',
  run_id: 'run-warning-fixture',
  source_type: 'contract-fixture',
  locator: 'fixture://unavailable-record',
  title: null,
  publisher: null,
  retrieved_at: null,
  content_hash: null,
  fixture_reference: 'missing-fixture-record',
  excerpt: null,
  structured_value: null,
  source_timestamp: null,
  source_version: null,
  retrieval_tool: null,
  retrieval_parameters: null,
  usage_classification: 'test-only',
  availability: 'unavailable',
};

export const availableEvidenceFixture = {
  evidence_id: 'evidence-available-fixture',
  run_id: 'run-completed-fixture',
  source_type: 'contract-fixture',
  locator: 'fixture://record-a',
  title: 'Invented record A',
  publisher: 'Contract fixture',
  retrieved_at: '2026-01-01T00:00:01Z',
  content_hash: 'fixture-hash-not-content-addressed',
  fixture_reference: 'record-a',
  excerpt: 'The invented token ALPHA is assigned to group one.',
  structured_value: null,
  source_timestamp: '2026-01-01T00:00:00Z',
  source_version: 'test-v1',
  retrieval_tool: 'fixture-reader',
  retrieval_parameters: { record: 'a' },
  usage_classification: 'test-only',
  availability: 'available',
};

export const mixedClaimSupportFixture = [
  {
    claim_id: 'claim-supported-fixture',
    claim_text: 'The invented fixture token ALPHA is assigned to group one.',
    artifact_id: 'artifact-mixed-support-fixture',
    section_reference: 'fixture-section-1',
    evidence_ids: ['evidence-available-fixture'],
    support_status: 'supported',
    verification_method: 'deterministic-fixture-match',
    score: fixtureNumber(1),
    explanation: 'The exact invented token assignment appears in fixture record A.',
    verified_at: '2026-01-01T00:00:03Z',
  },
  {
    claim_id: 'claim-partial-fixture',
    claim_text: 'The invented fixture tokens ALPHA and BETA are both assigned to group one.',
    artifact_id: 'artifact-mixed-support-fixture',
    section_reference: 'fixture-section-1',
    evidence_ids: ['evidence-available-fixture'],
    support_status: 'partially_supported',
    verification_method: 'deterministic-fixture-match',
    score: fixtureNumber(0.5),
    explanation: 'Only the invented ALPHA assignment appears in the available fixture.',
    verified_at: '2026-01-01T00:00:03Z',
  },
  {
    claim_id: 'claim-disputed-fixture',
    claim_text: 'The invented fixture token ALPHA is assigned to group two.',
    artifact_id: 'artifact-mixed-support-fixture',
    section_reference: 'fixture-section-2',
    evidence_ids: ['evidence-available-fixture'],
    support_status: 'disputed',
    verification_method: 'deterministic-fixture-match',
    score: fixtureNumber(0),
    explanation: 'Fixture record A assigns the invented token to a different group.',
    verified_at: '2026-01-01T00:00:03Z',
  },
  {
    claim_id: 'claim-unsupported-fixture',
    claim_text: 'The invented fixture token GAMMA has a recorded group.',
    artifact_id: 'artifact-mixed-support-fixture',
    section_reference: 'fixture-section-2',
    evidence_ids: [],
    support_status: 'unsupported',
    verification_method: 'deterministic-fixture-match',
    score: unavailable(),
    explanation: 'No fixture record supplies an assignment for the invented token.',
    verified_at: '2026-01-01T00:00:03Z',
  },
];

export const mixedSupportArtifactFixture = {
  artifact_id: 'artifact-mixed-support-fixture',
  run_id: 'run-completed-fixture',
  type: 'ResearchBrief',
  schema_version: 'test-v1',
  workflow_id: 'workflow-contract-fixture',
  workflow_version: '1.0.0-test',
  generating_task_id: 'task-completed-fixture',
  generating_phase: 'fixture-publish',
  created_at: '2026-01-01T00:00:03Z',
  content_hash: 'fixture-artifact-hash-not-content-addressed',
  evidence_ids: ['evidence-available-fixture', 'evidence-unavailable-fixture'],
  validation_status: 'partial',
  content: {
    title: 'Invented token classification fixture',
  },
  claims: mixedClaimSupportFixture,
};

export const malformedRunFixture = {
  run_id: 42,
  status: 'celebrating',
  objective: null,
  operational_metrics: {
    cost: {
      value: 99,
      origin: 'guessed',
      unit: 'USD',
    },
  },
};

export const partialRunFixture = {
  ...baseRun,
  run_id: 'run-partial-fixture',
  status: 'future_state_not_known_to_client',
  mode: 'future_mode_not_known_to_client',
  operational_metrics: {
    ...unavailableMetrics,
    cost: {
      value: 99,
      origin: 'guessed',
      unit: 'USD',
    },
  },
};

export const workbenchContractFixtures = {
  runs: {
    pending: pendingRunFixture,
    running: runningRunFixture,
    warning: warningRunFixture,
    failed: failedRunFixture,
    cancelled: cancelledRunFixture,
    completed: completedRunFixture,
    malformed: malformedRunFixture,
    partial: partialRunFixture,
  },
  evidence: {
    available: availableEvidenceFixture,
    unavailable: unavailableEvidenceFixture,
  },
  artifact: mixedSupportArtifactFixture,
};
