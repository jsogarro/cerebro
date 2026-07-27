import { expect, test } from '@playwright/test';
import {
  adaptArtifact,
  adaptClaimSupport,
  adaptEvidence,
  adaptRun,
  adaptTask,
  runRefetchInterval,
  subresourceRefetchInterval,
} from '../../src/api/runs';
import {
  availableEvidenceFixture,
  malformedRunFixture,
  mixedSupportArtifactFixture,
  partialRunFixture,
  unavailableEvidenceFixture,
  workbenchContractFixtures,
} from './fixtures/workbench-contract-fixtures';

test.describe('workbench transport contracts', () => {
  test('keeps every documented lifecycle state distinct', () => {
    const expectedStates = {
      pending: 'pending',
      running: 'running',
      warning: 'completed-with-warnings',
      failed: 'failed',
      cancelled: 'cancelled',
      completed: 'completed',
    } as const;

    for (const [fixtureName, expectedState] of Object.entries(expectedStates)) {
      const fixture =
        workbenchContractFixtures.runs[
          fixtureName as keyof typeof workbenchContractFixtures.runs
        ];
      const contract = adaptRun(fixture);
      expect(contract.value?.status).toBe(expectedState);
    }
  });

  test('does not promote unavailable or untrusted metrics to measured values', () => {
    const unavailable = adaptRun(workbenchContractFixtures.runs.pending);
    expect(unavailable.value?.metrics.cost).toEqual({
      value: null,
      origin: 'unavailable',
      unit: 'USD',
    });

    const partial = adaptRun(partialRunFixture);
    expect(partial.presentation.state).toBe('partial');
    expect(partial.value?.status).toBe('unknown');
    expect(partial.value?.metrics.cost).toEqual({
      value: null,
      origin: 'unavailable',
      unit: 'USD',
    });
  });

  test('surfaces malformed required fields without inventing a run', () => {
    const contract = adaptRun(malformedRunFixture);
    expect(contract.value).toBeNull();
    expect(contract.presentation.state).toBe('malformed');
  });

  test('keeps source availability separate from mixed claim support', () => {
    const available = adaptEvidence(availableEvidenceFixture);
    const unavailable = adaptEvidence(unavailableEvidenceFixture);
    const artifact = adaptArtifact(mixedSupportArtifactFixture);

    expect(available.value?.availability).toBe('available');
    expect(unavailable.value?.availability).toBe('unavailable');
    expect(artifact.value?.claims.map((claim) => claim.status)).toEqual([
      'supported',
      'partially-supported',
      'disputed',
      'unsupported',
    ]);
  });

  test('does not accept positive claim support without evidence references', () => {
    const contract = adaptClaimSupport({
      claim_id: 'claim-invalid-positive-fixture',
      claim_text: 'An invented token has an assignment.',
      artifact_id: 'artifact-mixed-support-fixture',
      evidence_ids: [''],
      support_status: 'supported',
      explanation: 'This intentionally inconsistent fixture has no evidence.',
    });

    expect(contract.presentation.state).toBe('partial');
    expect(contract.value?.status).toBe('unknown');
    expect(contract.value?.rawStatus).toBe('supported');
  });

  test('marks malformed and unknown nested task data as partial', () => {
    const malformedInvocations = adaptTask({
      task_id: 'task-malformed-invocations-fixture',
      run_id: 'run-running-fixture',
      phase: 'fixture-phase',
      status: 'running',
      tool_invocations: { unexpected: true },
    });
    const unknownInvocationStatus = adaptTask({
      task_id: 'task-unknown-invocation-status-fixture',
      run_id: 'run-running-fixture',
      phase: 'fixture-phase',
      status: 'running',
      tool_invocations: [
        {
          invocation_id: 'invocation-unknown-status-fixture',
          tool: 'fixture-tool',
          status: 'future_state_not_known_to_client',
        },
      ],
    });

    expect(malformedInvocations.presentation.state).toBe('partial');
    expect(unknownInvocationStatus.presentation.state).toBe('partial');
    expect(unknownInvocationStatus.value?.toolInvocations[0]?.status).toBe('unknown');
  });

  test('marks a present non-array claims field as partial', () => {
    const contract = adaptArtifact({
      ...mixedSupportArtifactFixture,
      claims: { unexpected: true },
    });

    expect(contract.presentation.state).toBe('partial');
    expect(contract.value?.claims).toEqual([]);
  });

  test('polls only active, valid run contracts and stops after bounded failures', () => {
    const running = adaptRun(workbenchContractFixtures.runs.running);
    const completed = adaptRun(workbenchContractFixtures.runs.completed);

    expect(runRefetchInterval(running, 0)).toBe(2_000);
    expect(runRefetchInterval(running, 3)).toBe(false);
    expect(runRefetchInterval(completed, 0)).toBe(false);
    expect(runRefetchInterval(adaptRun(partialRunFixture), 0)).toBe(false);
    expect(subresourceRefetchInterval('running', 0)).toBe(2_000);
    expect(subresourceRefetchInterval('running', 3)).toBe(false);
    expect(subresourceRefetchInterval('completed', 0)).toBe(false);
  });
});
