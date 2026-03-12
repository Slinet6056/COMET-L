import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { LogViewer } from './LogViewer';
import * as api from '../lib/api';

function buildSummary(runId: string) {
  return {
    runId,
    streams: {
      taskIds: ['main'],
      counts: { main: 2 },
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
          lastEntryAt: '2026-03-10T10:00:01Z',
          bufferedEntryCount: 2,
          totalEntryCount: 2,
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
          lastEntryAt: '2026-03-10T10:00:01Z',
          bufferedEntryCount: 2,
          totalEntryCount: 2,
        },
      },
    },
  };
}

function buildStreamResponse(messages: string[]) {
  return {
    runId: 'run-logs-live',
    taskId: 'main',
    availableTaskIds: ['main'],
    maxEntriesPerStream: 50,
    stream: {
      taskId: 'main',
      order: 0,
      status: 'running',
      startedAt: '2026-03-10T10:00:00Z',
      completedAt: null,
      endedAt: null,
      durationSeconds: null,
      firstEntryAt: '2026-03-10T10:00:00Z',
      lastEntryAt: '2026-03-10T10:00:01Z',
      bufferedEntryCount: messages.length,
      totalEntryCount: messages.length,
    },
    entries: messages.map((message, index) => ({
      sequence: index + 1,
      timestamp: `2026-03-10T10:00:0${index}Z`,
      taskId: 'main',
      logger: 'comet.main',
      level: 'INFO',
      message,
    })),
  };
}

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
    expect(screen.queryByText('comet.main')).not.toBeInTheDocument();
    expect(mainButton).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByRole('log', { name: 'main 的日志条目' })).toBeInTheDocument();

    await user.click(workerButton);

    expect(await screen.findByText('worker log line')).toBeInTheDocument();
    expect(screen.getByText('main log line')).toBeInTheDocument();
    expect(screen.queryByText('comet.worker')).not.toBeInTheDocument();
    expect(workerButton).toHaveAttribute('aria-expanded', 'true');
    expect(fetchRunLogsForTaskSpy).toHaveBeenCalledWith('run-logs-1', 'main');
    expect(fetchRunLogsForTaskSpy).toHaveBeenCalledWith('run-logs-1', 'task-7');
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

  it('keeps expanded logs visible during unchanged summary polling', async () => {
    vi.useFakeTimers();

    vi.spyOn(api, 'fetchRunLogs').mockImplementation(async () => buildSummary('run-logs-live'));

    let resolveDetailPoll: ((value: ReturnType<typeof buildStreamResponse>) => void) | null = null;
    const fetchRunLogsForTaskSpy = vi
      .spyOn(api, 'fetchRunLogsForTask')
      .mockImplementationOnce(async () => buildStreamResponse(['first line']))
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            resolveDetailPoll = resolve;
          }),
      );

    render(<LogViewer runId="run-logs-live" runStatus="running" />);

    await act(async () => {
      await Promise.resolve();
    });

    const mainButton = screen.getByRole('button', { name: /main/i });
    fireEvent.click(mainButton);

    await act(async () => {
      await Promise.resolve();
    });

    expect(screen.getByText('first line')).toBeInTheDocument();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
    });

    expect(fetchRunLogsForTaskSpy).toHaveBeenCalledTimes(2);
    expect(screen.getByText('first line')).toBeInTheDocument();
    expect(screen.queryByText('正在加载日志条目...')).not.toBeInTheDocument();

    await act(async () => {
      resolveDetailPoll?.(buildStreamResponse(['first line', 'second line']));
      await Promise.resolve();
    });

    expect(screen.getByText('second line')).toBeInTheDocument();
  });

  it('preserves manual upward scroll and still tail-follows near the bottom', async () => {
    vi.useFakeTimers();

    vi.spyOn(api, 'fetchRunLogs').mockImplementation(async () => buildSummary('run-logs-live'));

    const fetchRunLogsForTaskSpy = vi
      .spyOn(api, 'fetchRunLogsForTask')
      .mockResolvedValueOnce(buildStreamResponse(['first line', 'second line']))
      .mockResolvedValueOnce(buildStreamResponse(['first line', 'second line', 'third line']))
      .mockResolvedValueOnce(
        buildStreamResponse(['first line', 'second line', 'third line', 'fourth line']),
      );

    render(<LogViewer runId="run-logs-live" runStatus="running" />);

    await act(async () => {
      await Promise.resolve();
    });

    const mainButton = screen.getByRole('button', { name: /main/i });
    fireEvent.click(mainButton);

    await act(async () => {
      await Promise.resolve();
    });

    const panel = screen.getByRole('log', { name: 'main 的日志条目' });
    let scrollHeight = 300;
    Object.defineProperty(panel, 'clientHeight', {
      configurable: true,
      get: () => 100,
    });
    Object.defineProperty(panel, 'scrollHeight', {
      configurable: true,
      get: () => scrollHeight,
    });

    expect(fetchRunLogsForTaskSpy).toHaveBeenCalledTimes(1);

    panel.scrollTop = 40;
    fireEvent.scroll(panel);
    scrollHeight = 360;

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
    });

    await act(async () => {
      await Promise.resolve();
    });

    expect(screen.getByText('third line')).toBeInTheDocument();
    expect(panel.scrollTop).toBe(40);

    panel.scrollTop = 348;
    fireEvent.scroll(panel);
    scrollHeight = 420;

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
    });

    await act(async () => {
      await Promise.resolve();
    });

    expect(screen.getByText('fourth line')).toBeInTheDocument();
    expect(panel.scrollTop).toBe(420);
  });

  it('keeps rendered logs visible when summary polling fails after initial load', async () => {
    vi.useFakeTimers();

    const fetchRunLogsSpy = vi
      .spyOn(api, 'fetchRunLogs')
      .mockResolvedValueOnce(buildSummary('run-logs-live'))
      .mockRejectedValueOnce(new Error('summary poll failed'));
    vi.spyOn(api, 'fetchRunLogsForTask').mockResolvedValue(buildStreamResponse(['first line']));

    render(<LogViewer runId="run-logs-live" runStatus="running" />);

    await act(async () => {
      await Promise.resolve();
    });

    fireEvent.click(screen.getByRole('button', { name: /main/i }));

    await act(async () => {
      await Promise.resolve();
    });

    expect(screen.getByText('first line')).toBeInTheDocument();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
    });

    expect(fetchRunLogsSpy).toHaveBeenCalledTimes(2);
    expect(screen.getByText('first line')).toBeInTheDocument();
    expect(screen.getByRole('log', { name: 'main 的日志条目' })).toBeInTheDocument();
    expect(screen.getByRole('alert')).toHaveTextContent('summary poll failed');
  });

  it('updates row status from summary polling even after detail data was cached', async () => {
    vi.useFakeTimers();

    let summaryCalls = 0;
    vi.spyOn(api, 'fetchRunLogs').mockImplementation(async () => {
      summaryCalls += 1;

      return {
        runId: 'run-logs-status',
        streams: {
          taskIds: ['main'],
          counts: { main: 1 },
          maxEntriesPerStream: 50,
          items: [
            {
              taskId: 'main',
              order: 0,
              status: summaryCalls === 1 ? 'running' : 'completed',
              startedAt: '2026-03-10T10:00:00Z',
              completedAt: summaryCalls === 1 ? null : '2026-03-10T10:00:05Z',
              endedAt: summaryCalls === 1 ? null : '2026-03-10T10:00:05Z',
              durationSeconds: summaryCalls === 1 ? null : 5,
              firstEntryAt: '2026-03-10T10:00:00Z',
              lastEntryAt: '2026-03-10T10:00:05Z',
              bufferedEntryCount: 1,
              totalEntryCount: 1,
            },
          ],
          byTaskId: {
            main: {
              taskId: 'main',
              order: 0,
              status: summaryCalls === 1 ? 'running' : 'completed',
              startedAt: '2026-03-10T10:00:00Z',
              completedAt: summaryCalls === 1 ? null : '2026-03-10T10:00:05Z',
              endedAt: summaryCalls === 1 ? null : '2026-03-10T10:00:05Z',
              durationSeconds: summaryCalls === 1 ? null : 5,
              firstEntryAt: '2026-03-10T10:00:00Z',
              lastEntryAt: '2026-03-10T10:00:05Z',
              bufferedEntryCount: 1,
              totalEntryCount: 1,
            },
          },
        },
      };
    });
    const fetchRunLogsForTaskSpy = vi.spyOn(api, 'fetchRunLogsForTask').mockResolvedValue({
      runId: 'run-logs-status',
      taskId: 'main',
      availableTaskIds: ['main'],
      maxEntriesPerStream: 50,
      stream: {
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
      entries: [
        {
          sequence: 1,
          timestamp: '2026-03-10T10:00:00Z',
          taskId: 'main',
          logger: 'comet.main',
          level: 'INFO',
          message: 'cached line',
        },
      ],
    });

    render(<LogViewer runId="run-logs-status" runStatus="running" />);

    await act(async () => {
      await Promise.resolve();
    });

    expect(screen.getByRole('button', { name: /main/i })).toHaveTextContent('运行中');

    const mainButton = screen.getByRole('button', { name: /main/i });
    fireEvent.click(mainButton);

    await act(async () => {
      await Promise.resolve();
    });

    expect(screen.getByText('cached line')).toBeInTheDocument();

    fireEvent.click(mainButton);
    expect(mainButton).toHaveAttribute('aria-expanded', 'false');
    expect(fetchRunLogsForTaskSpy).toHaveBeenCalledTimes(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
      await Promise.resolve();
    });

    expect(screen.getByRole('button', { name: /main/i })).toHaveTextContent('已完成');
    expect(fetchRunLogsForTaskSpy).toHaveBeenCalledTimes(1);
  });

  it('updates timeline bar width from summary polling even after detail data was cached', async () => {
    vi.useFakeTimers();

    let summaryCalls = 0;
    vi.spyOn(api, 'fetchRunLogs').mockImplementation(async () => {
      summaryCalls += 1;

      return {
        runId: 'run-logs-timeline',
        streams: {
          taskIds: ['main', 'task-2'],
          counts: { main: 1, 'task-2': 1 },
          maxEntriesPerStream: 50,
          items: [
            {
              taskId: 'main',
              order: 0,
              status: summaryCalls === 1 ? 'running' : 'completed',
              startedAt: '2026-03-10T10:00:00Z',
              completedAt: summaryCalls === 1 ? null : '2026-03-10T10:00:05Z',
              endedAt: summaryCalls === 1 ? null : '2026-03-10T10:00:05Z',
              durationSeconds: summaryCalls === 1 ? null : 5,
              firstEntryAt: '2026-03-10T10:00:00Z',
              lastEntryAt: summaryCalls === 1 ? '2026-03-10T10:00:02Z' : '2026-03-10T10:00:05Z',
              bufferedEntryCount: 1,
              totalEntryCount: 1,
            },
            {
              taskId: 'task-2',
              order: 1,
              status: 'running',
              startedAt: '2026-03-10T10:00:00Z',
              completedAt: null,
              endedAt: null,
              durationSeconds: null,
              firstEntryAt: '2026-03-10T10:00:00Z',
              lastEntryAt: '2026-03-10T10:00:10Z',
              bufferedEntryCount: 1,
              totalEntryCount: 1,
            },
          ],
          byTaskId: {
            main: {
              taskId: 'main',
              order: 0,
              status: summaryCalls === 1 ? 'running' : 'completed',
              startedAt: '2026-03-10T10:00:00Z',
              completedAt: summaryCalls === 1 ? null : '2026-03-10T10:00:05Z',
              endedAt: summaryCalls === 1 ? null : '2026-03-10T10:00:05Z',
              durationSeconds: summaryCalls === 1 ? null : 5,
              firstEntryAt: '2026-03-10T10:00:00Z',
              lastEntryAt: summaryCalls === 1 ? '2026-03-10T10:00:02Z' : '2026-03-10T10:00:05Z',
              bufferedEntryCount: 1,
              totalEntryCount: 1,
            },
            'task-2': {
              taskId: 'task-2',
              order: 1,
              status: 'running',
              startedAt: '2026-03-10T10:00:00Z',
              completedAt: null,
              endedAt: null,
              durationSeconds: null,
              firstEntryAt: '2026-03-10T10:00:00Z',
              lastEntryAt: '2026-03-10T10:00:10Z',
              bufferedEntryCount: 1,
              totalEntryCount: 1,
            },
          },
        },
      };
    });

    const fetchRunLogsForTaskSpy = vi.spyOn(api, 'fetchRunLogsForTask').mockResolvedValue({
      runId: 'run-logs-timeline',
      taskId: 'main',
      availableTaskIds: ['main', 'task-2'],
      maxEntriesPerStream: 50,
      stream: {
        taskId: 'main',
        order: 0,
        status: 'running',
        startedAt: '2026-03-10T10:00:00Z',
        completedAt: null,
        endedAt: null,
        durationSeconds: null,
        firstEntryAt: '2026-03-10T10:00:00Z',
        lastEntryAt: '2026-03-10T10:00:02Z',
        bufferedEntryCount: 1,
        totalEntryCount: 1,
      },
      entries: [
        {
          sequence: 1,
          timestamp: '2026-03-10T10:00:00Z',
          taskId: 'main',
          logger: 'comet.main',
          level: 'INFO',
          message: 'cached line',
        },
      ],
    });

    render(<LogViewer runId="run-logs-timeline" runStatus="running" />);

    await act(async () => {
      await Promise.resolve();
    });

    const mainButton = screen.getByRole('button', { name: /main/i });
    const initialBar = mainButton.querySelector('.run-log-row__bar');
    expect(initialBar).not.toBeNull();
    expect(initialBar).toHaveAttribute('style', expect.stringContaining('width: 20%'));

    fireEvent.click(mainButton);

    await act(async () => {
      await Promise.resolve();
    });

    expect(screen.getByText('cached line')).toBeInTheDocument();
    fireEvent.click(mainButton);
    expect(mainButton).toHaveAttribute('aria-expanded', 'false');
    expect(fetchRunLogsForTaskSpy).toHaveBeenCalledTimes(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
      await Promise.resolve();
    });

    const updatedBar = screen
      .getByRole('button', { name: /main/i })
      .querySelector('.run-log-row__bar');
    expect(updatedBar).not.toBeNull();
    expect(updatedBar).toHaveAttribute('style', expect.stringContaining('width: 50%'));
    expect(fetchRunLogsForTaskSpy).toHaveBeenCalledTimes(1);
  });

  it('clears expanded log state when the run id changes', async () => {
    vi.spyOn(api, 'fetchRunLogs').mockImplementation(async (requestedRunId) => buildSummary(requestedRunId));
    vi.spyOn(api, 'fetchRunLogsForTask').mockImplementation(async (_runId, taskId) => ({
      ...buildStreamResponse(['first line']),
      taskId,
    }));

    const view = render(<LogViewer runId="run-logs-live" runStatus="running" />);

    await act(async () => {
      await Promise.resolve();
    });

    fireEvent.click(screen.getByRole('button', { name: /main/i }));

    await act(async () => {
      await Promise.resolve();
    });

    expect(screen.getByText('first line')).toBeInTheDocument();

    view.rerender(<LogViewer runId="run-logs-next" runStatus="running" />);

    await waitFor(() => {
      expect(screen.queryByText('first line')).not.toBeInTheDocument();
    });
    expect(screen.getByRole('button', { name: /main/i })).toHaveAttribute('aria-expanded', 'false');
  });
});
