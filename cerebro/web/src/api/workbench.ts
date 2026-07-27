export type ValueOrigin = 'measured' | 'fixture-derived' | 'unavailable';

export interface OriginValue<T> {
  value: T | null;
  origin: ValueOrigin;
  unit: string | null;
}

export type LifecycleState =
  | 'pending'
  | 'running'
  | 'completed-with-warnings'
  | 'failed'
  | 'cancelled'
  | 'completed'
  | 'unknown';

export type SupportState =
  | 'supported'
  | 'partially-supported'
  | 'disputed'
  | 'unsupported'
  | 'unknown';

export type AvailabilityState =
  | 'available'
  | 'partial'
  | 'unavailable'
  | 'malformed'
  | 'unknown';

export type EvaluationState = 'passed' | 'warning' | 'failed' | 'not-run' | 'unknown';
export type EvaluationSeverity = 'info' | 'warning' | 'error' | 'unknown';
export type ArtifactValidationState =
  | 'valid'
  | 'partial'
  | 'invalid'
  | 'not-validated'
  | 'unknown';
export type ExecutionMode = 'fixture' | 'live' | 'unknown';
export type WorkflowMaturity = 'experimental' | 'preview' | 'stable' | 'unknown';

export type SemanticTone =
  | 'neutral'
  | 'informative'
  | 'positive'
  | 'caution'
  | 'critical'
  | 'muted';

export type SemanticIconKey =
  | 'clock'
  | 'activity'
  | 'check-warning'
  | 'check'
  | 'x'
  | 'ban'
  | 'help'
  | 'link'
  | 'link-partial'
  | 'unlink'
  | 'alert'
  | 'database'
  | 'database-off'
  | 'file-warning';

export interface StateSemantics {
  label: string;
  description: string;
  tone: SemanticTone;
  iconKey: SemanticIconKey;
  isSuccess: boolean;
  isTerminal: boolean;
}

export const lifecycleSemantics: Record<LifecycleState, StateSemantics> = {
  pending: {
    label: 'Pending',
    description: 'The work is queued and has not started.',
    tone: 'neutral',
    iconKey: 'clock',
    isSuccess: false,
    isTerminal: false,
  },
  running: {
    label: 'Running',
    description: 'The work is currently in progress.',
    tone: 'informative',
    iconKey: 'activity',
    isSuccess: false,
    isTerminal: false,
  },
  'completed-with-warnings': {
    label: 'Completed with warnings',
    description: 'The work completed, but degraded or incomplete results require review.',
    tone: 'caution',
    iconKey: 'check-warning',
    isSuccess: false,
    isTerminal: true,
  },
  failed: {
    label: 'Failed',
    description: 'The work stopped before successful completion.',
    tone: 'critical',
    iconKey: 'x',
    isSuccess: false,
    isTerminal: true,
  },
  cancelled: {
    label: 'Cancelled',
    description: 'Cancellation was requested before completion.',
    tone: 'muted',
    iconKey: 'ban',
    isSuccess: false,
    isTerminal: true,
  },
  completed: {
    label: 'Completed',
    description: 'The work completed without reported warnings.',
    tone: 'positive',
    iconKey: 'check',
    isSuccess: true,
    isTerminal: true,
  },
  unknown: {
    label: 'Unknown state',
    description: 'The service returned a lifecycle state this client does not recognize.',
    tone: 'caution',
    iconKey: 'help',
    isSuccess: false,
    isTerminal: false,
  },
};

export const supportSemantics: Record<SupportState, StateSemantics> = {
  supported: {
    label: 'Supported',
    description: 'The cited evidence supports this claim.',
    tone: 'positive',
    iconKey: 'link',
    isSuccess: true,
    isTerminal: true,
  },
  'partially-supported': {
    label: 'Partially supported',
    description: 'The evidence supports only part of this claim.',
    tone: 'caution',
    iconKey: 'link-partial',
    isSuccess: false,
    isTerminal: true,
  },
  disputed: {
    label: 'Disputed',
    description: 'Available evidence conflicts with this claim.',
    tone: 'critical',
    iconKey: 'alert',
    isSuccess: false,
    isTerminal: true,
  },
  unsupported: {
    label: 'Unsupported',
    description: 'No sufficient supporting evidence is attached to this claim.',
    tone: 'critical',
    iconKey: 'unlink',
    isSuccess: false,
    isTerminal: true,
  },
  unknown: {
    label: 'Unknown support',
    description: 'The service returned a support state this client does not recognize.',
    tone: 'caution',
    iconKey: 'help',
    isSuccess: false,
    isTerminal: false,
  },
};

