import { afterEach, describe, expect, it, vi } from 'vitest';

import { fetchRunLogsForTask } from './api';

describe('fetchRunLogsForTask', () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('encodes task ids containing reserved URL characters', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(
        JSON.stringify({
          runId: 'run-1',
          taskId: 'Pre:ProductService.addProduct#abc123',
          availableTaskIds: ['main', 'Pre:ProductService.addProduct#abc123'],
          maxEntriesPerStream: 200,
          stream: {
            taskId: 'Pre:ProductService.addProduct#abc123',
            order: 1,
            status: 'running',
            startedAt: null,
            completedAt: null,
            failedAt: null,
            endedAt: null,
            durationSeconds: null,
            firstEntryAt: null,
            lastEntryAt: null,
            bufferedEntryCount: 0,
            totalEntryCount: 0,
          },
          entries: [],
        }),
        {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        },
      ),
    );

    await fetchRunLogsForTask('run-1', 'Pre:ProductService.addProduct#abc123');

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/runs/run-1/logs/Pre%3AProductService.addProduct%23abc123',
    );
  });
});
