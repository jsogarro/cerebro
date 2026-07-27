import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { apiClient } from './client';
import {
  adaptOperationalMetrics,
  adaptOriginValue,
  isRecord,
  listPayload,
  normalizeToken,
  parseAvailabilityState,
  parseExecutionMode,
  parseLifecycleState,
  parseSupportState,
  presentation,
  readNumber,
  readRecord,
  readString,
  readStringArray,
  type Artifact,
  type ArtifactValidationState,
  type ClaimSupport,
  type CollectionContract,
  type Evaluation,
  type EvaluationSeverity,
  type EvaluationState,
  type Event,
  type Evidence,
  type LifecycleState,
  type OriginValue,
  type ResourceContract,
  type Run,
  type Task,
  type ToolInvocation,
} from './workbench';

export interface RunDto {
  run_id: unknown;
  workflow_id: unknown;
  workflow_version: unknown;
  objective: unknown;
  inputs?: unknown;
  mode?: unknown;
  status?: unknown;
  created_at?: unknown;
  started_at?: unknown;
  updated_at?: unknown;
  completed_at?: unknown;
  provider_policy_snapshot?: unknown;
  task_ids?: unknown;
  artifact_ids?: unknown;
  evidence_ids?: unknown;
  evaluation_ids?: unknown;
  operational_metrics?: unknown;
  warnings?: unknown;
  failure_summary?: unknown;
}

export interface TaskDto {
  task_id: unknown;
  run_id: unknown;
  phase: unknown;
  purpose?: unknown;
  dependency_ids?: unknown;
  capability?: unknown;
  status?: unknown;
  attempt?: unknown;
  started_at?: unknown;
  completed_at?: unknown;
  operational_metrics?: unknown;
  tool_invocations?: unknown;
  output_summary?: unknown;
  error_summary?: unknown;
  parent_task_id?: unknown;
  child_task_ids?: unknown;
}

export interface EventDto {
  event_id: unknown;
  version: unknown;
  run_id: unknown;
  task_id?: unknown;
  type: unknown;
  prior_state?: unknown;
  new_state?: unknown;
  occurred_at?: unknown;
  summary: unknown;
  trace_id?: unknown;
  measured_values?: unknown;
}

export interface EvidenceDto {
  evidence_id: unknown;
  run_id: unknown;
  source_type: unknown;
  locator: unknown;
  availability?: unknown;
}

export interface ClaimSupportDto {
  claim_id: unknown;
  claim_text: unknown;
  artifact_id: unknown;
  evidence_ids?: unknown;
  support_status?: unknown;
}

export interface ArtifactDto {
  artifact_id: unknown;
  run_id: unknown;
  type: unknown;
  schema_version: unknown;
  workflow_id: unknown;
  workflow_version: unknown;
  claims?: unknown;
}

export interface EvaluationDto {
  evaluation_id: unknown;
  run_id: unknown;
  evaluator_id: unknown;
  evaluator_version: unknown;
  method?: unknown;
  status?: unknown;
  severity?: unknown;
}

export interface CreateRunInput {
  workflowId: string;
  workflowVersion: string;
  objective: string;
  inputs: Readonly<Record<string, unknown>>;
  mode: 'fixture' | 'live';
  budget?: {
    maxDurationSeconds?: number;
    maxCost?: number;
    currency?: string;
  };
  providerPolicyId?: string;
}

export interface RunListFilters {
  workflowId?: string;
  status?: LifecycleState;
  limit?: number;
  cursor?: string;
}

export interface RunSubresourceOptions {
  runStatus?: LifecycleState;
}

export const runKeys = {
  all: ['runs'] as const,
  list: (filters: RunListFilters = {}) => [...runKeys.all, 'list', filters] as const,
  detail: (runId: string) => [...runKeys.all, 'detail', runId] as const,
  tasks: (runId: string) => [...runKeys.detail(runId), 'tasks'] as const,
  events: (runId: string) => [...runKeys.detail(runId), 'events'] as const,
  evidence: (runId: string) => [...runKeys.detail(runId), 'evidence'] as const,
  artifacts: (runId: string) => [...runKeys.detail(runId), 'artifacts'] as const,
  evaluations: (runId: string) => [...runKeys.detail(runId), 'evaluations'] as const,
};