export const availabilitySemantics: Record<AvailabilityState, StateSemantics> = {
  available: {
    label: 'Available',
    description: 'The requested data is available.',
    tone: 'positive',
    iconKey: 'database',
    isSuccess: true,
    isTerminal: true,
  },
  partial: {
    label: 'Partially available',
    description: 'Some requested fields or records are unavailable.',
    tone: 'caution',
    iconKey: 'file-warning',
    isSuccess: false,
    isTerminal: true,
  },
  unavailable: {
    label: 'Unavailable',
    description: 'The requested data is not available.',
    tone: 'muted',
    iconKey: 'database-off',
    isSuccess: false,
    isTerminal: true,
  },
  malformed: {
    label: 'Malformed response',
    description: 'The service response could not be interpreted safely.',
    tone: 'critical',
    iconKey: 'file-warning',
    isSuccess: false,
    isTerminal: true,
  },
  unknown: {
    label: 'Unknown availability',
    description: 'The service returned an availability state this client does not recognize.',
    tone: 'caution',
    iconKey: 'help',
    isSuccess: false,
    isTerminal: false,
  },
};

export type PresentationState =
  | 'loading'
  | 'ready'
  | 'empty'
  | 'unavailable'
  | 'malformed'
  | 'partial';

export interface PresentationContract {
  state: PresentationState;
  message: string | null;
  retryable: boolean;
  issues: readonly string[];
}

export interface ResourceContract<T> {
  value: T | null;
  presentation: PresentationContract;
}

export interface CollectionContract<T> {
  items: readonly T[];
  presentation: PresentationContract;
}

export const presentation = {
  loading(message = 'Loading data.'): PresentationContract {
    return { state: 'loading', message, retryable: false, issues: [] };
  },
  ready(issues: readonly string[] = []): PresentationContract {
    return {
      state: issues.length > 0 ? 'partial' : 'ready',
      message: issues.length > 0 ? 'Some fields could not be interpreted.' : null,
      retryable: false,
      issues,
    };
  },
  empty(message = 'No records are available.'): PresentationContract {
    return { state: 'empty', message, retryable: false, issues: [] };
  },
  unavailable(message = 'This resource is unavailable.'): PresentationContract {
    return { state: 'unavailable', message, retryable: true, issues: [] };
  },
  malformed(issues: readonly string[]): PresentationContract {
    return {
      state: 'malformed',
      message: 'The service returned data in an unexpected format.',
      retryable: true,
      issues,
    };
  },
};

export interface OperationalMetrics {
  durationMs: OriginValue<number>;
  latencyMs: OriginValue<number>;
  cost: OriginValue<number>;
  inputTokens: OriginValue<number>;
  outputTokens: OriginValue<number>;
  totalTokens: OriginValue<number>;
  provider: OriginValue<string>;
  model: OriginValue<string>;
  promptId: OriginValue<string>;
  promptVersion: OriginValue<string>;
}

export interface Workflow {
  id: string;
  version: string;
  name: string;
  description: string;
  inputSchema: Readonly<Record<string, unknown>>;
  phases: readonly string[];
  requiredTools: readonly string[];
  providerPolicy: Readonly<Record<string, unknown>> | null;
  evidencePolicy: Readonly<Record<string, unknown>> | null;
  artifactSchemas: readonly string[];
  evaluators: readonly string[];
  maturity: WorkflowMaturity;
  rawMaturity: string | null;
  supportedModes: readonly ExecutionMode[];
  limitations: readonly string[];
  typicalMetrics: OperationalMetrics;
}

export interface Run {
  id: string;
  workflowId: string;
  workflowVersion: string;
  objective: string;
  inputs: Readonly<Record<string, unknown>>;
  mode: ExecutionMode;
  rawMode: string | null;
  status: LifecycleState;
  rawStatus: string | null;
  createdAt: string | null;
  startedAt: string | null;
  updatedAt: string | null;
  completedAt: string | null;
  providerPolicySnapshot: Readonly<Record<string, unknown>> | null;
  taskIds: readonly string[];
  taskReferencesReported: boolean;
  artifactIds: readonly string[];
  artifactReferencesReported: boolean;
  evidenceIds: readonly string[];
  evidenceReferencesReported: boolean;
  evaluationIds: readonly string[];
  evaluationReferencesReported: boolean;
  metrics: OperationalMetrics;
  warnings: readonly string[];
  failureSummary: string | null;
}

