import {
  cancelledRunFixture,
  completedRunFixture,
  failedRunFixture,
  malformedRunFixture,
  partialRunFixture,
  pendingRunFixture,
  runningRunFixture,
  warningRunFixture,
} from './workbench-contract-fixtures';

const pending = {
  ...pendingRunFixture,
  status: 'queued',
  objective: 'Await a controlled source pack before beginning fixture research.',
};

const running = {
  ...runningRunFixture,
  objective: 'Observe reported task changes for an active fixture research run.',
};

const completedWithWarnings = {
  ...warningRunFixture,
  objective: 'Reopen a degraded fixture artifact and inspect its unavailable evidence.',
};

const failed = {
  ...failedRunFixture,
  objective: 'Inspect a fixture run that stopped during validation.',
};

const cancelled = {
  ...cancelledRunFixture,
  objective: 'Review a fixture run cancelled before artifact publication.',
};

const completed = {
  ...completedRunFixture,
  objective: 'Inspect a completed fixture artifact and its linked evaluation.',
};

const partial = {
  ...partialRunFixture,
  objective: 'Reopen a partial fixture record with an unknown future lifecycle state.',
};

export const runsLedgerFixture = [
  pending,
  running,
  completedWithWarnings,
  failed,
  cancelled,
  completed,
  partial,
];

export const malformedRunsLedgerFixture = [
  malformedRunFixture,
  {
    ...completed,
    run_id: 'run-valid-amid-malformed-fixture',
    objective: 'Keep this valid run reopenable when another ledger record is malformed.',
  },
];

export const cancelledPendingRunFixture = {
  ...pending,
  status: 'cancelled',
  completed_at: '2026-01-01T00:00:04Z',
  updated_at: '2026-01-01T00:00:04Z',
};

export const runsContractFixtures = {
  ledger: runsLedgerFixture,
  empty: [],
  malformed: [malformedRunFixture],
  partial: malformedRunsLedgerFixture,
  cancellation: {
    success: cancelledPendingRunFixture,
  },
};
