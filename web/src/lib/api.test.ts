import { afterEach, describe, expect, it, vi } from 'vitest';

import { createRun, fetchRunLogsForTask } from './api';

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

describe('createRun', () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('submits github and java contract fields', async () => {
    let capturedBody: unknown = null;
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (_input, init) => {
      capturedBody = (init?.body ?? null) as FormData | null;
      return new Response(
        JSON.stringify({
          runId: 'run-2',
          status: 'created',
          mode: 'standard',
        }),
        {
          status: 201,
          headers: { 'Content-Type': 'application/json' },
        },
      );
    });

    await createRun({
      projectPath: '/tmp/project',
      githubRepoUrl: 'https://github.com/openai/example-repo',
      githubBaseBranch: 'main',
      selectedJavaVersion: '21',
      config: {
        llm: { api_key: 'test-key' },
      },
    });

    expect(capturedBody instanceof FormData).toBe(true);
    if (!(capturedBody instanceof FormData)) {
      throw new Error('expected FormData body');
    }
    const payload = capturedBody;
    expect(payload.get('githubRepoUrl')).toBe('https://github.com/openai/example-repo');
    expect(payload.get('githubBaseBranch')).toBe('main');
    expect(payload.get('selectedJavaVersion')).toBe('21');
  });
});