export interface ToolInvocation {
  id: string;
  tool: string;
  status: LifecycleState;
  rawStatus: string | null;
  latencyMs: OriginValue<number>;
  inputSummary: string | null;
  resultSummary: string | null;
}

export interface Task {
  id: string;
  runId: string;
  phase: string;
  purpose: string;
  dependencyIds: readonly string[];
  capability: string | null;
  status: LifecycleState;
  rawStatus: string | null;
  attempt: number | null;
  startedAt: string | null;
  completedAt: string | null;
  metrics: OperationalMetrics;
  toolInvocations: readonly ToolInvocation[];
  outputSummary: string | null;
  degradationReason: string | null;
  errorSummary: string | null;
  parentTaskId: string | null;
  childTaskIds: readonly string[];
}

export interface Event {
  id: string;
  version: string;
  runId: string;
  taskId: string | null;
  type: string;
  priorState: LifecycleState | null;
  rawPriorState: string | null;
  newState: LifecycleState | null;
  rawNewState: string | null;
  occurredAt: string | null;
  summary: string;
  traceId: string | null;
  metrics: OperationalMetrics;
}

export interface Evidence {
  id: string;
  runId: string;
  sourceType: string;
  locator: string;
  title: string | null;
  publisher: string | null;
  retrievedAt: string | null;
  contentHash: string | null;
  fixtureReference: string | null;
  excerpt: string | null;
  structuredValue: unknown | null;
  sourceTimestamp: string | null;
  sourceVersion: string | null;
  retrievalTool: string | null;
  retrievalParameters: Readonly<Record<string, unknown>> | null;
  usageClassification: string | null;
  availability: AvailabilityState;
  rawAvailability: string | null;
}

export interface ClaimSupport {
  claimId: string;
  claimText: string;
  artifactId: string;
  sectionReference: string | null;
  evidenceIds: readonly string[];
  status: SupportState;
  rawStatus: string | null;
  verificationMethod: string | null;
  score: OriginValue<number>;
  explanation: string;
  verifiedAt: string | null;
}

export interface Artifact {
  id: string;
  runId: string;
  type: string;
  schemaVersion: string;
  workflowId: string;
  workflowVersion: string;
  generatingTaskId: string | null;
  generatingPhase: string | null;
  createdAt: string | null;
  contentHash: string | null;
  evidenceIds: readonly string[];
  validation: ArtifactValidationState;
  rawValidation: string | null;
  content: unknown | null;
  claims: readonly ClaimSupport[];
}

export interface Evaluation {
  id: string;
  runId: string;
  evaluatorId: string;
  evaluatorVersion: string;
  method: 'deterministic' | 'model-assisted' | 'human' | 'unknown';
  rawMethod: string | null;
  status: EvaluationState;
  rawStatus: string | null;
  severity: EvaluationSeverity;
  rawSeverity: string | null;
  explanation: string;
  artifactIds: readonly string[];
  evidenceIds: readonly string[];
  metrics: Readonly<Record<string, OriginValue<number>>>;
  evaluatedAt: string | null;
}

export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

export function readString(record: Record<string, unknown>, key: string): string | null {
  const value = record[key];
  return typeof value === 'string' && value.trim().length > 0 ? value : null;
}

