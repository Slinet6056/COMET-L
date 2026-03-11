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
        taskIds: ['main', 'task-7'],
        counts: { main: 2, 'task-7': 1 },
        maxEntriesPerStream: 50,
      },
    });
    const fetchRunLogsForTaskSpy = vi
      .spyOn(api, 'fetchRunLogsForTask')
      .mockImplementation(async (_runId, taskId) => ({
        runId: 'run-logs-1',
        taskId,
        availableTaskIds: ['main', 'task-7'],
        maxEntriesPerStream: 50,
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

    await user.click(screen.getByRole('tab', { name: 'task-7' }));

    expect(await screen.findByText('worker log line')).toBeInTheDocument();
    expect(screen.getByText('Selected: task-7')).toBeInTheDocument();
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
      },
    });
    vi.spyOn(api, 'fetchRunLogsForTask').mockResolvedValue({
      runId: 'run-logs-2',
      taskId: 'main',
      availableTaskIds: ['main'],
      maxEntriesPerStream: 200,
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
      expect(screen.queryByRole('tablist', { name: 'Log streams' })).not.toBeInTheDocument();
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
              }
            : {
                taskIds: ['main', 'task-9'],
                counts: { main: 1, 'task-9': 1 },
                maxEntriesPerStream: 50,
              },
      };
    });
    vi.spyOn(api, 'fetchRunLogsForTask').mockImplementation(async (_runId, taskId) => ({
      runId: 'run-logs-3',
      taskId,
      availableTaskIds: taskId === 'main' ? ['main'] : ['main', 'task-9'],
      maxEntriesPerStream: 50,
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
    expect(screen.queryByRole('tab', { name: 'task-9' })).not.toBeInTheDocument();

    await act(async () => {
      await new Promise((resolve) => window.setTimeout(resolve, 1100));
    });

    await waitFor(() => {
      expect(screen.getByRole('tab', { name: 'task-9' })).toBeInTheDocument();
    });
  });
});