const ACTIVE_POLL_INTERVAL_MS = 2_000;
const MAX_POLL_FAILURES = 3;

function requiredStrings(
  record: Record<string, unknown>,
  fields: ReadonlyArray<{ canonical: string; aliases?: readonly string[] }>,
): { values: Record<string, string>; issues: string[] } {
  const values: Record<string, string> = {};
  const issues: string[] = [];
  for (const field of fields) {
    const keys = [field.canonical, ...(field.aliases ?? [])];
    const value = keys.reduce<string | null>(
      (found, key) => found ?? readString(record, key),
      null,
    );
    if (value === null) issues.push(`${field.canonical} is missing or invalid.`);
    else values[field.canonical] = value;
  }
  return { values, issues };
}

function referenceIds(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((item) => {
    if (typeof item === 'string') return item.trim().length > 0 ? [item] : [];
    if (!isRecord(item)) return [];
    const id =
      readString(item, 'id') ??
      readString(item, 'task_id') ??
      readString(item, 'artifact_id') ??
      readString(item, 'evidence_id') ??
      readString(item, 'evaluation_id');
    return id === null ? [] : [id];
  });
}

function optionalTimestamp(record: Record<string, unknown>, key: string): string | null {
  return readString(record, key);
}

function unknownStateIssue(kind: string, raw: string | null): string {
  return raw === null ? `${kind} is missing.` : `${kind} "${raw}" is unknown.`;
}

export function adaptRun(value: unknown): ResourceContract<Run> {
  if (!isRecord(value)) {
    return { value: null, presentation: presentation.malformed(['Run payload is not an object.']) };
  }
  const required = requiredStrings(value, [
    { canonical: 'run_id', aliases: ['id'] },
    { canonical: 'workflow_id' },
    { canonical: 'workflow_version' },
    { canonical: 'objective' },
  ]);
  if (required.issues.length > 0) {
    return { value: null, presentation: presentation.malformed(required.issues) };
  }

  const issues: string[] = [];
  const reportedStatus =
    typeof value.status === 'string' && normalizeToken(value.status) === 'queued'
      ? { state: 'pending' as const, raw: value.status }
      : parseLifecycleState(value.status);
  const status = reportedStatus;
  const mode = parseExecutionMode(value.mode ?? value.execution_mode);
  if (status.state === 'unknown') issues.push(unknownStateIssue('status', status.raw));
  if (mode.mode === 'unknown') issues.push(unknownStateIssue('execution mode', mode.raw));
  const inputs = readRecord(value, 'inputs') ?? readRecord(value, 'normalized_inputs');
  if (inputs === null) issues.push('inputs are unavailable.');

  return {
    value: {
      id: required.values.run_id,
      workflowId: required.values.workflow_id,
      workflowVersion: required.values.workflow_version,
      objective: required.values.objective,
      inputs: inputs ?? {},
      mode: mode.mode,
      rawMode: mode.raw,
      status: status.state,
      rawStatus: status.raw,
      createdAt: optionalTimestamp(value, 'created_at'),
      startedAt: optionalTimestamp(value, 'started_at'),
      updatedAt: optionalTimestamp(value, 'updated_at'),
      completedAt: optionalTimestamp(value, 'completed_at'),
      providerPolicySnapshot: readRecord(value, 'provider_policy_snapshot'),
      taskIds: referenceIds(value.task_ids ?? value.tasks),
      artifactIds: referenceIds(value.artifact_ids ?? value.artifacts),
      evidenceIds: referenceIds(value.evidence_ids ?? value.evidence),
      evaluationIds: referenceIds(value.evaluation_ids ?? value.evaluations),
      metrics: adaptOperationalMetrics(value.operational_metrics ?? value.metrics),
      warnings: readStringArray(value, 'warnings'),
      failureSummary: readString(value, 'failure_summary') ?? readString(value, 'error_summary'),
    },
    presentation: presentation.ready(issues),
  };
}

