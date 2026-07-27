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

const fixtureString = (value: string) => ({
  value,
  origin: 'fixture-derived',
  unit: null,
});

const fixtureOperationalMetrics = {
  duration_ms: fixtureNumber(3_420, 'ms'),
  latency_ms: fixtureNumber(640, 'ms'),
  cost: fixtureNumber(1.25, 'USD'),
  input_tokens: fixtureNumber(148, 'tokens'),
  output_tokens: fixtureNumber(92, 'tokens'),
  total_tokens: fixtureNumber(240, 'tokens'),
  provider: fixtureString('contract-fixture-provider'),
  model: fixtureString('contract-fixture-model'),
  prompt_id: fixtureString('fixture-research-synthesis'),
  prompt_version: fixtureString('test-v3'),
};

const unavailableOperationalMetrics = {
  duration_ms: unavailable('ms'),
  latency_ms: unavailable('ms'),
  cost: unavailable('USD'),
  input_tokens: unavailable('tokens'),
  output_tokens: unavailable('tokens'),
  total_tokens: unavailable('tokens'),
  provider: unavailable(),
  model: unavailable(),
  prompt_id: unavailable(),
  prompt_version: unavailable(),
};

const baseRun = {
  workflow_id: 'inspectable-fixture-workflow',
  workflow_version: '2.3.1-test',
  objective: 'Inspect invented archive labels and document which fixture record supports each classification.',
  inputs: { source_pack: 'test-only-inspection-pack', immutable: true },
  mode: 'fixture',
  created_at: '2026-02-03T10:15:00Z',
  started_at: '2026-02-03T10:15:01Z',
  updated_at: '2026-02-03T10:15:05Z',
  completed_at: '2026-02-03T10:15:05Z',
  provider_policy_snapshot: {
    policy_id: 'fixture-policy-test-only',
    raw_prompt: 'TEST ONLY: classify invented labels from the supplied records.',
    developer_metadata: { deterministic: true },
  },
  task_ids: ['task-detail-collect', 'task-detail-synthesize'],
  artifact_ids: ['artifact-detail-fixture'],
  evidence_ids: ['evidence-detail-alpha', 'evidence-detail-beta'],
  evaluation_ids: ['evaluation-detail-fixture'],
  warnings: [],
  failure_summary: null,
};

export const completedDetailRun = {
  ...baseRun,
  run_id: 'run-detail-completed',
  status: 'completed',
  operational_metrics: fixtureOperationalMetrics,
};

export const runningDetailRun = {
  ...baseRun,
  run_id: 'run-detail-running',
  status: 'running',
  completed_at: null,
  artifact_ids: [],
  evidence_ids: [],
  evaluation_ids: [],
  operational_metrics: {
    ...unavailableOperationalMetrics,
    duration_ms: fixtureNumber(2_180, 'ms'),
  },
};

export const warningDetailRun = {
  ...baseRun,
  run_id: 'run-detail-warning',
  status: 'completed_with_warnings',
  warnings: ['The BETA fixture record was unavailable during the collection task.'],
  operational_metrics: fixtureOperationalMetrics,
};

export const failedDetailRun = {
  ...baseRun,
  run_id: 'run-detail-failed',
  status: 'failed',
  task_ids: ['task-detail-failed'],
  artifact_ids: [],
  evidence_ids: [],
  evaluation_ids: [],
  failure_summary: 'Fixture validation intentionally rejected an invented record shape.',
  operational_metrics: unavailableOperationalMetrics,
};

export const cancelledDetailRun = {
  ...baseRun,
  run_id: 'run-detail-cancelled',
  status: 'cancelled',
  completed_at: '2026-02-03T10:15:03Z',
  artifact_ids: [],
  evidence_ids: [],
  evaluation_ids: [],
  operational_metrics: unavailableOperationalMetrics,
};

export const partialDetailRun = {
  ...baseRun,
  run_id: 'run-detail-partial',
  status: 'future-inspection-state',
  mode: 'future-fixture-mode',
  operational_metrics: unavailableOperationalMetrics,
};

export const malformedDetailRun = {
  run_id: 81,
  workflow_id: null,
  objective: null,
  status: 'celebrating',
};

