import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  ApiError,
  createRun,
  fetchConfigDefaults,
  fetchGitHubRepositories,
  fetchRunResults,
  fetchRunSnapshot,
  fetchRunLogsForTask,
  getCurrentUser,
  login,
  logout,
  uploadBugReportsZip,
  uploadProjectZip,
  subscribeToRunEvents,
} from './api';

describe('fetchConfigDefaults', () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('preserves config policy annotations from the defaults endpoint', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(
        JSON.stringify({
          config: {
            deployment: {
              allow_local_path_mode: false,
            },
          },
          configPolicy: {
            overriddenFields: ['preprocessing.max_workers'],
            clampedFields: ['evolution.budget_llm_calls'],
            redactedFields: ['llm.api_key'],
          },
        }),
        {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        },
      ),
    );

    const result = await fetchConfigDefaults();

    expect(result.configPolicy?.overriddenFields).toEqual(['preprocessing.max_workers']);
    expect(result.configPolicy?.clampedFields).toEqual(['evolution.budget_llm_calls']);
    expect(result.configPolicy?.redactedFields).toEqual(['llm.api_key']);
  });
});

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

describe('subscribeToRunEvents', () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it('closes terminal streams and ignores the follow-up disconnect error', () => {
    class MockEventSource {
      static instances: MockEventSource[] = [];

      url: string;
      onerror: (() => void) | null = null;
      closed = false;
      close = vi.fn(() => {
        this.closed = true;
      });

      private listeners = new Map<string, Array<(event: MessageEvent<string>) => void>>();

      constructor(url: string) {
        this.url = url;
        MockEventSource.instances.push(this);
      }

      addEventListener(type: string, listener: EventListenerOrEventListenerObject) {
        const callback = listener as (event: MessageEvent<string>) => void;
        const current = this.listeners.get(type) ?? [];
        current.push(callback);
        this.listeners.set(type, current);
      }

      emit(type: string, payload: unknown) {
        if (this.closed) {
          return;
        }

        const event = new MessageEvent(type, { data: JSON.stringify(payload) });
        for (const listener of this.listeners.get(type) ?? []) {
          listener(event);
        }
      }

      triggerError() {
        if (!this.closed) {
          this.onerror?.();
        }
      }
    }

    vi.stubGlobal('EventSource', MockEventSource as unknown as typeof EventSource);

    const onEvent = vi.fn();
    const onError = vi.fn();
    const teardown = subscribeToRunEvents('run-terminal', {
      onEvent,
      onError,
    });

    const stream = MockEventSource.instances[0];
    stream.emit('run.snapshot', {
      type: 'run.snapshot',
      status: 'completed',
      snapshot: {
        runId: 'run-terminal',
        status: 'completed',
      },
    });

    expect(onEvent).toHaveBeenCalledTimes(1);
    expect(stream.close).toHaveBeenCalledTimes(1);

    stream.triggerError();
    expect(onError).not.toHaveBeenCalled();

    teardown();
  });
});

describe('run visibility API errors', () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('exposes 401 status and stable auth code without losing the safe message', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(
        JSON.stringify({
          error: {
            code: 'auth_required',
            message: '请先登录',
            fieldErrors: [],
          },
        }),
        {
          status: 401,
          headers: { 'Content-Type': 'application/json' },
        },
      ),
    );

    await expect(fetchRunSnapshot('run-private')).rejects.toMatchObject({
      name: 'ApiError',
      status: 401,
      code: 'auth_required',
      message: '请先登录',
    });
  });

  it('exposes 404 status and not-found code so pages can render cross-user copy safely', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(
        JSON.stringify({
          error: {
            code: 'run_not_found',
            message: 'Run run-secret was not found at /home/comet/state/users/alice/run-secret',
            fieldErrors: [],
          },
        }),
        {
          status: 404,
          headers: { 'Content-Type': 'application/json' },
        },
      ),
    );

    const error = await fetchRunResults('run-secret').catch((caught: unknown) => caught);

    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({
      status: 404,
      code: 'run_not_found',
    });
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

  it('strips deployment policy from generated run config file', async () => {
    let capturedBody: unknown = null;
    let capturedConfigText = '';
    const OriginalFile = globalThis.File;

    class CapturingFile extends OriginalFile {
      constructor(parts: BlobPart[], name: string, options?: FilePropertyBag) {
        super(parts, name, options);
        capturedConfigText = parts
          .map((part) => (typeof part === 'string' ? part : String(part)))
          .join('');
      }
    }

    vi.stubGlobal('File', CapturingFile as typeof File);
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (_input, init) => {
      capturedBody = (init?.body ?? null) as FormData | null;
      return new Response(
        JSON.stringify({
          runId: 'run-4',
          status: 'created',
          mode: 'standard',
        }),
        {
          status: 201,
          headers: { 'Content-Type': 'application/json' },
        },
      );
    });

    const config = {
      llm: { api_key: 'test-key' },
      evolution: { mutation_enabled: true, max_iterations: 10 },
      deployment: { max_budget: 100, allow_local_path_mode: true },
    };

    try {
      await createRun({
        projectPath: '/tmp/project',
        config,
      });
    } finally {
      vi.unstubAllGlobals();
    }

    expect(capturedBody instanceof FormData).toBe(true);
    if (!(capturedBody instanceof FormData)) {
      throw new Error('expected FormData body');
    }
    const submittedConfig = JSON.parse(capturedConfigText) as Record<string, unknown>;
    expect(submittedConfig.deployment).toBeUndefined();
    expect(submittedConfig.llm).toEqual({ api_key: 'test-key' });
    expect(config.deployment).toEqual({ max_budget: 100, allow_local_path_mode: true });
  });
});

