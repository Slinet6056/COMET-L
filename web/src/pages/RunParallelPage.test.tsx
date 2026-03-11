import { act, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { App } from '../App';
import * as api from '../lib/api';

function buildParallelSnapshot(overrides: Record<string, unknown> = {}) {
  return {
    runId: 'run-par-42',
    status: 'running',
    mode: 'parallel',
    iteration: 4,
    llmCalls: 18,
    budget: 120,
    decisionReasoning: 'Keep the current batch focused on uncovered calculator targets.',
    currentTarget: null,
    previousTarget: null,
    recentImprovements: [],
    improvementSummary: { count: 0, latest: null },
    metrics: {
      mutationScore: 0.52,
      globalMutationScore: 0.67,
      lineCoverage: 0.78,
      branchCoverage: 0.61,
      totalTests: 11,
      totalMutants: 20,
      globalTotalMutants: 25,
      killedMutants: 12,
      globalKilledMutants: 17,
      survivedMutants: 8,
      globalSurvivedMutants: 8,
      currentMethodCoverage: null,
    },
    phase: {
      key: 'running',
      label: 'Running',
    },
    artifacts: {},
    currentBatch: 2,
    parallelStats: {
      total_batches: 2,
      total_workers_spawned: 4,
      total_targets_processed: 3,
      failed_targets_in_parallel: 1,
    },
    activeTargets: [
      {
        targetId: 'Calculator.divide',
        className: 'Calculator',
        methodName: 'divide',
        method_coverage: 0.42,
      },
    ],
    workerCards: [
      {
        targetId: 'Calculator.add',
        className: 'Calculator',
        methodName: 'add',
        success: true,
        error: null,
        testsGenerated: 2,
        mutantsGenerated: 3,
        mutantsEvaluated: 3,
        mutantsKilled: 2,
        localMutationScore: 2 / 3,
        processingTime: 1.4,
      },
    ],
    batchResults: [
      [
        {
          targetId: 'Calculator.add',
          success: true,
        },
      ],
    ],
    ...overrides,
  };
}

describe('Run page parallel mode', () => {
  let onEvent: ((event: api.RunEvent) => void) | null;

  beforeEach(() => {
    onEvent = null;
    vi.restoreAllMocks();
    vi.stubGlobal('EventSource', class {} as unknown as typeof EventSource);
    vi.spyOn(api, 'fetchRunSnapshot').mockResolvedValue(buildParallelSnapshot());
    vi.spyOn(api, 'fetchRunLogs').mockResolvedValue({
      runId: 'run-par-42',
      streams: {
        taskIds: ['main', 'task-1'],
        counts: { main: 1, 'task-1': 1 },
        maxEntriesPerStream: 200,
      },
    });
    vi.spyOn(api, 'fetchRunLogsForTask').mockImplementation(async (_runId, taskId) => ({
      runId: 'run-par-42',
      taskId,
      availableTaskIds: ['main', 'task-1'],
      maxEntriesPerStream: 200,
      entries: [
        {
          sequence: 1,
          timestamp: '2026-03-10T10:00:00Z',
          taskId,
          logger: 'comet.parallel',
          level: 'INFO',
          message: `${taskId} log line`,
        },
      ],
    }));
    vi.spyOn(api, 'subscribeToRunEvents').mockImplementation((_runId, handlers) => {
      onEvent = handlers.onEvent;
      return () => {};
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it('restores the parallel worker view from the snapshot before live updates resume', async () => {
    render(
      <MemoryRouter initialEntries={['/runs/run-par-42']}>
        <App />
      </MemoryRouter>,
    );

    expect(await screen.findByRole('heading', { name: 'Parallel Run Status' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Worker Cards' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Parallel Stats' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Active Targets' })).toBeInTheDocument();
    expect(screen.getByText('Current batch: 2')).toBeInTheDocument();
    expect(screen.getByText('Calculator.add')).toBeInTheDocument();
    expect(screen.getByText('Calculator.divide')).toBeInTheDocument();
    expect(screen.getByText('Total workers spawned')).toBeInTheDocument();
    expect(screen.getByText('Coverage 42.0%')).toBeInTheDocument();

    await waitFor(() => {
      expect(api.subscribeToRunEvents).toHaveBeenCalledWith(
        'run-par-42',
        expect.objectContaining({ onEvent: expect.any(Function) }),
      );
    });
  });

  it('applies SSE updates to the parallel batch and worker cards', async () => {
    render(
      <MemoryRouter initialEntries={['/runs/run-par-42']}>
        <App />
      </MemoryRouter>,
    );

    await screen.findByRole('heading', { name: 'Worker Cards' });

    await act(async () => {
      onEvent?.({
        type: 'run.snapshot',
        sequence: 2,
        snapshot: buildParallelSnapshot({
          currentBatch: 3,
          workerCards: [
            {
              targetId: 'Calculator.multiply',
              className: 'Calculator',
              methodName: 'multiply',
              success: false,
              error: 'Timed out while evaluating mutants.',
              testsGenerated: 1,
              mutantsGenerated: 4,
              mutantsEvaluated: 2,
              mutantsKilled: 1,
              localMutationScore: 0.25,
              processingTime: 2.7,
            },
          ],
          activeTargets: [
            {
              targetId: 'Calculator.multiply',
              className: 'Calculator',
              methodName: 'multiply',
              method_coverage: 0.5,
            },
          ],
        }),
      });
    });

    expect(await screen.findByText('Current batch: 3')).toBeInTheDocument();
    expect(screen.getAllByText('Calculator.multiply')).toHaveLength(2);
    expect(screen.getByText('Timed out while evaluating mutants.')).toBeInTheDocument();
    expect(screen.getByText('Coverage 50.0%')).toBeInTheDocument();
  });
});