export const detailTasks = [
  {
    task_id: 'task-detail-collect',
    run_id: 'run-detail-completed',
    phase: 'collect',
    purpose: 'Read the controlled fixture records.',
    status: 'completed_with_warnings',
    attempt: 2,
    started_at: '2026-02-03T10:15:01Z',
    completed_at: '2026-02-03T10:15:03Z',
    operational_metrics: {
      ...unavailableOperationalMetrics,
      duration_ms: fixtureNumber(1_870, 'ms'),
    },
    output_summary: 'ALPHA was collected; BETA remained unavailable.',
    degradation_reason: 'The second fixture record intentionally returned an unavailable state.',
    error_summary: null,
    capability: 'fixture-reader',
    dependency_ids: [],
    tool_invocations: [
      {
        invocation_id: 'invocation-detail-reader',
        tool: 'fixture-reader',
        status: 'completed',
        latency_ms: fixtureNumber(410, 'ms'),
        input_summary: 'Read two invented records.',
        result_summary: 'One available record and one unavailable record.',
      },
    ],
  },
  {
    task_id: 'task-detail-synthesize',
    run_id: 'run-detail-completed',
    phase: 'synthesize',
    purpose: 'Write an inspectable artifact without filling evidence gaps.',
    status: 'completed',
    attempt: 1,
    started_at: '2026-02-03T10:15:03Z',
    completed_at: '2026-02-03T10:15:05Z',
    operational_metrics: {
      ...unavailableOperationalMetrics,
      duration_ms: fixtureNumber(1_310, 'ms'),
    },
    output_summary: 'Published two material claims with explicit support states.',
    degradation_reason: null,
    error_summary: null,
    capability: 'fixture-synthesizer',
    dependency_ids: ['task-detail-collect'],
    tool_invocations: [],
  },
];

export const failedDetailTasks = [
  {
    ...detailTasks[0],
    run_id: 'run-detail-failed',
    task_id: 'task-detail-failed',
    status: 'failed',
    attempt: 3,
    output_summary: null,
    degradation_reason: null,
    error_summary: 'The test-only validator rejected a deliberately malformed fixture record.',
  },
];

export const detailEvents = [
  {
    event_id: 'event-detail-started',
    version: '1',
    run_id: 'run-detail-completed',
    task_id: null,
    type: 'lifecycle',
    prior_state: 'pending',
    new_state: 'running',
    occurred_at: '2026-02-03T10:15:01Z',
    summary: 'Controlled fixture execution started.',
    trace_id: 'trace-test-only-detail',
    measured_values: unavailableOperationalMetrics,
  },
  {
    event_id: 'event-detail-completed',
    version: '2',
    run_id: 'run-detail-completed',
    task_id: 'task-detail-synthesize',
    type: 'lifecycle',
    prior_state: 'running',
    new_state: 'completed',
    occurred_at: '2026-02-03T10:15:05Z',
    summary: 'Artifact and evaluation records were attached.',
    trace_id: 'trace-test-only-detail',
    measured_values: fixtureOperationalMetrics,
  },
];

export const detailEvidence = [
  {
    evidence_id: 'evidence-detail-alpha',
    run_id: 'run-detail-completed',
    source_type: 'contract-fixture-record',
    locator: 'fixture://inspection-pack/record-alpha#line-4',
    title: 'Invented archive record ALPHA',
    publisher: 'Contract fixture archive',
    retrieved_at: '2026-02-03T10:15:02Z',
    content_hash: 'sha256:test-only-alpha-content-hash',
    fixture_reference: 'inspection-pack/record-alpha',
    excerpt: 'Archive label ALPHA belongs to the invented copper collection.',
    structured_value: { label: 'ALPHA', collection: 'copper' },
    source_timestamp: '2026-02-02T09:00:00Z',
    source_version: 'fixture-r4',
    retrieval_tool: 'fixture-reader',
    retrieval_parameters: { record: 'alpha', line: 4 },
    usage_classification: 'test-only',
    availability: 'available',
  },
  {
    evidence_id: 'evidence-detail-beta',
    run_id: 'run-detail-completed',
    source_type: 'contract-fixture-record',
    locator: 'fixture://inspection-pack/record-beta#line-7',
    title: 'Invented archive record BETA',
    publisher: 'Contract fixture archive',
    retrieved_at: null,
    content_hash: null,
    fixture_reference: 'inspection-pack/record-beta',
    excerpt: null,
    structured_value: null,
    source_timestamp: null,
    source_version: null,
    retrieval_tool: 'fixture-reader',
    retrieval_parameters: { record: 'beta', line: 7 },
    usage_classification: 'test-only',
    availability: 'unavailable',
  },
];