describe('uploadProjectZip', () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('uploads project zip to the uploads endpoint', async () => {
    let capturedBody: unknown = null;
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (_input, init) => {
      capturedBody = (init?.body ?? null) as FormData | null;
      return new Response(
        JSON.stringify({
          uploadId: 'upload-123',
          kind: 'project',
          status: 'ready',
          originalFilename: 'project.zip',
          extractedRoot: '/sandbox/uploads/upload-123',
        }),
        {
          status: 201,
          headers: { 'Content-Type': 'application/json' },
        },
      );
    });

    const file = new File(['test content'], 'project.zip', { type: 'application/zip' });
    const result = await uploadProjectZip(file);

    expect(capturedBody instanceof FormData).toBe(true);
    if (!(capturedBody instanceof FormData)) {
      throw new Error('expected FormData body');
    }
    const payload = capturedBody;
    expect(payload.get('file')).toBe(file);
    expect(result.uploadId).toBe('upload-123');
    expect(result.kind).toBe('project');
    expect(result.originalFilename).toBe('project.zip');
  });

  it('throws ApiError on upload failure', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(
        JSON.stringify({
          error: {
            code: 'invalid_zip',
            message: '上传的文件不是有效的 ZIP 文件',
            fieldErrors: [],
          },
        }),
        {
          status: 400,
          headers: { 'Content-Type': 'application/json' },
        },
      ),
    );

    const file = new File(['invalid'], 'invalid.zip', { type: 'application/zip' });
    await expect(uploadProjectZip(file)).rejects.toThrow('上传的文件不是有效的 ZIP 文件');
  });
});

describe('uploadBugReportsZip', () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('uploads bug reports zip to the uploads endpoint', async () => {
    let capturedBody: unknown = null;
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (_input, init) => {
      capturedBody = (init?.body ?? null) as FormData | null;
      return new Response(
        JSON.stringify({
          uploadId: 'upload-456',
          kind: 'bug_reports',
          status: 'ready',
          originalFilename: 'bug-reports.zip',
          extractedRoot: '/sandbox/uploads/upload-456',
        }),
        {
          status: 201,
          headers: { 'Content-Type': 'application/json' },
        },
      );
    });

    const file = new File(['test content'], 'bug-reports.zip', { type: 'application/zip' });
    const result = await uploadBugReportsZip(file);

    expect(capturedBody instanceof FormData).toBe(true);
    if (!(capturedBody instanceof FormData)) {
      throw new Error('expected FormData body');
    }
    const payload = capturedBody;
    expect(payload.get('file')).toBe(file);
    expect(result.uploadId).toBe('upload-456');
    expect(result.kind).toBe('bug_reports');
    expect(result.originalFilename).toBe('bug-reports.zip');
  });
});