function adaptCollection<T>(
  value: unknown,
  keys: readonly string[],
  label: string,
  adapter: (item: unknown) => ResourceContract<T>,
): CollectionContract<T> {
  const payload = listPayload(value, keys);
  if (payload === null) {
    return {
      items: [],
      presentation: presentation.malformed([`${label} collection is not an array.`]),
    };
  }
  const items: T[] = [];
  const issues: string[] = [];
  payload.forEach((item, index) => {
    const adapted = adapter(item);
    if (adapted.value !== null) items.push(adapted.value);
    adapted.presentation.issues.forEach((issue) => issues.push(`Item ${index + 1}: ${issue}`));
  });
  if (payload.length === 0) {
    return { items, presentation: presentation.empty(`No ${label.toLowerCase()} are available.`) };
  }
  if (items.length === 0) return { items, presentation: presentation.malformed(issues) };
  return { items, presentation: presentation.ready(issues) };
}

export function adaptRunCollection(value: unknown): CollectionContract<Run> {
  return adaptCollection(value, ['items', 'runs', 'results'], 'Runs', adaptRun);
}

function adaptToolInvocation(value: unknown): ToolInvocation | null {
  if (!isRecord(value)) return null;
  const id = readString(value, 'invocation_id') ?? readString(value, 'id');
  const tool = readString(value, 'tool') ?? readString(value, 'tool_name');
  if (id === null || tool === null) return null;
  const status = parseLifecycleState(value.status);
  return {
    id,
    tool,
    status: status.state,
    rawStatus: status.raw,
    latencyMs: adaptOriginValue<number>(value.latency_ms, 'number', 'ms'),
    inputSummary: readString(value, 'input_summary'),
    resultSummary: readString(value, 'result_summary'),
  };
}

export function adaptTask(value: unknown): ResourceContract<Task> {
  if (!isRecord(value)) {
    return { value: null, presentation: presentation.malformed(['Task payload is not an object.']) };
  }
  const required = requiredStrings(value, [
    { canonical: 'task_id', aliases: ['id'] },
    { canonical: 'run_id' },
    { canonical: 'phase' },
  ]);
  if (required.issues.length > 0) {
    return { value: null, presentation: presentation.malformed(required.issues) };
  }
  const issues: string[] = [];
  const status = parseLifecycleState(value.status);
  if (status.state === 'unknown') issues.push(unknownStateIssue('status', status.raw));
  const rawInvocations = Array.isArray(value.tool_invocations) ? value.tool_invocations : [];
  if (value.tool_invocations !== undefined && !Array.isArray(value.tool_invocations)) {
    issues.push('tool_invocations is not an array.');
  }
  const toolInvocations = rawInvocations.flatMap((item) => {
    const invocation = adaptToolInvocation(item);
    return invocation === null ? [] : [invocation];
  });
  if (toolInvocations.length !== rawInvocations.length) {
    issues.push('One or more tool invocations are malformed.');
  }
  if (toolInvocations.some((invocation) => invocation.status === 'unknown')) {
    issues.push('One or more tool invocation statuses are unknown.');
  }
  const attempt = readNumber(value, 'attempt');
  return {
    value: {
      id: required.values.task_id,
      runId: required.values.run_id,
      phase: required.values.phase,
      purpose: readString(value, 'purpose') ?? required.values.phase,
      dependencyIds: referenceIds(value.dependency_ids),
      capability: readString(value, 'capability') ?? readString(value, 'assigned_capability'),
      status: status.state,
      rawStatus: status.raw,
      attempt: attempt !== null && Number.isInteger(attempt) && attempt >= 0 ? attempt : null,
      startedAt: optionalTimestamp(value, 'started_at'),
      completedAt: optionalTimestamp(value, 'completed_at'),
      metrics: adaptOperationalMetrics(value.operational_metrics ?? value.metrics),
      toolInvocations,
      outputSummary: readString(value, 'output_summary'),
      degradationReason: readString(value, 'degradation_reason'),
      errorSummary: readString(value, 'error_summary'),
      parentTaskId: readString(value, 'parent_task_id'),
      childTaskIds: referenceIds(value.child_task_ids),
    },
    presentation: presentation.ready(issues),
  };
}

