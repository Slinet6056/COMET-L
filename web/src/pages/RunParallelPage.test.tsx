import { act, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { App } from '../App';
import * as api from '../lib/api';

function buildWorkerResult(index: number, overrides: Record<string, unknown> = {}) {
  const targetId = `Calculator.method${index}#sig-${String(index).padStart(3, '0')}`;

  return {
    targetId,
    target_id: targetId,
    className: 'Calculator',
    class_name: 'Calculator',
    methodName: `method${index}`,
    method_name: `method${index}`,
    success: index % 2 === 1,
    error: null,
    testsGenerated: index,
    tests_generated: index,
    mutantsGenerated: index + 1,
    mutants_generated: index + 1,
    mutantsEvaluated: index + 1,
    mutants_evaluated: index + 1,
    mutantsKilled: Math.max(index - 1, 0),
    mutants_killed: Math.max(index - 1, 0),
    localMutationScore: 0.5,
    local_mutation_score: 0.5,
    processingTime: 1.2,
    processing_time: 1.2,
    methodCoverage: 0.4,
    method_coverage: 0.4,
    ...overrides,
  };
}

function buildWorkerCard(index: number, overrides: Record<string, unknown> = {}) {
  const result = buildWorkerResult(index, overrides);

  return {
    targetId: result.targetId,
    className: result.className,
    methodName: result.methodName,
    success: result.success,
    error: result.error,
    testsGenerated: result.testsGenerated,
    mutantsGenerated: result.mutantsGenerated,
    mutantsEvaluated: result.mutantsEvaluated,
    mutantsKilled: result.mutantsKilled,
    localMutationScore: result.localMutationScore,
    processingTime: result.processingTime,
    methodCoverage: result.methodCoverage,
  };
}

function buildBatchResults(total: number) {
  const firstBatchSize = Math.min(total, 8);
  const secondBatchSize = Math.max(total - firstBatchSize, 0);

  return [
    Array.from({ length: firstBatchSize }, (_unused, index) => buildWorkerResult(index + 1)),
    Array.from({ length: secondBatchSize }, (_unused, index) =>
      buildWorkerResult(firstBatchSize + index + 1),
    ),
  ].filter((batch) => batch.length > 0);
}

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
          target_id: 'Calculator.add#sig-aaa111',
          className: 'Calculator',
          class_name: 'Calculator',
          methodName: 'add',
          method_name: 'add',
          success: true,
          testsGenerated: 2,
          tests_generated: 2,
          mutantsGenerated: 3,
          mutants_generated: 3,
          mutantsKilled: 2,
          mutants_killed: 2,
          localMutationScore: 2 / 3,
          local_mutation_score: 2 / 3,
          methodCoverage: 0.42,
          method_coverage: 0.42,
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
    expect(screen.queryByRole('button', { name: '上一页' })).not.toBeInTheDocument();
    expect(screen.getByText('当前批次：2')).toBeInTheDocument();
    expect(screen.getByTitle('Calculator.add#sig-aaa111')).toBeInTheDocument();
    expect(screen.getByText('累计工作线程数')).toBeInTheDocument();
    expect(screen.getByText('42.0%')).toBeInTheDocument();

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
          workerCards: [buildWorkerCard(3)],
          batchResults: [
            [
              buildWorkerResult(1, {
                targetId: 'Calculator.add#sig-aaa111',
                target_id: 'Calculator.add#sig-aaa111',
                className: 'Calculator',
                class_name: 'Calculator',
                methodName: 'add',
                method_name: 'add',
                methodCoverage: 0.42,
                method_coverage: 0.42,
                localMutationScore: 2 / 3,
                local_mutation_score: 2 / 3,
                testsGenerated: 2,
                tests_generated: 2,
                mutantsGenerated: 3,
                mutants_generated: 3,
                mutantsKilled: 2,
                mutants_killed: 2,
              }),
            ],
            [
              buildWorkerResult(3, {
                targetId: 'Calculator.multiply#sig-ccc333',
                target_id: 'Calculator.multiply#sig-ccc333',
                className: 'Calculator',
                class_name: 'Calculator',
                methodName: 'multiply',
                method_name: 'multiply',
                success: false,
                error: 'Timed out while evaluating mutants.',
                testsGenerated: 1,
                tests_generated: 1,
                mutantsGenerated: 4,
                mutants_generated: 4,
                mutantsKilled: 1,
                mutants_killed: 1,
                localMutationScore: 0.25,
                local_mutation_score: 0.25,
                methodCoverage: 0.5,
                method_coverage: 0.5,
              }),
            ],
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
    expect(screen.getByTitle('Calculator.add#sig-aaa111')).toBeInTheDocument();
    expect(screen.getByTitle('Calculator.multiply#sig-ccc333')).toBeInTheDocument();
    expect(screen.getByText('50.0%')).toBeInTheDocument();
    expect(screen.getByText('Timed out while evaluating mutants.')).toBeInTheDocument();
  });

  it('paginates cumulative batch rows and keeps the current page during live snapshot updates', async () => {
    vi.spyOn(api, 'fetchRunSnapshot').mockResolvedValue(
      buildParallelSnapshot({
        batchResults: buildBatchResults(16),
        workerCards: [buildWorkerCard(16)],
      }),
    );

    render(
      <MemoryRouter initialEntries={['/runs/run-par-42']}>
        <App />
      </MemoryRouter>,
    );

    expect(await screen.findByText('第 1 / 2 页')).toBeInTheDocument();
    expect(screen.getByTitle('Calculator.method1#sig-001')).toBeInTheDocument();
    expect(screen.queryByTitle('Calculator.method16#sig-016')).not.toBeInTheDocument();

    await act(async () => {
      screen.getByRole('button', { name: '下一页' }).click();
    });

    expect(screen.getByText('第 2 / 2 页')).toBeInTheDocument();
    expect(screen.getByTitle('Calculator.method16#sig-016')).toBeInTheDocument();
    expect(screen.queryByTitle('Calculator.method1#sig-001')).not.toBeInTheDocument();

    await act(async () => {
      onEvent?.({
        type: 'run.snapshot',
        sequence: 3,
        snapshot: buildParallelSnapshot({
          currentBatch: 3,
          batchResults: buildBatchResults(17),
          workerCards: [buildWorkerCard(17)],
        }),
      });
    });

    expect(await screen.findByText('第 2 / 2 页')).toBeInTheDocument();
    expect(screen.getByTitle('Calculator.method16#sig-016')).toBeInTheDocument();
    expect(screen.getByTitle('Calculator.method17#sig-017')).toBeInTheDocument();
    expect(screen.queryByTitle('Calculator.method1#sig-001')).not.toBeInTheDocument();
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
        batchResults: [
          [
            buildWorkerResult(1, {
              targetId:
                'Calculator.withAnExceptionallyLongTargetIdentifierThatShouldStayInsideTheWorkerCardHeader',
              target_id:
                'Calculator.withAnExceptionallyLongTargetIdentifierThatShouldStayInsideTheWorkerCardHeader',
              className: 'Calculator',
              class_name: 'Calculator',
              methodName:
                'withAnExceptionallyLongTargetIdentifierThatShouldStayInsideTheWorkerCardHeader',
              method_name:
                'withAnExceptionallyLongTargetIdentifierThatShouldStayInsideTheWorkerCardHeader',
              success: true,
              error: null,
              testsGenerated: 2,
              tests_generated: 2,
              mutantsGenerated: 3,
              mutants_generated: 3,
              mutantsKilled: 2,
              mutants_killed: 2,
              localMutationScore: 2 / 3,
              local_mutation_score: 2 / 3,
            }),
          ],
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
