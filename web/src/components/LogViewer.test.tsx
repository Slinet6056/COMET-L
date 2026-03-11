import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { LogViewer } from './LogViewer';
import * as api from '../lib/api';

describe('Log viewer', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  afterEach(() => {
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

    render(<LogViewer runId="run-logs-1" />);

    expect(await screen.findByText('main log line')).toBeInTheDocument();
    expect(screen.getByText('Buffered: 2 / 50')).toBeInTheDocument();

    await user.click(screen.getByRole('tab', { name: 'task-7' }));

    expect(await screen.findByText('worker log line')).toBeInTheDocument();
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

    render(<LogViewer runId="run-logs-2" />);

    expect(await screen.findByText('Only the main log stream is available for this run.')).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.queryByText('Loading log entries...')).not.toBeInTheDocument();
    });
    expect(screen.getByText('No log entries yet.')).toBeInTheDocument();
    expect(screen.getByText('The coordinator stream has not written any messages yet.')).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.queryByRole('tablist', { name: 'Log streams' })).not.toBeInTheDocument();
    });
  });
});