export function adaptTaskCollection(value: unknown): CollectionContract<Task> {
  return adaptCollection(value, ['items', 'tasks', 'results'], 'Tasks', adaptTask);
}

function optionalLifecycle(value: unknown): {
  state: LifecycleState | null;
  raw: string | null;
  issue: string | null;
} {
  if (value === null || value === undefined) return { state: null, raw: null, issue: null };
  const parsed = parseLifecycleState(value);
  return {
    state: parsed.state,
    raw: parsed.raw,
    issue: parsed.state === 'unknown' ? unknownStateIssue('event state', parsed.raw) : null,
  };
}

export function adaptEvent(value: unknown): ResourceContract<Event> {
  if (!isRecord(value)) {
    return { value: null, presentation: presentation.malformed(['Event payload is not an object.']) };
  }
  const required = requiredStrings(value, [
    { canonical: 'event_id', aliases: ['id'] },
    { canonical: 'version' },
    { canonical: 'run_id' },
    { canonical: 'type', aliases: ['event_type'] },
    { canonical: 'summary' },
  ]);
  if (required.issues.length > 0) {
    return { value: null, presentation: presentation.malformed(required.issues) };
  }
  const priorState = optionalLifecycle(value.prior_state);
  const newState = optionalLifecycle(value.new_state);
  const issues = [priorState.issue, newState.issue].filter((issue): issue is string => issue !== null);
  return {
    value: {
      id: required.values.event_id,
      version: required.values.version,
      runId: required.values.run_id,
      taskId: readString(value, 'task_id'),
      type: required.values.type,
      priorState: priorState.state,
      rawPriorState: priorState.raw,
      newState: newState.state,
      rawNewState: newState.raw,
      occurredAt: optionalTimestamp(value, 'occurred_at') ?? optionalTimestamp(value, 'timestamp'),
      summary: required.values.summary,
      traceId: readString(value, 'trace_id'),
      metrics: adaptOperationalMetrics(value.measured_values ?? value.operational_metrics),
    },
    presentation: presentation.ready(issues),
  };
}

export function adaptEventCollection(value: unknown): CollectionContract<Event> {
  return adaptCollection(value, ['items', 'events', 'results'], 'Events', adaptEvent);
}

export function adaptEvidence(value: unknown): ResourceContract<Evidence> {
  if (!isRecord(value)) {
    return {
      value: null,
      presentation: presentation.malformed(['Evidence payload is not an object.']),
    };
  }
  const required = requiredStrings(value, [
    { canonical: 'evidence_id', aliases: ['id'] },
    { canonical: 'run_id' },
    { canonical: 'source_type' },
    { canonical: 'locator', aliases: ['canonical_locator'] },
  ]);
  if (required.issues.length > 0) {
    return { value: null, presentation: presentation.malformed(required.issues) };
  }
  const availability = parseAvailabilityState(value.availability ?? value.availability_status);
  const issues =
    availability.state === 'unknown'
      ? [unknownStateIssue('availability', availability.raw)]
      : [];
  return {
    value: {
      id: required.values.evidence_id,
      runId: required.values.run_id,
      sourceType: required.values.source_type,
      locator: required.values.locator,
      title: readString(value, 'title'),
      publisher: readString(value, 'publisher') ?? readString(value, 'author'),
      retrievedAt: optionalTimestamp(value, 'retrieved_at'),
      contentHash: readString(value, 'content_hash'),
      fixtureReference: readString(value, 'fixture_reference'),
      excerpt: readString(value, 'excerpt'),
      structuredValue: value.structured_value ?? null,
      sourceTimestamp: optionalTimestamp(value, 'source_timestamp'),
      sourceVersion: readString(value, 'source_version'),
      retrievalTool: readString(value, 'retrieval_tool'),
      retrievalParameters: readRecord(value, 'retrieval_parameters'),
      usageClassification: readString(value, 'usage_classification'),
      availability: availability.state,
      rawAvailability: availability.raw,
    },
    presentation: presentation.ready(issues),
  };
}