export function readNumber(record: Record<string, unknown>, key: string): number | null {
  const value = record[key];
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

export function readStringArray(record: Record<string, unknown>, key: string): string[] {
  const value = record[key];
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : [];
}

export function readRecord(
  record: Record<string, unknown>,
  key: string,
): Record<string, unknown> | null {
  const value = record[key];
  return isRecord(value) ? value : null;
}

export function normalizeToken(value: string): string {
  return value.trim().toLowerCase().replaceAll('_', '-').replaceAll(' ', '-');
}

export function parseLifecycleState(value: unknown): {
  state: LifecycleState;
  raw: string | null;
} {
  if (typeof value !== 'string') return { state: 'unknown', raw: null };
  const normalized = normalizeToken(value);
  const aliases: Record<string, LifecycleState> = {
    queued: 'pending',
    pending: 'pending',
    'in-progress': 'running',
    retrying: 'running',
    running: 'running',
    'completed-with-warnings': 'completed-with-warnings',
    failed: 'failed',
    cancelled: 'cancelled',
    completed: 'completed',
  };
  return { state: aliases[normalized] ?? 'unknown', raw: aliases[normalized] ? null : value };
}

export function parseSupportState(value: unknown): {
  state: SupportState;
  raw: string | null;
} {
  if (typeof value !== 'string') return { state: 'unknown', raw: null };
  const normalized = normalizeToken(value);
  const aliases: Record<string, SupportState> = {
    supported: 'supported',
    'partially-supported': 'partially-supported',
    disputed: 'disputed',
    unsupported: 'unsupported',
  };
  return { state: aliases[normalized] ?? 'unknown', raw: aliases[normalized] ? null : value };
}

export function parseAvailabilityState(value: unknown): {
  state: AvailabilityState;
  raw: string | null;
} {
  if (typeof value !== 'string') return { state: 'unknown', raw: null };
  const normalized = normalizeToken(value);
  const aliases: Record<string, AvailabilityState> = {
    available: 'available',
    partial: 'partial',
    unavailable: 'unavailable',
    malformed: 'malformed',
  };
  return { state: aliases[normalized] ?? 'unknown', raw: aliases[normalized] ? null : value };
}

export function unavailableMetric<T>(unit: string | null = null): OriginValue<T> {
  return { value: null, origin: 'unavailable', unit };
}

export function adaptOriginValue<T extends string | number>(
  value: unknown,
  expectedType: 'string' | 'number',
  defaultUnit: string | null = null,
): OriginValue<T> {
  if (!isRecord(value)) return unavailableMetric<T>(defaultUnit);
  const rawOrigin = readString(value, 'origin');
  const origin = rawOrigin === null ? null : normalizeToken(rawOrigin);
  const rawValue = value.value;
  const unit = readString(value, 'unit') ?? defaultUnit;
  if (origin === 'unavailable' || rawValue === null || rawValue === undefined) {
    return unavailableMetric<T>(unit);
  }
  if (origin !== 'measured' && origin !== 'fixture-derived') {
    return unavailableMetric<T>(unit);
  }
  if (typeof rawValue !== expectedType) return unavailableMetric<T>(unit);
  if (expectedType === 'string' && typeof rawValue === 'string' && rawValue.trim().length === 0) {
    return unavailableMetric<T>(unit);
  }
  if (expectedType === 'number' && !Number.isFinite(rawValue)) {
    return unavailableMetric<T>(unit);
  }
  return { value: rawValue as T, origin, unit };
}

export function adaptOperationalMetrics(value: unknown): OperationalMetrics {
  const record = isRecord(value) ? value : {};
  return {
    durationMs: adaptOriginValue<number>(record.duration_ms, 'number', 'ms'),
    latencyMs: adaptOriginValue<number>(record.latency_ms, 'number', 'ms'),
    cost: adaptOriginValue<number>(record.cost, 'number'),
    inputTokens: adaptOriginValue<number>(record.input_tokens, 'number', 'tokens'),
    outputTokens: adaptOriginValue<number>(record.output_tokens, 'number', 'tokens'),
    totalTokens: adaptOriginValue<number>(record.total_tokens, 'number', 'tokens'),
    provider: adaptOriginValue<string>(record.provider, 'string'),
    model: adaptOriginValue<string>(record.model, 'string'),
    promptId: adaptOriginValue<string>(record.prompt_id, 'string'),
    promptVersion: adaptOriginValue<string>(record.prompt_version, 'string'),
  };
}

export function listPayload(value: unknown, keys: readonly string[]): unknown[] | null {
  if (Array.isArray(value)) return value;
  if (!isRecord(value)) return null;
  for (const key of keys) {
    if (Array.isArray(value[key])) return value[key];
  }
  return null;
}

export function parseExecutionMode(value: unknown): { mode: ExecutionMode; raw: string | null } {
  if (typeof value !== 'string') return { mode: 'unknown', raw: null };
  const normalized = normalizeToken(value);
  if (normalized === 'fixture' || normalized === 'live') {
    return { mode: normalized, raw: null };
  }
  return { mode: 'unknown', raw: value };
}