describe('createRun with upload IDs', () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('submits upload IDs for upload-mode runs', async () => {
    let capturedBody: unknown = null;
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (_input, init) => {
      capturedBody = (init?.body ?? null) as FormData | null;
      return new Response(
        JSON.stringify({
          runId: 'run-upload-1',
          status: 'pending',
          mode: 'upload',
          queuePosition: 1,
          effectiveConfig: { llm: { api_key: '[REDACTED]' } },
          configPolicy: {},
          uploadSource: {
            projectUploadId: 'upload-123',
            bugReportsUploadId: 'upload-456',
          },
        }),
        {
          status: 201,
          headers: { 'Content-Type': 'application/json' },
        },
      );
    });

    await createRun({
      projectUploadId: 'upload-123',
      bugReportsUploadId: 'upload-456',
      config: {
        llm: { api_key: 'test-key' },
      },
    });

    expect(capturedBody instanceof FormData).toBe(true);
    if (!(capturedBody instanceof FormData)) {
      throw new Error('expected FormData body');
    }
    const payload = capturedBody;
    expect(payload.get('projectUploadId')).toBe('upload-123');
    expect(payload.get('bugReportsUploadId')).toBe('upload-456');
    expect(payload.get('projectPath')).toBeNull();
  });

  it('omits empty upload IDs', async () => {
    let capturedBody: unknown = null;
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (_input, init) => {
      capturedBody = (init?.body ?? null) as FormData | null;
      return new Response(
        JSON.stringify({
          runId: 'run-upload-2',
          status: 'pending',
          mode: 'upload',
          queuePosition: 2,
        }),
        {
          status: 201,
          headers: { 'Content-Type': 'application/json' },
        },
      );
    });

    await createRun({
      projectUploadId: 'upload-789',
      bugReportsUploadId: null,
      config: {
        llm: { api_key: 'test-key' },
      },
    });

    expect(capturedBody instanceof FormData).toBe(true);
    if (!(capturedBody instanceof FormData)) {
      throw new Error('expected FormData body');
    }
    const payload = capturedBody;
    expect(payload.get('projectUploadId')).toBe('upload-789');
    expect(payload.get('bugReportsUploadId')).toBeNull();
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

describe('login', () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('posts username and password to auth endpoint', async () => {
    let capturedInit: RequestInit | undefined;
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (_input, init) => {
      capturedInit = init;
      return new Response(
        JSON.stringify({
          user: { id: 1, username: 'testuser', role: 'admin' },
        }),
        {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        },
      );
    });

    const result = await login('testuser', 'password123');

    expect(capturedInit?.method).toBe('POST');
    expect(capturedInit?.headers).toEqual({ 'Content-Type': 'application/json' });
    expect(capturedInit?.body).toBe(
      JSON.stringify({ username: 'testuser', password: 'password123' }),
    );
    expect(result.user.id).toBe(1);
    expect(result.user.username).toBe('testuser');
    expect(result.user.role).toBe('admin');
  });

  it('throws ApiError on invalid credentials', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(
        JSON.stringify({
          error: {
            code: 'invalid_credentials',
            message: '用户名或密码错误',
            fieldErrors: [],
          },
        }),
        {
          status: 401,
          headers: { 'Content-Type': 'application/json' },
        },
      ),
    );

    await expect(login('wrong', 'wrong')).rejects.toThrow('用户名或密码错误');
  });
});

describe('logout', () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('posts to logout endpoint', async () => {
    let capturedInit: RequestInit | undefined;
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (_input, init) => {
      capturedInit = init;
      return new Response(null, { status: 200 });
    });

    await logout();

    expect(capturedInit?.method).toBe('POST');
  });

  it('throws ApiError on logout failure', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(
        JSON.stringify({
          error: {
            code: 'auth_required',
            message: '未登录',
            fieldErrors: [],
          },
        }),
        {
          status: 401,
          headers: { 'Content-Type': 'application/json' },
        },
      ),
    );

    await expect(logout()).rejects.toThrow('未登录');
  });
});

describe('getCurrentUser', () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('fetches current user from auth me endpoint', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(
        JSON.stringify({
          user: { id: 2, username: 'regularuser', role: 'user' },
        }),
        {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        },
      ),
    );

    const result = await getCurrentUser();

    expect(result.user.id).toBe(2);
    expect(result.user.username).toBe('regularuser');
    expect(result.user.role).toBe('user');
  });

  it('throws ApiError when not authenticated', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(
        JSON.stringify({
          error: {
            code: 'auth_required',
            message: '请先登录',
            fieldErrors: [],
          },
        }),
        {
          status: 401,
          headers: { 'Content-Type': 'application/json' },
        },
      ),
    );

    await expect(getCurrentUser()).rejects.toThrow('请先登录');
  });
});