export function adaptEvidenceCollection(value: unknown): CollectionContract<Evidence> {
  return adaptCollection(value, ['items', 'evidence', 'results'], 'Evidence', adaptEvidence);
}

export function adaptClaimSupport(value: unknown): ResourceContract<ClaimSupport> {
  if (!isRecord(value)) {
    return {
      value: null,
      presentation: presentation.malformed(['Claim-support payload is not an object.']),
    };
  }
  const required = requiredStrings(value, [
    { canonical: 'claim_id', aliases: ['id'] },
    { canonical: 'claim_text', aliases: ['text'] },
    { canonical: 'artifact_id' },
    { canonical: 'explanation' },
  ]);
  if (required.issues.length > 0) {
    return { value: null, presentation: presentation.malformed(required.issues) };
  }
  const status = parseSupportState(value.support_status ?? value.status);
  const evidenceIds = referenceIds(value.evidence_ids);
  const issues: string[] = [];
  let normalizedStatus = status.state;
  let rawStatus = status.raw;
  if (status.state === 'unknown') {
    issues.push(unknownStateIssue('support status', status.raw));
  }
  if (
    (status.state === 'supported' || status.state === 'partially-supported') &&
    evidenceIds.length === 0
  ) {
    issues.push('A positive support status requires at least one evidence reference.');
    normalizedStatus = 'unknown';
    rawStatus = readString(value, 'support_status') ?? readString(value, 'status');
  }
  return {
    value: {
      claimId: required.values.claim_id,
      claimText: required.values.claim_text,
      artifactId: required.values.artifact_id,
      sectionReference: readString(value, 'section_reference'),
      evidenceIds,
      status: normalizedStatus,
      rawStatus,
      verificationMethod: readString(value, 'verification_method'),
      score: adaptOriginValue<number>(value.score, 'number'),
      explanation: required.values.explanation,
      verifiedAt: optionalTimestamp(value, 'verified_at'),
    },
    presentation: presentation.ready(issues),
  };
}

function parseArtifactValidation(value: unknown): {
  state: ArtifactValidationState;
  raw: string | null;
} {
  if (typeof value !== 'string') return { state: 'unknown', raw: null };
  const normalized = normalizeToken(value);
  const aliases: Record<string, ArtifactValidationState> = {
    valid: 'valid',
    passed: 'valid',
    partial: 'partial',
    invalid: 'invalid',
    failed: 'invalid',
    'not-validated': 'not-validated',
    pending: 'not-validated',
  };
  return { state: aliases[normalized] ?? 'unknown', raw: aliases[normalized] ? null : value };
}

