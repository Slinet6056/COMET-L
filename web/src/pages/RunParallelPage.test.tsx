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
        targetId: 'Calculator.divide#sig-bbb222',
        className: 'Calculator',
        methodName: 'divide',
        method_coverage: 0.42,
      },
    ],
    workerCards: [
      {
        targetId: 'Calculator.add#sig-aaa111',
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
        methodCoverage: 0.42,
      },
    ],
    batchResults: [
      [
        {
          targetId: 'Calculator.add#sig-aaa111',
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
        items: [
          {
            taskId: 'main',
            order: 0,
            status: 'running',
            startedAt: '2026-03-10T10:00:00Z',
            completedAt: null,
            endedAt: null,
            durationSeconds: null,
            firstEntryAt: '2026-03-10T10:00:00Z',
            lastEntryAt: '2026-03-10T10:00:00Z',
            bufferedEntryCount: 1,
            totalEntryCount: 1,
          },
          {
            taskId: 'task-1',
            order: 1,
            status: 'running',
            startedAt: '2026-03-10T10:00:01Z',
            completedAt: null,
            endedAt: null,
            durationSeconds: null,
            firstEntryAt: '2026-03-10T10:00:01Z',
            lastEntryAt: '2026-03-10T10:00:01Z',
            bufferedEntryCount: 1,
            totalEntryCount: 1,
          },
        ],
        byTaskId: {
          main: {
            taskId: 'main',
            order: 0,
            status: 'running',
            startedAt: '2026-03-10T10:00:00Z',
            completedAt: null,
            endedAt: null,
            durationSeconds: null,
            firstEntryAt: '2026-03-10T10:00:00Z',
            lastEntryAt: '2026-03-10T10:00:00Z',
            bufferedEntryCount: 1,
            totalEntryCount: 1,
          },
          'task-1': {
            taskId: 'task-1',
            order: 1,
            status: 'running',
            startedAt: '2026-03-10T10:00:01Z',
            completedAt: null,
            endedAt: null,
            durationSeconds: null,
            firstEntryAt: '2026-03-10T10:00:01Z',
            lastEntryAt: '2026-03-10T10:00:01Z',
            bufferedEntryCount: 1,
            totalEntryCount: 1,
          },
        },
      },
    });
    vi.spyOn(api, 'fetchRunLogsForTask').mockImplementation(async (_runId, taskId) => ({
      runId: 'run-par-42',
      taskId,
      availableTaskIds: ['main', 'task-1'],
      maxEntriesPerStream: 200,
      stream: {
        taskId,
        order: taskId === 'main' ? 0 : 1,
        status: 'running',
        startedAt: taskId === 'main' ? '2026-03-10T10:00:00Z' : '2026-03-10T10:00:01Z',
        completedAt: null,
        endedAt: null,
        durationSeconds: null,
        firstEntryAt: taskId === 'main' ? '2026-03-10T10:00:00Z' : '2026-03-10T10:00:01Z',
        lastEntryAt: taskId === 'main' ? '2026-03-10T10:00:00Z' : '2026-03-10T10:00:01Z',
        bufferedEntryCount: 1,
        totalEntryCount: 1,
      },
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

    expect(await screen.findByRole('heading', { name: '并行运行状态' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '核心指标' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '工作线程输出' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '日志查看器' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '批次摘要' })).toBeInTheDocument();
    expect(screen.getByRole('columnheader', { name: '覆盖率' })).toBeInTheDocument();
    expect(screen.queryByRole('columnheader', { name: 'Runtime' })).not.toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: 'Parallel Stats' })).not.toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: 'Active Targets' })).not.toBeInTheDocument();
    expect(screen.getByText('当前批次：2')).toBeInTheDocument();
    expect(screen.getByTitle('Calculator.add#sig-aaa111')).toBeInTheDocument();
    expect(screen.getByTitle('Calculator.divide#sig-bbb222')).toBeInTheDocument();
    expect(screen.getByText('累计工作线程数')).toBeInTheDocument();
    expect(screen.getByText('覆盖率 42.0%')).toBeInTheDocument();

    await waitFor(() => {
      expect(api.subscribeToRunEvents).toHaveBeenCalledWith(
        'run-par-42',
        expect.objectContaining({ onEvent: expect.any(Function) }),
      );
    });
  });

  it('applies SSE updates to the parallel batch and worker rows', async () => {
    render(
      <MemoryRouter initialEntries={['/runs/run-par-42']}>
        <App />
      </MemoryRouter>,
    );

    await screen.findByRole('heading', { name: '工作线程输出' });

    await act(async () => {
      onEvent?.({
        type: 'run.snapshot',
        sequence: 2,
        snapshot: buildParallelSnapshot({
          currentBatch: 3,
          workerCards: [
            {
              targetId: 'Calculator.multiply#sig-ccc333',
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
              methodCoverage: 0.5,
            },
          ],
          activeTargets: [
            {
              targetId: 'Calculator.multiply#sig-ccc333',
              className: 'Calculator',
              methodName: 'multiply',
              method_coverage: 0.5,
            },
          ],
        }),
      });
    });

    expect(await screen.findByText('当前批次：3')).toBeInTheDocument();
    expect(screen.getAllByTitle('Calculator.multiply#sig-ccc333')).toHaveLength(2);
    expect(screen.getByText('覆盖率 50.0%')).toBeInTheDocument();
    expect(screen.getByText('Timed out while evaluating mutants.')).toBeInTheDocument();
  });

  it('keeps long worker target ids available in the dense row layout', async () => {
    vi.spyOn(api, 'fetchRunSnapshot').mockResolvedValue(
      buildParallelSnapshot({
        workerCards: [
          {
            targetId:
              'Calculator.withAnExceptionallyLongTargetIdentifierThatShouldStayInsideTheWorkerCardHeader',
            className: 'Calculator',
            methodName:
              'withAnExceptionallyLongTargetIdentifierThatShouldStayInsideTheWorkerCardHeader',
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
      }),
    );

    render(
      <MemoryRouter initialEntries={['/runs/run-par-42']}>
        <App />
      </MemoryRouter>,
    );

    const workerLabel = await screen.findByTitle(
      'Calculator.withAnExceptionallyLongTargetIdentifierThatShouldStayInsideTheWorkerCardHeader',
    );
    expect(workerLabel).toBeInTheDocument();
  });

  it('hides worker and batch execution detail during preprocessing', async () => {
    vi.spyOn(api, 'fetchRunSnapshot').mockResolvedValue(
      buildParallelSnapshot({
        phase: { key: 'preprocessing', label: 'Preprocessing' },
      }),
    );

    render(
      <MemoryRouter initialEntries={['/runs/run-par-42']}>
        <App />
      </MemoryRouter>,
    );

    expect(await screen.findByRole('heading', { name: '并行预处理' })).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: '批次摘要' })).not.toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: '工作线程输出' })).not.toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '日志查看器' })).toBeInTheDocument();
  });
});
