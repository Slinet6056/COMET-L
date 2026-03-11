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

  it('switches between main and worker streams using the logs API', async () => {
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

    expect(await screen.findByText('main log line')).toBeInTheDocument();
    expect(screen.getByText('Selected: main')).toBeInTheDocument();
    expect(screen.getByText('Visible: 1')).toBeInTheDocument();
    expect(screen.getByText('Buffered: 2 / 50')).toBeInTheDocument();
    expect(screen.getByRole('log', { name: 'Log entries for main' })).toBeInTheDocument();
    const rows = screen.getAllByRole('button');
    expect(rows[0]).toHaveTextContent('task-7');
    expect(rows[1]).toHaveTextContent('main');

    await user.click(screen.getByRole('button', { name: /task-7/i }));

    expect(await screen.findByText('worker log line')).toBeInTheDocument();
    expect(screen.getByText('Selected: task-7')).toBeInTheDocument();
    expect(screen.getByText('Status: completed')).toBeInTheDocument();
    expect(fetchRunLogsForTaskSpy).toHaveBeenNthCalledWith(1, 'run-logs-1', 'main');
    expect(fetchRunLogsForTaskSpy).toHaveBeenNthCalledWith(2, 'run-logs-1', 'task-7');
  });

  it('gracefully degrades when only the main stream exists and it is empty', async () => {
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

    expect(await screen.findByText('Only the main log stream is available for this run.')).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.queryByText('Loading log entries...')).not.toBeInTheDocument();
    });
    expect(screen.getByText('No log entries yet.')).toBeInTheDocument();
    expect(screen.getByText('The coordinator stream has not written any messages yet.')).toBeInTheDocument();
    expect(screen.getByText('Selected: main')).toBeInTheDocument();
    expect(screen.getByText('Visible: 0')).toBeInTheDocument();
    expect(screen.getByText('Buffered: 0 / 200')).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.queryByText('task-9')).not.toBeInTheDocument();
    });
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

    expect(await screen.findByText('main log line')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /task-9/i })).not.toBeInTheDocument();

    await act(async () => {
      await new Promise((resolve) => window.setTimeout(resolve, 1100));
    });

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /task-9/i })).toBeInTheDocument();
    });
  });
});