export const detailArtifact = {
  artifact_id: 'artifact-detail-fixture',
  run_id: 'run-detail-completed',
  type: 'InspectableResearchDossier',
  schema_version: 'test-v2',
  workflow_id: 'inspectable-fixture-workflow',
  workflow_version: '2.3.1-test',
  generating_task_id: 'task-detail-synthesize',
  generating_phase: 'synthesize',
  created_at: '2026-02-03T10:15:05Z',
  content_hash: 'sha256:test-only-artifact-content-hash',
  evidence_ids: ['evidence-detail-alpha', 'evidence-detail-beta'],
  validation_status: 'partial',
  content: {
    title: 'An inspectable dossier of invented archive labels',
    summary: 'This test-only artifact separates supported classification from an explicit evidence gap.',
    sections: [
      {
        heading: 'Available classification',
        body: 'The controlled archive supplies an exact excerpt for ALPHA.',
      },
      {
        heading: 'Documented limitation',
        body: 'No classification is inferred for BETA while its fixture record is unavailable.',
      },
    ],
  },
  claims: [
    {
      claim_id: 'claim-detail-supported',
      claim_text: 'ALPHA belongs to the invented copper collection.',
      artifact_id: 'artifact-detail-fixture',
      section_reference: 'available-classification',
      evidence_ids: ['evidence-detail-alpha'],
      support_status: 'supported',
      verification_method: 'exact-fixture-excerpt',
      score: fixtureNumber(0.93),
      explanation: 'The exact ALPHA classification appears in the cited fixture excerpt.',
      verified_at: '2026-02-03T10:15:05Z',
    },
    {
      claim_id: 'claim-detail-partial',
      claim_text: 'ALPHA and BETA both belong to named invented collections.',
      artifact_id: 'artifact-detail-fixture',
      section_reference: 'documented-limitation',
      evidence_ids: ['evidence-detail-alpha', 'evidence-detail-beta'],
      support_status: 'partially_supported',
      verification_method: 'fixture-availability-check',
      score: fixtureNumber(0.47),
      explanation: 'ALPHA has exact evidence, while the BETA record and excerpt are unavailable.',
      verified_at: '2026-02-03T10:15:05Z',
    },
    {
      claim_id: 'claim-detail-disputed',
      claim_text: 'ALPHA belongs to the invented silver collection.',
      artifact_id: 'artifact-detail-fixture',
      section_reference: 'available-classification',
      evidence_ids: ['evidence-detail-alpha'],
      support_status: 'disputed',
      verification_method: 'exact-fixture-excerpt',
      score: fixtureNumber(0.12),
      explanation: 'The available excerpt identifies copper, which conflicts with silver.',
      verified_at: '2026-02-03T10:15:05Z',
    },
    {
      claim_id: 'claim-detail-unsupported',
      claim_text: 'GAMMA belongs to an invented collection.',
      artifact_id: 'artifact-detail-fixture',
      section_reference: 'documented-limitation',
      evidence_ids: [],
      support_status: 'unsupported',
      verification_method: 'fixture-availability-check',
      score: unavailable(),
      explanation: 'No fixture record or excerpt for GAMMA was supplied.',
      verified_at: '2026-02-03T10:15:05Z',
    },
  ],
};

export const detailEvaluation = {
  evaluation_id: 'evaluation-detail-fixture',
  run_id: 'run-detail-completed',
  evaluator_id: 'fixture-grounding-auditor',
  evaluator_version: 'test-v5',
  method: 'deterministic',
  status: 'warning',
  severity: 'warning',
  explanation: 'Supported claims have exact excerpts; the BETA evidence gap remains explicit.',
  artifact_ids: ['artifact-detail-fixture'],
  evidence_ids: ['evidence-detail-alpha', 'evidence-detail-beta'],
  metrics: {
    inspectable_claim_ratio: fixtureNumber(0.78),
  },
  evaluated_at: '2026-02-03T10:15:05Z',
};

export const runDetailContractFixtures = {
  runs: {
    completed: completedDetailRun,
    running: runningDetailRun,
    warning: warningDetailRun,
    failed: failedDetailRun,
    cancelled: cancelledDetailRun,
    partial: partialDetailRun,
    malformed: malformedDetailRun,
  },
  tasks: detailTasks,
  failedTasks: failedDetailTasks,
  events: detailEvents,
  evidence: detailEvidence,
  artifact: detailArtifact,
  evaluation: detailEvaluation,
};
