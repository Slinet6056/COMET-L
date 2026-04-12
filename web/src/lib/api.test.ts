import { afterEach, describe, expect, it, vi } from 'vitest';

import { createRun, fetchGitHubRepositories, fetchRunLogsForTask } from './api';

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
    expect(payload.get('projectPath')).toBe('/tmp/project');
  });

  it('omits empty projectPath for github-mode submissions', async () => {
    let capturedBody: unknown = null;
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (_input, init) => {
      capturedBody = (init?.body ?? null) as FormData | null;
      return new Response(
        JSON.stringify({
          runId: 'run-3',
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
      projectPath: '',
      githubRepoUrl: 'https://github.com/openai/example-repo',
      selectedJavaVersion: '17',
      config: {
        llm: { api_key: 'test-key' },
      },
    });

    expect(capturedBody instanceof FormData).toBe(true);
    if (!(capturedBody instanceof FormData)) {
      throw new Error('expected FormData body');
    }
    const payload = capturedBody;
    expect(payload.get('projectPath')).toBeNull();
    expect(payload.get('githubRepoUrl')).toBe('https://github.com/openai/example-repo');
  });
});

describe('fetchGitHubRepositories', () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('fetches repositories from the github endpoint', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(
        JSON.stringify({
          repositories: [
            {
              name: 'test-repo',
              fullName: 'testuser/test-repo',
              url: 'https://github.com/testuser/test-repo',
              description: 'A test repository',
              private: false,
              updatedAt: '2024-01-15T10:30:00Z',
            },
            {
              name: 'private-repo',
              fullName: 'testuser/private-repo',
              url: 'https://github.com/testuser/private-repo',
              description: null,
              private: true,
              updatedAt: null,
            },
          ],
        }),
        {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        },
      ),
    );

    const result = await fetchGitHubRepositories();

    expect(result.repositories).toHaveLength(2);
    expect(result.repositories[0].fullName).toBe('testuser/test-repo');
    expect(result.repositories[0].url).toBe('https://github.com/testuser/test-repo');
    expect(result.repositories[1].private).toBe(true);
  });

  it('throws ApiError when fetch fails', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(
        JSON.stringify({
          error: {
            code: 'github_auth_required',
            message: 'GitHub authorization required',
            fieldErrors: [],
          },
        }),
        {
          status: 401,
          headers: { 'Content-Type': 'application/json' },
        },
      ),
    );

    await expect(fetchGitHubRepositories()).rejects.toThrow('GitHub authorization required');
  });
});