export function adaptArtifact(value: unknown): ResourceContract<Artifact> {
  if (!isRecord(value)) {
    return {
      value: null,
      presentation: presentation.malformed(['Artifact payload is not an object.']),
    };
  }
  const required = requiredStrings(value, [
    { canonical: 'artifact_id', aliases: ['id'] },
    { canonical: 'run_id' },
    { canonical: 'type', aliases: ['artifact_type'] },
    { canonical: 'schema_version' },
    { canonical: 'workflow_id' },
    { canonical: 'workflow_version' },
  ]);
  if (required.issues.length > 0) {
    return { value: null, presentation: presentation.malformed(required.issues) };
  }
  const issues: string[] = [];
  const validation = parseArtifactValidation(value.validation_status ?? value.validation);
  if (validation.state === 'unknown') {
    issues.push(unknownStateIssue('artifact validation', validation.raw));
  }
  const rawClaims = Array.isArray(value.claims) ? value.claims : [];
  if (value.claims !== undefined && !Array.isArray(value.claims)) {
    issues.push('claims is not an array.');
  }
  const claims: ClaimSupport[] = [];
  rawClaims.forEach((claim, index) => {
    const adapted = adaptClaimSupport(claim);
    if (adapted.value !== null) claims.push(adapted.value);
    adapted.presentation.issues.forEach((issue) => issues.push(`Claim ${index + 1}: ${issue}`));
  });
  return {
    value: {
      id: required.values.artifact_id,
      runId: required.values.run_id,
      type: required.values.type,
      schemaVersion: required.values.schema_version,
      workflowId: required.values.workflow_id,
      workflowVersion: required.values.workflow_version,
      generatingTaskId: readString(value, 'generating_task_id'),
      generatingPhase: readString(value, 'generating_phase'),
      createdAt: optionalTimestamp(value, 'created_at'),
      contentHash: readString(value, 'content_hash'),
      evidenceIds: referenceIds(value.evidence_ids),
      validation: validation.state,
      rawValidation: validation.raw,
      content: value.content ?? null,
      claims,
    },
    presentation: presentation.ready(issues),
  };
}

export function adaptArtifactCollection(value: unknown): CollectionContract<Artifact> {
  return adaptCollection(value, ['items', 'artifacts', 'results'], 'Artifacts', adaptArtifact);
}

function parseEvaluationState(value: unknown): {
  state: EvaluationState;
  raw: string | null;
} {
  if (typeof value !== 'string') return { state: 'unknown', raw: null };
  const normalized = normalizeToken(value);
  const aliases: Record<string, EvaluationState> = {
    passed: 'passed',
    pass: 'passed',
    warning: 'warning',
    failed: 'failed',
    fail: 'failed',
    'not-run': 'not-run',
    skipped: 'not-run',
  };
  return { state: aliases[normalized] ?? 'unknown', raw: aliases[normalized] ? null : value };
}

function parseEvaluationSeverity(value: unknown): {
  severity: EvaluationSeverity;
  raw: string | null;
} {
  if (typeof value !== 'string') return { severity: 'unknown', raw: null };
  const normalized = normalizeToken(value);
  const aliases: Record<string, EvaluationSeverity> = {
    info: 'info',
    informational: 'info',
    warning: 'warning',
    error: 'error',
    critical: 'error',
  };
  return {
    severity: aliases[normalized] ?? 'unknown',
    raw: aliases[normalized] ? null : value,
  };
}

function adaptEvaluationMetrics(value: unknown): Record<string, OriginValue<number>> {
  if (!isRecord(value)) return {};
  const metrics: Record<string, OriginValue<number>> = {};
  for (const [key, metric] of Object.entries(value)) {
    metrics[key] = adaptOriginValue<number>(metric, 'number');
  }
  return metrics;
}

export function adaptEvaluation(value: unknown): ResourceContract<Evaluation> {
  if (!isRecord(value)) {
    return {
      value: null,
      presentation: presentation.malformed(['Evaluation payload is not an object.']),
    };
  }
  const required = requiredStrings(value, [
    { canonical: 'evaluation_id', aliases: ['id'] },
    { canonical: 'run_id' },
    { canonical: 'evaluator_id' },
    { canonical: 'evaluator_version' },
    { canonical: 'explanation' },
  ]);
  if (required.issues.length > 0) {
    return { value: null, presentation: presentation.malformed(required.issues) };
  }
  const status = parseEvaluationState(value.status);
  const severity = parseEvaluationSeverity(value.severity);
  const rawMethod = readString(value, 'method');
  const normalizedMethod = rawMethod === null ? null : normalizeToken(rawMethod);
  const method =
    normalizedMethod === 'deterministic' ||
    normalizedMethod === 'model-assisted' ||
    normalizedMethod === 'human'
      ? normalizedMethod
      : 'unknown';
  const issues: string[] = [];
  if (status.state === 'unknown') issues.push(unknownStateIssue('evaluation status', status.raw));
  if (severity.severity === 'unknown') {
    issues.push(unknownStateIssue('evaluation severity', severity.raw));
  }
  if (method === 'unknown') issues.push(unknownStateIssue('evaluation method', rawMethod));
  return {
    value: {
      id: required.values.evaluation_id,
      runId: required.values.run_id,
      evaluatorId: required.values.evaluator_id,
      evaluatorVersion: required.values.evaluator_version,
      method,
      rawMethod: method === 'unknown' ? rawMethod : null,
      status: status.state,
      rawStatus: status.raw,
      severity: severity.severity,
      rawSeverity: severity.raw,
      explanation: required.values.explanation,
      artifactIds: referenceIds(value.artifact_ids),
      evidenceIds: referenceIds(value.evidence_ids),
      metrics: adaptEvaluationMetrics(value.metrics),
      evaluatedAt: optionalTimestamp(value, 'evaluated_at'),
    },
    presentation: presentation.ready(issues),
  };
}

