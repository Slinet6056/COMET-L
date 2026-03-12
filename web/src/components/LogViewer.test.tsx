import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { LogViewer } from './LogViewer';
import * as api from '../lib/api';

describe('Log viewer', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it('expands per-stream inline log panels on demand', async () => {
    const user = userEvent.setup();
    vi.spyOn(api, 'fetchRunLogs').mockResolvedValue({
      runId: 'run-logs-1',
      streams: {
        taskIds: ['task-7', 'main'],
        counts: { main: 2, 'task-7': 1 },
        maxEntriesPerStream: 50,
        items: [
          {
            taskId: 'task-7',
            order: 0,
            status: 'completed',
            startedAt: '2026-03-10T10:00:01Z',
            completedAt: '2026-03-10T10:00:03Z',
            endedAt: '2026-03-10T10:00:03Z',
            durationSeconds: 2,
            firstEntryAt: '2026-03-10T10:00:01Z',
            lastEntryAt: '2026-03-10T10:00:03Z',
            bufferedEntryCount: 1,
            totalEntryCount: 1,
          },
          {
            taskId: 'main',
            order: 1,
            status: 'running',
            startedAt: '2026-03-10T10:00:00Z',
            completedAt: null,
            endedAt: null,
            durationSeconds: null,
            firstEntryAt: '2026-03-10T10:00:00Z',
            lastEntryAt: '2026-03-10T10:00:00Z',
            bufferedEntryCount: 2,
            totalEntryCount: 2,
          },
        ],
        byTaskId: {
          'task-7': {
            taskId: 'task-7',
            order: 0,
            status: 'completed',
            startedAt: '2026-03-10T10:00:01Z',
            completedAt: '2026-03-10T10:00:03Z',
            endedAt: '2026-03-10T10:00:03Z',
            durationSeconds: 2,
            firstEntryAt: '2026-03-10T10:00:01Z',
            lastEntryAt: '2026-03-10T10:00:03Z',
            bufferedEntryCount: 1,
            totalEntryCount: 1,
          },
          main: {
            taskId: 'main',
            order: 1,
            status: 'running',
            startedAt: '2026-03-10T10:00:00Z',
            completedAt: null,
            endedAt: null,
            durationSeconds: null,
            firstEntryAt: '2026-03-10T10:00:00Z',
            lastEntryAt: '2026-03-10T10:00:00Z',
            bufferedEntryCount: 2,
            totalEntryCount: 2,
          },
        },
      },
    });
    const fetchRunLogsForTaskSpy = vi
      .spyOn(api, 'fetchRunLogsForTask')
      .mockImplementation(async (_runId, taskId) => ({
        runId: 'run-logs-1',
        taskId,
        availableTaskIds: ['main', 'task-7'],
        maxEntriesPerStream: 50,
        stream: {
          taskId,
          order: taskId === 'main' ? 1 : 0,
          status: taskId === 'main' ? 'running' : 'completed',
          startedAt: taskId === 'main' ? '2026-03-10T10:00:00Z' : '2026-03-10T10:00:01Z',
          completedAt: taskId === 'main' ? null : '2026-03-10T10:00:03Z',
          endedAt: taskId === 'main' ? null : '2026-03-10T10:00:03Z',
          durationSeconds: taskId === 'main' ? null : 2,
          firstEntryAt: taskId === 'main' ? '2026-03-10T10:00:00Z' : '2026-03-10T10:00:01Z',
          lastEntryAt: taskId === 'main' ? '2026-03-10T10:00:00Z' : '2026-03-10T10:00:03Z',
          bufferedEntryCount: taskId === 'main' ? 2 : 1,
          totalEntryCount: taskId === 'main' ? 2 : 1,
        },
        entries:
          taskId === 'main'
            ? [
                {
                  sequence: 1,
                  timestamp: '2026-03-10T10:00:00Z',
                  taskId: 'main',
                  logger: 'comet.main',
                  level: 'INFO',
                  message: 'main log line',
                },
              ]
            : [
                {
                  sequence: 2,
                  timestamp: '2026-03-10T10:00:01Z',
                  taskId: 'task-7',
                  logger: 'comet.worker',
                  level: 'INFO',
                  message: 'worker log line',
                },
              ],
      }));

    render(<LogViewer runId="run-logs-1" runStatus="running" />);

    expect(await screen.findByText('展开任意流行即可查看其缓冲日志输出。')).toBeInTheDocument();
    expect(screen.queryByText('main log line')).not.toBeInTheDocument();
    expect(screen.queryByText(/^Selected:/)).not.toBeInTheDocument();
    expect(screen.queryByText(/^Status:/)).not.toBeInTheDocument();
    expect(screen.queryByText(/^Start:/)).not.toBeInTheDocument();
    expect(screen.queryByText(/^Duration:/)).not.toBeInTheDocument();

    const buttons = screen.getAllByRole('button');
    expect(buttons[0]).toHaveTextContent('main');
    expect(buttons[1]).toHaveTextContent('task-7');

    const workerButton = screen.getByRole('button', { name: /task-7/i });
    const mainButton = screen.getByRole('button', { name: /main/i });

    expect(workerButton).toHaveAttribute('aria-expanded', 'false');
    expect(mainButton).toHaveAttribute('aria-expanded', 'false');

    await user.click(mainButton);

    expect(await screen.findByText('main log line')).toBeInTheDocument();
    expect(mainButton).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByRole('log', { name: 'main 的日志条目' })).toBeInTheDocument();

    await user.click(workerButton);

    expect(await screen.findByText('worker log line')).toBeInTheDocument();
    expect(screen.getByText('main log line')).toBeInTheDocument();
    expect(workerButton).toHaveAttribute('aria-expanded', 'true');
    expect(fetchRunLogsForTaskSpy).toHaveBeenNthCalledWith(1, 'run-logs-1', 'main');
    expect(fetchRunLogsForTaskSpy).toHaveBeenNthCalledWith(2, 'run-logs-1', 'task-7');
  });

  it('gracefully degrades when only the main stream exists and it is empty', async () => {
    const user = userEvent.setup();
    vi.spyOn(api, 'fetchRunLogs').mockResolvedValue({
      runId: 'run-logs-2',
      streams: {
        taskIds: ['main'],
        counts: { main: 0 },
        maxEntriesPerStream: 200,
        items: [
          {
            taskId: 'main',
            order: 0,
            status: 'completed',
            startedAt: '2026-03-10T10:00:00Z',
            completedAt: '2026-03-10T10:00:05Z',
            endedAt: '2026-03-10T10:00:05Z',
            durationSeconds: 5,
            firstEntryAt: null,
            lastEntryAt: null,
            bufferedEntryCount: 0,
            totalEntryCount: 0,
          },
        ],
        byTaskId: {
          main: {
            taskId: 'main',
            order: 0,
            status: 'completed',
            startedAt: '2026-03-10T10:00:00Z',
            completedAt: '2026-03-10T10:00:05Z',
            endedAt: '2026-03-10T10:00:05Z',
            durationSeconds: 5,
            firstEntryAt: null,
            lastEntryAt: null,
            bufferedEntryCount: 0,
            totalEntryCount: 0,
          },
        },
      },
    });
    vi.spyOn(api, 'fetchRunLogsForTask').mockResolvedValue({
      runId: 'run-logs-2',
      taskId: 'main',
      availableTaskIds: ['main'],
      maxEntriesPerStream: 200,
      stream: {
        taskId: 'main',
        order: 0,
        status: 'completed',
        startedAt: '2026-03-10T10:00:00Z',
        completedAt: '2026-03-10T10:00:05Z',
        endedAt: '2026-03-10T10:00:05Z',
        durationSeconds: 5,
        firstEntryAt: null,
        lastEntryAt: null,
        bufferedEntryCount: 0,
        totalEntryCount: 0,
      },
      entries: [],
    });

    render(<LogViewer runId="run-logs-2" runStatus="completed" />);

    const mainButton = await screen.findByRole('button', { name: /main/i });
    expect(mainButton).toHaveAttribute('aria-expanded', 'false');
    expect(screen.getByText('未捕获到日志')).toBeInTheDocument();

    await user.click(mainButton);

    await waitFor(() => {
      expect(screen.queryByText('正在加载日志条目...')).not.toBeInTheDocument();
    });
    expect(screen.getByText('未捕获到主协调器日志。')).toBeInTheDocument();
    expect(
      screen.getByText('运行结束时，主协调器流尚未产生可缓冲的日志输出。'),
    ).toBeInTheDocument();
  });

  it('discovers new worker streams while the run is still active', async () => {
    let summaryCalls = 0;

    vi.spyOn(api, 'fetchRunLogs').mockImplementation(async () => {
      summaryCalls += 1;
      return {
        runId: 'run-logs-3',
        streams:
          summaryCalls === 1
            ? {
                taskIds: ['main'],
                counts: { main: 1, 'task-9': 0 },
                maxEntriesPerStream: 50,
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
                  'task-9': {
                    taskId: 'task-9',
                    order: 1,
                    status: 'pending',
                    startedAt: null,
                    completedAt: null,
                    endedAt: null,
                    durationSeconds: null,
                    firstEntryAt: null,
                    lastEntryAt: null,
                    bufferedEntryCount: 0,
                    totalEntryCount: 0,
                  },
                },
              }
            : {
                taskIds: ['main', 'task-9'],
                counts: { main: 1, 'task-9': 1 },
                maxEntriesPerStream: 50,
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
                    taskId: 'task-9',
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
                  'task-9': {
                    taskId: 'task-9',
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
      };
    });
    vi.spyOn(api, 'fetchRunLogsForTask').mockImplementation(async (_runId, taskId) => ({
      runId: 'run-logs-3',
      taskId,
      availableTaskIds: taskId === 'main' ? ['main'] : ['main', 'task-9'],
      maxEntriesPerStream: 50,
      stream: {
        taskId,
        order: taskId === 'main' ? 0 : 1,
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
      entries: [
        {
          sequence: taskId === 'main' ? 1 : 2,
          timestamp: '2026-03-10T10:00:00Z',
          taskId,
          logger: 'comet.worker',
          level: 'INFO',
          message: `${taskId} log line`,
        },
      ],
    }));

    render(<LogViewer runId="run-logs-3" runStatus="running" />);

    expect(await screen.findByRole('button', { name: /main/i })).toBeInTheDocument();
    expect(screen.queryByText('main log line')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /task-9/i })).not.toBeInTheDocument();

    await act(async () => {
      await new Promise((resolve) => window.setTimeout(resolve, 1100));
    });

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /task-9/i })).toBeInTheDocument();
    });
  });

  it('normalizes ended failed runs so stale worker streams do not look live', async () => {
    const user = userEvent.setup();

    vi.spyOn(api, 'fetchRunLogs').mockResolvedValue({
      runId: 'run-logs-4',
      streams: {
        taskIds: ['task-4'],
        counts: { 'task-4': 0 },
        maxEntriesPerStream: 50,
        items: [
          {
            taskId: 'task-4',
            order: 0,
            status: 'running',
            startedAt: '2026-03-10T10:00:01Z',
            completedAt: null,
            endedAt: null,
            durationSeconds: null,
            firstEntryAt: null,
            lastEntryAt: null,
            bufferedEntryCount: 0,
            totalEntryCount: 0,
          },
        ],
        byTaskId: {
          'task-4': {
            taskId: 'task-4',
            order: 0,
            status: 'running',
            startedAt: '2026-03-10T10:00:01Z',
            completedAt: null,
            endedAt: null,
            durationSeconds: null,
            firstEntryAt: null,
            lastEntryAt: null,
            bufferedEntryCount: 0,
            totalEntryCount: 0,
          },
        },
      },
    });
    vi.spyOn(api, 'fetchRunLogsForTask').mockResolvedValue({
      runId: 'run-logs-4',
      taskId: 'task-4',
      availableTaskIds: ['task-4'],
      maxEntriesPerStream: 50,
      stream: {
        taskId: 'task-4',
        order: 0,
        status: 'running',
        startedAt: '2026-03-10T10:00:01Z',
        completedAt: null,
        endedAt: null,
        durationSeconds: null,
        firstEntryAt: null,
        lastEntryAt: null,
        bufferedEntryCount: 0,
        totalEntryCount: 0,
      },
      entries: [],
    });

    render(<LogViewer runId="run-logs-4" runStatus="failed" />);

    const workerButton = await screen.findByRole('button', { name: /task-4/i });
    expect(workerButton).toHaveTextContent('失败');
    expect(workerButton).toHaveTextContent('日志前即失败');
    expect(workerButton).toHaveTextContent('已结束');

    await user.click(workerButton);

    expect(await screen.findByText('工作线程在捕获日志前失败。')).toBeInTheDocument();
  });

  it('keeps active streams ahead of finished streams during live runs', async () => {
    vi.spyOn(api, 'fetchRunLogs').mockResolvedValue({
      runId: 'run-logs-5',
      streams: {
        taskIds: ['task-done', 'main', 'task-pending', 'task-failed'],
        counts: { main: 0, 'task-pending': 0, 'task-done': 0, 'task-failed': 0 },
        maxEntriesPerStream: 50,
        items: [
          {
            taskId: 'task-done',
            order: 1,
            status: 'completed',
            startedAt: '2026-03-10T10:00:01Z',
            completedAt: '2026-03-10T10:00:03Z',
            endedAt: '2026-03-10T10:00:03Z',
            durationSeconds: 2,
            firstEntryAt: null,
            lastEntryAt: null,
            bufferedEntryCount: 0,
            totalEntryCount: 0,
          },
          {
            taskId: 'main',
            order: 0,
            status: 'running',
            startedAt: '2026-03-10T10:00:00Z',
            completedAt: null,
            endedAt: null,
            durationSeconds: null,
            firstEntryAt: null,
            lastEntryAt: null,
            bufferedEntryCount: 0,
            totalEntryCount: 0,
          },
          {
            taskId: 'task-pending',
            order: 2,
            status: 'pending',
            startedAt: null,
            completedAt: null,
            endedAt: null,
            durationSeconds: null,
            firstEntryAt: null,
            lastEntryAt: null,
            bufferedEntryCount: 0,
            totalEntryCount: 0,
          },
          {
            taskId: 'task-failed',
            order: 3,
            status: 'failed',
            startedAt: '2026-03-10T10:00:04Z',
            completedAt: null,
            endedAt: '2026-03-10T10:00:05Z',
            durationSeconds: 1,
            firstEntryAt: null,
            lastEntryAt: null,
            bufferedEntryCount: 0,
            totalEntryCount: 0,
          },
        ],
        byTaskId: {
          'task-done': {
            taskId: 'task-done',
            order: 1,
            status: 'completed',
            startedAt: '2026-03-10T10:00:01Z',
            completedAt: '2026-03-10T10:00:03Z',
            endedAt: '2026-03-10T10:00:03Z',
            durationSeconds: 2,
            firstEntryAt: null,
            lastEntryAt: null,
            bufferedEntryCount: 0,
            totalEntryCount: 0,
          },
          main: {
            taskId: 'main',
            order: 0,
            status: 'running',
            startedAt: '2026-03-10T10:00:00Z',
            completedAt: null,
            endedAt: null,
            durationSeconds: null,
            firstEntryAt: null,
            lastEntryAt: null,
            bufferedEntryCount: 0,
            totalEntryCount: 0,
          },
          'task-pending': {
            taskId: 'task-pending',
            order: 2,
            status: 'pending',
            startedAt: null,
            completedAt: null,
            endedAt: null,
            durationSeconds: null,
            firstEntryAt: null,
            lastEntryAt: null,
            bufferedEntryCount: 0,
            totalEntryCount: 0,
          },
          'task-failed': {
            taskId: 'task-failed',
            order: 3,
            status: 'failed',
            startedAt: '2026-03-10T10:00:04Z',
            completedAt: null,
            endedAt: '2026-03-10T10:00:05Z',
            durationSeconds: 1,
            firstEntryAt: null,
            lastEntryAt: null,
            bufferedEntryCount: 0,
            totalEntryCount: 0,
          },
        },
      },
    });

    render(<LogViewer runId="run-logs-5" runStatus="running" />);

    const buttons = await screen.findAllByRole('button');
    expect(buttons[0]).toHaveTextContent('main');
    expect(buttons[1]).toHaveTextContent('task-pending');
    expect(buttons[2]).toHaveTextContent('task-done');
    expect(buttons[3]).toHaveTextContent('task-failed');
  });

  it.each(['completed', 'failed'] as const)(
    'keeps pure appearance order after a %s run ends',
    async (runStatus) => {
      vi.spyOn(api, 'fetchRunLogs').mockResolvedValue({
        runId: `run-logs-${runStatus}`,
        streams: {
          taskIds: ['task-done', 'main', 'task-pending', 'task-failed'],
          counts: { main: 0, 'task-pending': 0, 'task-done': 0, 'task-failed': 0 },
          maxEntriesPerStream: 50,
          items: [
            {
              taskId: 'task-done',
              order: 1,
              status: 'completed',
              startedAt: '2026-03-10T10:00:01Z',
              completedAt: '2026-03-10T10:00:03Z',
              endedAt: '2026-03-10T10:00:03Z',
              durationSeconds: 2,
              firstEntryAt: null,
              lastEntryAt: null,
              bufferedEntryCount: 0,
              totalEntryCount: 0,
            },
            {
              taskId: 'main',
              order: 0,
              status: 'running',
              startedAt: '2026-03-10T10:00:00Z',
              completedAt: null,
              endedAt: null,
              durationSeconds: null,
              firstEntryAt: null,
              lastEntryAt: null,
              bufferedEntryCount: 0,
              totalEntryCount: 0,
            },
            {
              taskId: 'task-pending',
              order: 2,
              status: 'pending',
              startedAt: null,
              completedAt: null,
              endedAt: null,
              durationSeconds: null,
              firstEntryAt: null,
              lastEntryAt: null,
              bufferedEntryCount: 0,
              totalEntryCount: 0,
            },
            {
              taskId: 'task-failed',
              order: 3,
              status: 'failed',
              startedAt: '2026-03-10T10:00:04Z',
              completedAt: null,
              endedAt: '2026-03-10T10:00:05Z',
              durationSeconds: 1,
              firstEntryAt: null,
              lastEntryAt: null,
              bufferedEntryCount: 0,
              totalEntryCount: 0,
            },
          ],
          byTaskId: {
            'task-done': {
              taskId: 'task-done',
              order: 1,
              status: 'completed',
              startedAt: '2026-03-10T10:00:01Z',
              completedAt: '2026-03-10T10:00:03Z',
              endedAt: '2026-03-10T10:00:03Z',
              durationSeconds: 2,
              firstEntryAt: null,
              lastEntryAt: null,
              bufferedEntryCount: 0,
              totalEntryCount: 0,
            },
            main: {
              taskId: 'main',
              order: 0,
              status: 'running',
              startedAt: '2026-03-10T10:00:00Z',
              completedAt: null,
              endedAt: null,
              durationSeconds: null,
              firstEntryAt: null,
              lastEntryAt: null,
              bufferedEntryCount: 0,
              totalEntryCount: 0,
            },
            'task-pending': {
              taskId: 'task-pending',
              order: 2,
              status: 'pending',
              startedAt: null,
              completedAt: null,
              endedAt: null,
              durationSeconds: null,
              firstEntryAt: null,
              lastEntryAt: null,
              bufferedEntryCount: 0,
              totalEntryCount: 0,
            },
            'task-failed': {
              taskId: 'task-failed',
              order: 3,
              status: 'failed',
              startedAt: '2026-03-10T10:00:04Z',
              completedAt: null,
              endedAt: '2026-03-10T10:00:05Z',
              durationSeconds: 1,
              firstEntryAt: null,
              lastEntryAt: null,
              bufferedEntryCount: 0,
              totalEntryCount: 0,
            },
          },
        },
      });

      render(<LogViewer runId={`run-logs-${runStatus}`} runStatus={runStatus} />);

      const buttons = await screen.findAllByRole('button');
      expect(buttons[0]).toHaveTextContent('main');
      expect(buttons[1]).toHaveTextContent('task-done');
      expect(buttons[2]).toHaveTextContent('task-pending');
      expect(buttons[3]).toHaveTextContent('task-failed');
    },
  );
});
