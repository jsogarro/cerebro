import { useQuery } from '@tanstack/react-query';
import { apiClient } from './client';
import {
  adaptOperationalMetrics,
  isRecord,
  listPayload,
  normalizeToken,
  parseExecutionMode,
  presentation,
  readRecord,
  readString,
  readStringArray,
  type CollectionContract,
  type ExecutionMode,
  type ResourceContract,
  type Workflow,
  type WorkflowMaturity,
} from './workbench';

export interface WorkflowDto {
  workflow_id: unknown;
  version: unknown;
  name: unknown;
  description: unknown;
  input_schema?: unknown;
  phases?: unknown;
  required_tools?: unknown;
  provider_policy?: unknown;
  evidence_policy?: unknown;
  artifact_schemas?: unknown;
  evaluators?: unknown;
  maturity?: unknown;
  supported_modes?: unknown;
  limitations?: unknown;
  typical_metrics?: unknown;
}

export const workflowKeys = {
  all: ['workflows'] as const,
  catalog: () => [...workflowKeys.all, 'catalog'] as const,
  detail: (workflowId: string) => [...workflowKeys.all, 'detail', workflowId] as const,
};

function parseMaturity(value: unknown): {
  maturity: WorkflowMaturity;
  raw: string | null;
} {
  if (typeof value !== 'string') return { maturity: 'unknown', raw: null };
  const normalized = normalizeToken(value);
  if (normalized === 'experimental' || normalized === 'preview' || normalized === 'stable') {
    return { maturity: normalized, raw: null };
  }
  return { maturity: 'unknown', raw: value };
}

function parseModes(value: unknown): {
  modes: ExecutionMode[];
  hasUnknown: boolean;
} {
  if (!Array.isArray(value)) return { modes: [], hasUnknown: value !== undefined };
  const modes: ExecutionMode[] = [];
  let hasUnknown = false;
  for (const item of value) {
    const parsed = parseExecutionMode(item);
    if (parsed.mode === 'unknown') {
      hasUnknown = true;
      continue;
    }
    if (!modes.includes(parsed.mode)) modes.push(parsed.mode);
  }
  return { modes, hasUnknown };
}

function phaseNames(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((phase) => {
    if (typeof phase === 'string') return [phase];
    if (!isRecord(phase)) return [];
    const name = readString(phase, 'name') ?? readString(phase, 'phase');
    return name === null ? [] : [name];
  });
}

function namedReferences(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((item) => {
    if (typeof item === 'string') return [item];
    if (!isRecord(item)) return [];
    const name =
      readString(item, 'id') ??
      readString(item, 'name') ??
      readString(item, 'type') ??
      readString(item, 'schema');
    return name === null ? [] : [name];
  });
}

export function adaptWorkflow(value: unknown): ResourceContract<Workflow> {
  if (!isRecord(value)) {
    return {
      value: null,
      presentation: presentation.malformed(['Workflow payload is not an object.']),
    };
  }

  const issues: string[] = [];
  const id = readString(value, 'workflow_id') ?? readString(value, 'id');
  const version = readString(value, 'version');
  const name = readString(value, 'name');
  const description = readString(value, 'description');
  if (id === null) issues.push('workflow_id is missing or invalid.');
  if (version === null) issues.push('version is missing or invalid.');
  if (name === null) issues.push('name is missing or invalid.');
  if (description === null) issues.push('description is missing or invalid.');

  if (id === null || version === null || name === null || description === null) {
    return { value: null, presentation: presentation.malformed(issues) };
  }

  const maturity = parseMaturity(value.maturity);
  const modes = parseModes(value.supported_modes ?? value.modes);
  const inputSchema = readRecord(value, 'input_schema');
  if (inputSchema === null) issues.push('input_schema is unavailable.');
  if (maturity.maturity === 'unknown') issues.push('maturity is unknown.');
  if (modes.hasUnknown) issues.push('One or more execution modes are unknown.');

  return {
    value: {
      id,
      version,
      name,
      description,
      inputSchema: inputSchema ?? {},
      phases: phaseNames(value.phases),
      requiredTools: namedReferences(value.required_tools),
      providerPolicy: readRecord(value, 'provider_policy'),
      evidencePolicy: readRecord(value, 'evidence_policy'),
      artifactSchemas: namedReferences(value.artifact_schemas),
      evaluators: namedReferences(value.evaluators),
      maturity: maturity.maturity,
      rawMaturity: maturity.raw,
      supportedModes: modes.modes,
      limitations: readStringArray(value, 'limitations'),
      typicalMetrics: adaptOperationalMetrics(value.typical_metrics),
    },
    presentation: presentation.ready(issues),
  };
}

export function adaptWorkflowCollection(value: unknown): CollectionContract<Workflow> {
  const payload = listPayload(value, ['items', 'workflows', 'results']);
  if (payload === null) {
    return {
      items: [],
      presentation: presentation.malformed(['Workflow collection is not an array.']),
    };
  }

  const items: Workflow[] = [];
  const issues: string[] = [];
  payload.forEach((item, index) => {
    const adapted = adaptWorkflow(item);
    if (adapted.value !== null) items.push(adapted.value);
    adapted.presentation.issues.forEach((issue) => issues.push(`Item ${index + 1}: ${issue}`));
  });

  if (payload.length === 0) {
    return { items, presentation: presentation.empty('No runnable workflows are available.') };
  }
  if (items.length === 0) {
    return { items, presentation: presentation.malformed(issues) };
  }
  return { items, presentation: presentation.ready(issues) };
}

export async function fetchWorkflows(): Promise<CollectionContract<Workflow>> {
  const { data } = await apiClient.get<unknown>('/workflows', { handleErrorLocally: true });
  return adaptWorkflowCollection(data);
}

export async function fetchWorkflow(
  workflowId: string,
): Promise<ResourceContract<Workflow>> {
  const { data } = await apiClient.get<unknown>(`/workflows/${encodeURIComponent(workflowId)}`);
  return adaptWorkflow(data);
}

export function useWorkflows() {
  return useQuery({
    queryKey: workflowKeys.catalog(),
    queryFn: fetchWorkflows,
    staleTime: 60_000,
    retry: false,
  });
}

export function useWorkflow(workflowId: string) {
  return useQuery({
    queryKey: workflowKeys.detail(workflowId),
    queryFn: () => fetchWorkflow(workflowId),
    enabled: workflowId.length > 0,
    staleTime: 60_000,
    retry: 2,
  });
}