export function adaptEvaluationCollection(value: unknown): CollectionContract<Evaluation> {
  return adaptCollection(
    value,
    ['items', 'evaluations', 'results'],
    'Evaluations',
    adaptEvaluation,
  );
}

function runParams(filters: RunListFilters): Record<string, string | number> {
  const params: Record<string, string | number> = {};
  if (filters.workflowId) params.workflow_id = filters.workflowId;
  if (filters.status && filters.status !== 'unknown') params.status = filters.status;
  if (filters.limit !== undefined) params.limit = filters.limit;
  if (filters.cursor) params.cursor = filters.cursor;
  return params;
}

export async function fetchRuns(
  filters: RunListFilters = {},
): Promise<CollectionContract<Run>> {
  const { data } = await apiClient.get<unknown>('/runs', {
    params: runParams(filters),
    handleErrorLocally: true,
  });
  return adaptRunCollection(data);
}

export async function fetchRun(runId: string): Promise<ResourceContract<Run>> {
  const { data } = await apiClient.get<unknown>(`/runs/${encodeURIComponent(runId)}`, {
    handleErrorLocally: true,
  });
  return adaptRun(data);
}

export async function createRun(input: CreateRunInput): Promise<ResourceContract<Run>> {
  const payload = {
    workflow_id: input.workflowId,
    workflow_version: input.workflowVersion,
    objective: input.objective,
    inputs: input.inputs,
    mode: input.mode,
    budget: input.budget
      ? {
          max_duration_seconds: input.budget.maxDurationSeconds,
          max_cost: input.budget.maxCost,
          currency: input.budget.currency,
        }
      : undefined,
    provider_policy_id: input.providerPolicyId,
  };
  const { data } = await apiClient.post<unknown>('/runs', payload, {
    handleErrorLocally: true,
  });
  return adaptRun(data);
}

export async function cancelRun(runId: string): Promise<ResourceContract<Run>> {
  const { data } = await apiClient.post<unknown>(
    `/runs/${encodeURIComponent(runId)}/cancel`,
    undefined,
    { handleErrorLocally: true },
  );
  const contract = adaptRun(data);
  if (contract.value === null) {
    throw new Error('The cancellation response did not include an interpretable run.');
  }
  if (contract.value.id !== runId) {
    throw new Error('The cancellation response identified a different run.');
  }
  return contract;
}

async function fetchRunCollection<T>(
  runId: string,
  resource: string,
  adapter: (value: unknown) => CollectionContract<T>,
): Promise<CollectionContract<T>> {
  const { data } = await apiClient.get<unknown>(
    `/runs/${encodeURIComponent(runId)}/${resource}`,
    { handleErrorLocally: true },
  );
  return adapter(data);
}

export const fetchRunTasks = (runId: string) =>
  fetchRunCollection(runId, 'tasks', adaptTaskCollection);
export const fetchRunEvents = (runId: string) =>
  fetchRunCollection(runId, 'events', adaptEventCollection);
export const fetchRunEvidence = (runId: string) =>
  fetchRunCollection(runId, 'evidence', adaptEvidenceCollection);
export const fetchRunArtifacts = (runId: string) =>
  fetchRunCollection(runId, 'artifacts', adaptArtifactCollection);
export const fetchRunEvaluations = (runId: string) =>
  fetchRunCollection(runId, 'evaluations', adaptEvaluationCollection);

export function runRefetchInterval(
  contract: ResourceContract<Run> | undefined,
  failureCount: number,
): number | false {
  if (failureCount >= MAX_POLL_FAILURES || contract?.value === null || !contract?.value) {
    return false;
  }
  const status = contract.value.status;
  return status === 'pending' || status === 'running' ? ACTIVE_POLL_INTERVAL_MS : false;
}

export function subresourceRefetchInterval(
  runStatus: LifecycleState | undefined,
  failureCount: number,
): number | false {
  if (failureCount >= MAX_POLL_FAILURES) return false;
  return runStatus === 'pending' || runStatus === 'running'
    ? ACTIVE_POLL_INTERVAL_MS
    : false;
}

export function useRuns(filters: RunListFilters = {}) {
  return useQuery({
    queryKey: runKeys.list(filters),
    queryFn: () => fetchRuns(filters),
    staleTime: 10_000,
    retry: 2,
  });
}

export function useRun(runId: string) {
  return useQuery({
    queryKey: runKeys.detail(runId),
    queryFn: () => fetchRun(runId),
    enabled: runId.length > 0,
    staleTime: 1_000,
    retry: MAX_POLL_FAILURES - 1,
    refetchInterval: (query) =>
      runRefetchInterval(query.state.data, query.state.fetchFailureCount),
    refetchIntervalInBackground: false,
  });
}

export function useCreateRun() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: createRun,
    onSuccess: (contract) => {
      void queryClient.invalidateQueries({ queryKey: runKeys.all });
      if (contract.value) queryClient.setQueryData(runKeys.detail(contract.value.id), contract);
    },
  });
}

export function useCancelRun() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: cancelRun,
    onSuccess: (contract, runId) => {
      if (contract.value) {
        queryClient.setQueryData(runKeys.detail(runId), contract);
        queryClient.setQueryData<CollectionContract<Run>>(
          runKeys.list(),
          (current) =>
            current
              ? {
                  ...current,
                  items: current.items.map((run) =>
                    run.id === runId ? contract.value! : run,
                  ),
                }
              : current,
        );
      }
      void queryClient.invalidateQueries({ queryKey: runKeys.all });
    },
  });
}

function useRunSubresource<T>(
  runId: string,
  queryKey: readonly unknown[],
  queryFn: (id: string) => Promise<CollectionContract<T>>,
  options: RunSubresourceOptions,
) {
  return useQuery({
    queryKey,
    queryFn: () => queryFn(runId),
    enabled: runId.length > 0,
    staleTime: 2_000,
    retry: 2,
    refetchInterval: (query) =>
      subresourceRefetchInterval(options.runStatus, query.state.fetchFailureCount),
    refetchIntervalInBackground: false,
  });
}

export const useRunTasks = (runId: string, options: RunSubresourceOptions = {}) =>
  useRunSubresource(runId, runKeys.tasks(runId), fetchRunTasks, options);
export const useRunEvents = (runId: string, options: RunSubresourceOptions = {}) =>
  useRunSubresource(runId, runKeys.events(runId), fetchRunEvents, options);
export const useRunEvidence = (runId: string, options: RunSubresourceOptions = {}) =>
  useRunSubresource(runId, runKeys.evidence(runId), fetchRunEvidence, options);
export const useRunArtifacts = (runId: string, options: RunSubresourceOptions = {}) =>
  useRunSubresource(runId, runKeys.artifacts(runId), fetchRunArtifacts, options);
export const useRunEvaluations = (runId: string, options: RunSubresourceOptions = {}) =>
  useRunSubresource(runId, runKeys.evaluations(runId), fetchRunEvaluations, options);
