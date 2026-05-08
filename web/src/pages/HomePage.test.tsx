import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { App } from '../App';
import * as api from '../lib/api';

const defaultConfig = {
  llm: {
    base_url: 'https://api.openai.com/v1',
    api_key: 'default-key',
    model: 'gpt-4',
    temperature: 0.7,
    max_tokens: 4096,
    supports_json_mode: true,
    timeout: 120,
    reasoning_effort: null,
    reasoning_enabled: null,
    verbosity: null,
  },
  execution: {
    timeout: 300,
    test_timeout: 30,
    coverage_timeout: 300,
    max_retries: 3,
    runtime_java_home: null,
    target_java_home: null,
    selected_java_version: null,
    maven_home: null,
  },
  evolution: {
    mutation_enabled: true,
    max_iterations: 10,
    min_improvement_threshold: 0.01,
    budget_llm_calls: 1000,
    stop_on_no_improvement_rounds: 3,
    excellent_mutation_score: 0.95,
    excellent_line_coverage: 0.9,
    excellent_branch_coverage: 0.85,
    min_method_lines: 5,
  },
  knowledge: {
    enabled: true,
    enable_dynamic_update: true,
    pattern_confidence_threshold: 0.5,
    contract_extraction_enabled: true,
    embedding: {
      base_url: 'https://api.openai.com/v1',
      api_key: null,
      model: 'text-embedding-3-small',
      batch_size: 100,
    },
    retrieval: {
      top_k: 5,
      score_threshold: 0.5,
    },
  },
  logging: {
    level: 'INFO',
    file: 'comet.log',
  },
  preprocessing: {
    enabled: true,
    exit_after_preprocessing: false,
    max_workers: null,
    timeout_per_method: 300,
  },
  formatting: {
    enabled: true,
    style: 'GOOGLE',
  },
  agent: {
    parallel: {
      enabled: false,
      max_parallel_targets: 4,
      max_eval_workers: 4,
      timeout_per_target: 300,
    },
  },
  deployment: {
    allow_local_path_mode: false,
  },
};

const localPathConfig = {
  ...defaultConfig,
  deployment: {
    allow_local_path_mode: true,
  },
};

const defaultUser = { id: 1, username: 'testuser', role: 'user' as const };
const adminUser = { id: 2, username: 'admin', role: 'admin' as const };

const availableExamplesPayload = {
  projects: [
    {
      id: 'calculator-demo',
      label: '计算器示例',
      displayName: '计算器示例',
      description: '最小 Maven 计算器项目。',
    },
    {
      id: 'multi-file-demo',
      label: '多文件示例',
      displayName: '多文件示例',
      description: '覆盖多文件协作的 Maven 示例项目。',
    },
  ],
  config: {
    available: true,
    defaults: defaultConfig,
    error: null,
  },
};

function mockExamplesEndpoint(payload: unknown = availableExamplesPayload) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    if (input === '/api/examples') {
      return new Response(JSON.stringify(payload), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      });
    }

    return new Response(
      JSON.stringify({
        error: {
          code: 'not_found',
          message: '测试未 mock 此端点。',
          fieldErrors: [],
        },
      }),
      {
        status: 404,
        headers: { 'Content-Type': 'application/json' },
      },
    );
  });
}

describe('HomePage upload-first UI for ordinary users', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('shows upload tab as default for ordinary users', async () => {
    vi.spyOn(api, 'fetchConfigDefaults').mockResolvedValue({ config: defaultConfig });
    vi.spyOn(api, 'getCurrentUser').mockResolvedValue({ user: defaultUser });

    render(
      <MemoryRouter initialEntries={['/']}>
        <App />
      </MemoryRouter>,
    );

    await screen.findByLabelText('上传项目 ZIP');

    expect(screen.getByRole('tab', { name: '上传项目', selected: true })).toBeInTheDocument();
    expect(screen.queryByRole('tab', { name: '本地路径' })).not.toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'GitHub 仓库' })).toBeInTheDocument();
  });

  it('shows project upload input for ordinary users', async () => {
    vi.spyOn(api, 'fetchConfigDefaults').mockResolvedValue({ config: defaultConfig });
    vi.spyOn(api, 'getCurrentUser').mockResolvedValue({ user: defaultUser });

    render(
      <MemoryRouter initialEntries={['/']}>
        <App />
      </MemoryRouter>,
    );

    await screen.findByLabelText('上传项目 ZIP');
    expect(screen.getByLabelText('上传项目 ZIP')).toBeInTheDocument();
    expect(screen.getByText('点击上传项目 ZIP')).toBeInTheDocument();
    expect(screen.getByText('必需，包含 Maven pom.xml 的项目目录')).toBeInTheDocument();
  });

  it('shows optional bug reports upload input for ordinary users', async () => {
    vi.spyOn(api, 'fetchConfigDefaults').mockResolvedValue({ config: defaultConfig });
    vi.spyOn(api, 'getCurrentUser').mockResolvedValue({ user: defaultUser });

    render(
      <MemoryRouter initialEntries={['/']}>
        <App />
      </MemoryRouter>,
    );

    await screen.findByLabelText('上传项目 ZIP');
    expect(screen.getByLabelText('上传缺陷报告 ZIP')).toBeInTheDocument();
    expect(screen.getByText('点击上传缺陷报告 ZIP')).toBeInTheDocument();
    expect(screen.getByText('可选，包含 Markdown 缺陷报告的目录')).toBeInTheDocument();
  });

  it('does not show local path inputs for ordinary users', async () => {
    vi.spyOn(api, 'fetchConfigDefaults').mockResolvedValue({ config: defaultConfig });
    vi.spyOn(api, 'getCurrentUser').mockResolvedValue({ user: defaultUser });

    render(
      <MemoryRouter initialEntries={['/']}>
        <App />
      </MemoryRouter>,
    );

    await screen.findByLabelText('上传项目 ZIP');
    expect(screen.queryByLabelText('项目路径')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('缺陷报告目录')).not.toBeInTheDocument();
  });

  it('requires project upload before submit', async () => {
    vi.spyOn(api, 'fetchConfigDefaults').mockResolvedValue({ config: defaultConfig });
    vi.spyOn(api, 'getCurrentUser').mockResolvedValue({ user: defaultUser });

    render(
      <MemoryRouter initialEntries={['/']}>
        <App />
      </MemoryRouter>,
    );

    await screen.findByLabelText('上传项目 ZIP');
    expect(screen.getByRole('button', { name: '请选择 Java 版本' })).toBeDisabled();
  });

  it('uploads project zip and enables submit', async () => {
    vi.spyOn(api, 'fetchConfigDefaults').mockResolvedValue({ config: defaultConfig });
    vi.spyOn(api, 'getCurrentUser').mockResolvedValue({ user: defaultUser });
    vi.spyOn(api, 'uploadProjectZip').mockResolvedValue({
      uploadId: 'upload-123',
      kind: 'project',
      status: 'ready',
      originalFilename: 'project.zip',
      extractedRoot: '/sandbox/uploads/upload-123',
    });

    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={['/']}>
        <App />
      </MemoryRouter>,
    );

    await screen.findByLabelText('上传项目 ZIP');

    const file = new File(['test'], 'project.zip', { type: 'application/zip' });
    const input = screen.getByLabelText('上传项目 ZIP') as HTMLInputElement;
    await user.upload(input, file);

    await waitFor(() => {
      expect(screen.getByText('project.zip')).toBeInTheDocument();
      expect(screen.getByText('已上传')).toBeInTheDocument();
    });

    await user.selectOptions(screen.getByTestId('java-version-select'), '17');

    expect(screen.getByRole('button', { name: '启动运行' })).not.toBeDisabled();
  });

  it('keeps the visible start button inside the upload source section after upload', async () => {
    vi.spyOn(api, 'fetchConfigDefaults').mockResolvedValue({ config: defaultConfig });
    vi.spyOn(api, 'getCurrentUser').mockResolvedValue({ user: defaultUser });
    vi.spyOn(api, 'uploadProjectZip').mockResolvedValue({
      uploadId: 'upload-123',
      kind: 'project',
      status: 'ready',
      originalFilename: 'project.zip',
      extractedRoot: '/sandbox/uploads/upload-123',
    });

    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={['/']}>
        <App />
      </MemoryRouter>,
    );

    await screen.findByLabelText('上传项目 ZIP');
    await user.upload(
      screen.getByLabelText('上传项目 ZIP') as HTMLInputElement,
      new File(['test'], 'project.zip', { type: 'application/zip' }),
    );
    await user.selectOptions(screen.getByTestId('java-version-select'), '17');

    const sourceSection = await screen.findByTestId('target-source-section');
    const startButton = await screen.findByRole('button', { name: '启动运行' });

    expect(sourceSection).toContainElement(startButton);
    expect(startButton).not.toBeDisabled();
  });

  it('uploads bug reports zip optionally', async () => {
    vi.spyOn(api, 'fetchConfigDefaults').mockResolvedValue({ config: defaultConfig });
    vi.spyOn(api, 'getCurrentUser').mockResolvedValue({ user: defaultUser });
    vi.spyOn(api, 'uploadProjectZip').mockResolvedValue({
      uploadId: 'upload-123',
      kind: 'project',
      status: 'ready',
      originalFilename: 'project.zip',
      extractedRoot: '/sandbox/uploads/upload-123',
    });
    vi.spyOn(api, 'uploadBugReportsZip').mockResolvedValue({
      uploadId: 'upload-456',
      kind: 'bug_reports',
      status: 'ready',
      originalFilename: 'bug-reports.zip',
      extractedRoot: '/sandbox/uploads/upload-456',
    });

    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={['/']}>
        <App />
      </MemoryRouter>,
    );

    await screen.findByLabelText('上传项目 ZIP');

    const projectFile = new File(['test'], 'project.zip', { type: 'application/zip' });
    const projectInput = screen.getByLabelText('上传项目 ZIP') as HTMLInputElement;
    await user.upload(projectInput, projectFile);

    await waitFor(() => {
      expect(screen.getByText('project.zip')).toBeInTheDocument();
    });

    const bugReportsFile = new File(['test'], 'bug-reports.zip', { type: 'application/zip' });
    const bugReportsInput = screen.getByLabelText('上传缺陷报告 ZIP') as HTMLInputElement;
    await user.upload(bugReportsInput, bugReportsFile);

    await waitFor(() => {
      expect(screen.getByText('bug-reports.zip')).toBeInTheDocument();
      expect(screen.getByText('缺陷报告 bug-reports.zip 已上传。')).toBeInTheDocument();
    });
  });

  it('submits run with upload IDs', async () => {
    vi.spyOn(api, 'fetchConfigDefaults').mockResolvedValue({ config: defaultConfig });
    vi.spyOn(api, 'getCurrentUser').mockResolvedValue({ user: defaultUser });
    vi.spyOn(api, 'uploadProjectZip').mockResolvedValue({
      uploadId: 'upload-123',
      kind: 'project',
      status: 'ready',
      originalFilename: 'project.zip',
      extractedRoot: '/sandbox/uploads/upload-123',
    });
    vi.spyOn(api, 'uploadBugReportsZip').mockResolvedValue({
      uploadId: 'upload-456',
      kind: 'bug_reports',
      status: 'ready',
      originalFilename: 'bug-reports.zip',
      extractedRoot: '/sandbox/uploads/upload-456',
    });
    const createRunSpy = vi.spyOn(api, 'createRun').mockResolvedValue({
      runId: 'run-upload-1',
      status: 'pending',
      mode: 'upload',
      queuePosition: 1,
    });
    vi.spyOn(api, 'fetchRunSnapshot').mockResolvedValue({
      runId: 'run-upload-1',
      status: 'pending',
      mode: 'upload',
      iteration: 0,
      llmCalls: 0,
      budget: 1000,
      decisionReasoning: null,
      currentTarget: null,
      previousTarget: null,
      recentImprovements: [],
      improvementSummary: { count: 0, latest: null },
      metrics: {
        mutationScore: 0,
        globalMutationScore: 0,
        lineCoverage: 0,
        branchCoverage: 0,
        totalTests: 0,
        totalMutants: 0,
        globalTotalMutants: 0,
        killedMutants: 0,
        globalKilledMutants: 0,
        survivedMutants: 0,
        globalSurvivedMutants: 0,
        currentMethodCoverage: null,
      },
      phase: { key: 'pending', label: 'Pending' },
      artifacts: {},
    });
    vi.spyOn(api, 'subscribeToRunEvents').mockReturnValue(() => {});

    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={['/']}>
        <App />
      </MemoryRouter>,
    );

    await screen.findByLabelText('上传项目 ZIP');

    const projectFile = new File(['test'], 'project.zip', { type: 'application/zip' });
    const projectInput = screen.getByLabelText('上传项目 ZIP') as HTMLInputElement;
    await user.upload(projectInput, projectFile);

    await waitFor(() => {
      expect(screen.getByText('project.zip')).toBeInTheDocument();
    });

    const bugReportsFile = new File(['test'], 'bug-reports.zip', { type: 'application/zip' });
    const bugReportsInput = screen.getByLabelText('上传缺陷报告 ZIP') as HTMLInputElement;
    await user.upload(bugReportsInput, bugReportsFile);

    await waitFor(() => {
      expect(screen.getByText('bug-reports.zip')).toBeInTheDocument();
    });

    await user.selectOptions(screen.getByTestId('java-version-select'), '17');

    await user.click(screen.getByRole('button', { name: '启动运行' }));

    await waitFor(() => {
      expect(createRunSpy).toHaveBeenCalledWith(
        expect.objectContaining({
          projectUploadId: 'upload-123',
          bugReportsUploadId: 'upload-456',
          selectedJavaVersion: '17',
          config: expect.any(Object),
        }),
      );
    });
  });

  it('shows validation error when project upload is missing', async () => {
    vi.spyOn(api, 'fetchConfigDefaults').mockResolvedValue({ config: defaultConfig });
    vi.spyOn(api, 'getCurrentUser').mockResolvedValue({ user: defaultUser });

    render(
      <MemoryRouter initialEntries={['/']}>
        <App />
      </MemoryRouter>,
    );

    await screen.findByLabelText('上传项目 ZIP');

    await userEvent.setup().selectOptions(screen.getByTestId('java-version-select'), '17');

    const submitButton = screen.getByRole('button', { name: '请先上传项目' });
    expect(submitButton).toBeDisabled();
  });

  it('requires Java version before submitting uploaded project', async () => {
    vi.spyOn(api, 'fetchConfigDefaults').mockResolvedValue({ config: defaultConfig });
    vi.spyOn(api, 'getCurrentUser').mockResolvedValue({ user: defaultUser });
    vi.spyOn(api, 'uploadProjectZip').mockResolvedValue({
      uploadId: 'upload-123',
      kind: 'project',
      status: 'ready',
      originalFilename: 'project.zip',
      extractedRoot: '/sandbox/uploads/upload-123',
    });

    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={['/']}>
        <App />
      </MemoryRouter>,
    );

    await screen.findByLabelText('上传项目 ZIP');
    await user.upload(
      screen.getByLabelText('上传项目 ZIP') as HTMLInputElement,
      new File(['test'], 'project.zip', { type: 'application/zip' }),
    );

    await waitFor(() => {
      expect(screen.getByText('project.zip')).toBeInTheDocument();
    });

    expect(screen.getByRole('button', { name: '请选择 Java 版本' })).toBeDisabled();
  });
});

describe('HomePage 示例项目 source mode', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('shows 示例项目 as an independent source option for ordinary users', async () => {
    vi.spyOn(api, 'fetchConfigDefaults').mockResolvedValue({ config: defaultConfig });
    vi.spyOn(api, 'getCurrentUser').mockResolvedValue({ user: defaultUser });
    mockExamplesEndpoint();

    render(
      <MemoryRouter initialEntries={['/']}>
        <App />
      </MemoryRouter>,
    );

    await screen.findByLabelText('上传项目 ZIP');

    expect(screen.getByRole('tab', { name: '上传项目' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: '示例项目' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'GitHub 仓库' })).toBeInTheDocument();
    expect(screen.queryByRole('tab', { name: '本地路径' })).not.toBeInTheDocument();
  });

  it('loads 计算器示例 and 多文件示例 from the examples endpoint without exposing a local path textbox', async () => {
    vi.spyOn(api, 'fetchConfigDefaults').mockResolvedValue({ config: defaultConfig });
    vi.spyOn(api, 'getCurrentUser').mockResolvedValue({ user: defaultUser });
    const fetchSpy = mockExamplesEndpoint();

    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={['/']}>
        <App />
      </MemoryRouter>,
    );

    await screen.findByLabelText('上传项目 ZIP');
    await user.click(screen.getByRole('tab', { name: '示例项目' }));

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith('/api/examples');
    });
    expect(screen.getByRole('button', { name: '计算器示例' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '多文件示例' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Mockito 示例' })).not.toBeInTheDocument();
    expect(screen.queryByLabelText('项目路径')).not.toBeInTheDocument();
  });

  it('disables submit for unavailable 示例项目 config and clears the error when switching source modes', async () => {
    vi.spyOn(api, 'fetchConfigDefaults').mockResolvedValue({ config: defaultConfig });
    vi.spyOn(api, 'getCurrentUser').mockResolvedValue({ user: defaultUser });
    mockExamplesEndpoint({
      projects: [
        {
          id: 'calculator-demo',
          label: '计算器示例',
          displayName: '计算器示例',
          description: '缺少示例配置的项目。',
        },
        {
          id: 'multi-file-demo',
          label: '多文件示例',
          displayName: '多文件示例',
          description: '可用示例项目。',
        },
      ],
      config: {
        available: false,
        defaults: null,
        error: '示例项目配置不可用：后端未生成 calculator-demo 配置。',
      },
    });

    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={['/']}>
        <App />
      </MemoryRouter>,
    );

    await screen.findByLabelText('上传项目 ZIP');
    await user.click(screen.getByRole('tab', { name: '示例项目' }));
    await user.click(await screen.findByRole('button', { name: '计算器示例' }));

    expect(
      await screen.findByText('示例项目配置不可用：后端未生成 calculator-demo 配置。'),
    ).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '启动运行' })).toBeDisabled();

    await user.click(screen.getByRole('tab', { name: '上传项目' }));
    expect(screen.queryByText(/示例项目配置不可用/)).not.toBeInTheDocument();

    await user.click(screen.getByRole('tab', { name: 'GitHub 仓库' }));
    expect(screen.queryByText(/示例项目配置不可用/)).not.toBeInTheDocument();
  });

  it('submits a minimal createRun payload for selected 示例项目 without local or github fields', async () => {
    vi.spyOn(api, 'fetchConfigDefaults').mockResolvedValue({ config: defaultConfig });
    vi.spyOn(api, 'getCurrentUser').mockResolvedValue({ user: defaultUser });
    mockExamplesEndpoint();
    const createRunSpy = vi.spyOn(api, 'createRun').mockResolvedValue({
      runId: 'run-example-1',
      status: 'created',
      mode: 'example',
    });
    vi.spyOn(api, 'fetchRunSnapshot').mockResolvedValue({
      runId: 'run-example-1',
      status: 'created',
      mode: 'example',
      iteration: 0,
      llmCalls: 0,
      budget: 1000,
      decisionReasoning: null,
      currentTarget: null,
      previousTarget: null,
      recentImprovements: [],
      improvementSummary: { count: 0, latest: null },
      metrics: {
        mutationScore: 0,
        globalMutationScore: 0,
        lineCoverage: 0,
        branchCoverage: 0,
        totalTests: 0,
        totalMutants: 0,
        globalTotalMutants: 0,
        killedMutants: 0,
        globalKilledMutants: 0,
        survivedMutants: 0,
        globalSurvivedMutants: 0,
        currentMethodCoverage: null,
      },
      phase: { key: 'queued', label: 'Queued' },
      artifacts: {},
    });
    vi.spyOn(api, 'subscribeToRunEvents').mockReturnValue(() => {});

    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={['/']}>
        <App />
      </MemoryRouter>,
    );

    await screen.findByLabelText('上传项目 ZIP');
    await user.click(screen.getByRole('tab', { name: '示例项目' }));
    await user.click(await screen.findByRole('button', { name: '计算器示例' }));
    await user.click(screen.getByRole('button', { name: '启动运行' }));

    await waitFor(() => {
      expect(createRunSpy).toHaveBeenCalled();
    });
    const submittedPayload = createRunSpy.mock.calls[0]?.[0] as Record<string, unknown>;
    expect(submittedPayload).toMatchObject({
      projectSourceType: 'example',
      exampleProjectId: 'calculator-demo',
    });
    expect(submittedPayload).not.toHaveProperty('config');
    expect(submittedPayload).not.toHaveProperty('projectPath');
    expect(submittedPayload).not.toHaveProperty('githubRepoUrl');
  });

  it('does not leak selected 示例项目 config into later upload submissions', async () => {
    vi.spyOn(api, 'fetchConfigDefaults').mockResolvedValue({ config: defaultConfig });
    vi.spyOn(api, 'getCurrentUser').mockResolvedValue({ user: defaultUser });
    mockExamplesEndpoint({
      projects: availableExamplesPayload.projects,
      config: {
        available: true,
        defaults: {
          ...defaultConfig,
          llm: {
            ...defaultConfig.llm,
            api_key: '[REDACTED]',
            model: 'example-only-model',
          },
          evolution: {
            ...defaultConfig.evolution,
            max_iterations: 99,
          },
        },
        error: null,
      },
    });
    vi.spyOn(api, 'uploadProjectZip').mockResolvedValue({
      uploadId: 'upload-after-example',
      kind: 'project',
      status: 'ready',
      originalFilename: 'project.zip',
      extractedRoot: '/sandbox/uploads/upload-after-example',
    });
    const createRunSpy = vi.spyOn(api, 'createRun').mockResolvedValue({
      runId: 'run-upload-after-example',
      status: 'pending',
      mode: 'upload',
    });
    vi.spyOn(api, 'fetchRunSnapshot').mockResolvedValue({
      runId: 'run-upload-after-example',
      status: 'pending',
      mode: 'upload',
      iteration: 0,
      llmCalls: 0,
      budget: 1000,
      decisionReasoning: null,
      currentTarget: null,
      previousTarget: null,
      recentImprovements: [],
      improvementSummary: { count: 0, latest: null },
      metrics: {
        mutationScore: 0,
        globalMutationScore: 0,
        lineCoverage: 0,
        branchCoverage: 0,
        totalTests: 0,
        totalMutants: 0,
        globalTotalMutants: 0,
        killedMutants: 0,
        globalKilledMutants: 0,
        survivedMutants: 0,
        globalSurvivedMutants: 0,
        currentMethodCoverage: null,
      },
      phase: { key: 'pending', label: 'Pending' },
      artifacts: {},
    });
    vi.spyOn(api, 'subscribeToRunEvents').mockReturnValue(() => {});

    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={['/']}>
        <App />
      </MemoryRouter>,
    );

    await screen.findByLabelText('上传项目 ZIP');
    await user.click(screen.getByRole('tab', { name: '示例项目' }));
    await user.click(await screen.findByRole('button', { name: '计算器示例' }));
    expect(screen.getByLabelText('模型')).toHaveValue('example-only-model');

    await user.click(screen.getByRole('tab', { name: '上传项目' }));
    await user.upload(
      screen.getByLabelText('上传项目 ZIP') as HTMLInputElement,
      new File(['test'], 'project.zip', { type: 'application/zip' }),
    );
    await user.selectOptions(screen.getByTestId('java-version-select'), '17');
    await user.click(screen.getByRole('button', { name: '启动运行' }));

    await waitFor(() => {
      expect(createRunSpy).toHaveBeenCalledWith(
        expect.objectContaining({
          projectUploadId: 'upload-after-example',
          selectedJavaVersion: '17',
          config: expect.objectContaining({
            llm: expect.objectContaining({
              api_key: 'default-key',
              model: 'gpt-4',
            }),
            evolution: expect.objectContaining({
              max_iterations: 10,
            }),
          }),
        }),
      );
    });
  });
});

describe('HomePage admin local path and GitHub modes', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('does not show local path tab for admins when the server disables local path mode', async () => {
    vi.spyOn(api, 'fetchConfigDefaults').mockResolvedValue({ config: defaultConfig });
    vi.spyOn(api, 'getCurrentUser').mockResolvedValue({ user: adminUser });

    render(
      <MemoryRouter initialEntries={['/']}>
        <App />
      </MemoryRouter>,
    );

    await screen.findByLabelText('上传项目 ZIP');

    expect(screen.getByRole('tab', { name: '上传项目' })).toBeInTheDocument();
    expect(screen.queryByRole('tab', { name: '本地路径' })).not.toBeInTheDocument();
    expect(screen.queryByLabelText('项目路径')).not.toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'GitHub 仓库' })).toBeInTheDocument();
  });

  it('shows local path and GitHub tabs for admins when the server enables local path mode', async () => {
    vi.spyOn(api, 'fetchConfigDefaults').mockResolvedValue({ config: localPathConfig });
    vi.spyOn(api, 'getCurrentUser').mockResolvedValue({ user: adminUser });

    render(
      <MemoryRouter initialEntries={['/']}>
        <App />
      </MemoryRouter>,
    );

    await screen.findByLabelText('上传项目 ZIP');

    expect(screen.getByRole('tab', { name: '上传项目' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: '本地路径' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'GitHub 仓库' })).toBeInTheDocument();
  });

  it('shows admin restriction notice in local path mode', async () => {
    vi.spyOn(api, 'fetchConfigDefaults').mockResolvedValue({ config: localPathConfig });
    vi.spyOn(api, 'getCurrentUser').mockResolvedValue({ user: adminUser });

    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={['/']}>
        <App />
      </MemoryRouter>,
    );

    await screen.findByLabelText('上传项目 ZIP');

    const localTab = screen.getByRole('tab', { name: '本地路径' });
    await user.click(localTab);

    await waitFor(() => {
      expect(screen.getByText('本地路径模式仅限管理员使用。服务端限制。')).toBeInTheDocument();
    });
    expect(screen.getByLabelText('项目路径')).toBeInTheDocument();
  });

  it('renders visible server config policy annotations with service-side limit copy', async () => {
    vi.spyOn(api, 'fetchConfigDefaults').mockResolvedValue({
      config: defaultConfig,
      configPolicy: {
        overriddenFields: ['preprocessing.max_workers'],
        clampedFields: ['evolution.budget_llm_calls'],
        redactedFields: ['llm.api_key'],
      },
    });
    vi.spyOn(api, 'getCurrentUser').mockResolvedValue({ user: adminUser });

    render(
      <MemoryRouter initialEntries={['/']}>
        <App />
      </MemoryRouter>,
    );

    await screen.findByLabelText('上传项目 ZIP');

    expect(screen.getByText('此字段由服务端固定，提交时会使用后端值。')).toBeInTheDocument();
    expect(screen.getByText('超过部署上限时会由服务端自动收紧。')).toBeInTheDocument();
    expect(screen.queryByText('敏感值已隐藏，不会在前端显示。')).not.toBeInTheDocument();
  });

  it('preserves uploaded secret values when hydrating uploaded config', async () => {
    vi.spyOn(api, 'fetchConfigDefaults').mockResolvedValue({ config: defaultConfig });
    vi.spyOn(api, 'getCurrentUser').mockResolvedValue({ user: adminUser });
    vi.spyOn(api, 'parseConfigFile').mockResolvedValue({
      config: {
        ...defaultConfig,
        llm: {
          ...defaultConfig.llm,
          api_key: 'uploaded-llm-key',
          max_tokens: 8192,
        },
        knowledge: {
          ...defaultConfig.knowledge,
          embedding: {
            ...defaultConfig.knowledge.embedding,
            api_key: 'uploaded-embedding-key',
          },
        },
      },
      configPolicy: {},
    });

    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={['/']}>
        <App />
      </MemoryRouter>,
    );

    await screen.findByLabelText('上传项目 ZIP');

    const file = new File(['llm:\n  api_key: [REDACTED]\n'], 'config.yaml', {
      type: 'application/x-yaml',
    });
    await user.upload(screen.getByLabelText('上传 YAML'), file);

    await waitFor(() => {
      expect(screen.getByLabelText('API 密钥')).toHaveValue('uploaded-llm-key');
      expect(screen.getByLabelText('最大令牌数')).toHaveValue(8192);
      expect(screen.getByLabelText('嵌入 API 密钥')).toHaveValue('uploaded-embedding-key');
    });
  });

  it('does not show an admin restriction notice in GitHub mode', async () => {
    vi.spyOn(api, 'fetchConfigDefaults').mockResolvedValue({ config: defaultConfig });
    vi.spyOn(api, 'getCurrentUser').mockResolvedValue({ user: defaultUser });
    vi.spyOn(api, 'fetchGitHubAuthStatus').mockResolvedValue({
      connected: false,
      requiresReauth: false,
    });

    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={['/']}>
        <App />
      </MemoryRouter>,
    );

    await screen.findByLabelText('上传项目 ZIP');

    const githubTab = screen.getByRole('tab', { name: 'GitHub 仓库' });
    await user.click(githubTab);

    await waitFor(() => {
      expect(screen.getByText('未连接')).toBeInTheDocument();
    });
    expect(screen.queryByText(/GitHub.*仅限管理员/)).not.toBeInTheDocument();
  });
});

describe('HomePage GitHub auth flow', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(api, 'getCurrentUser').mockResolvedValue({ user: adminUser });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('shows GitHub connect button when not connected', async () => {
    vi.spyOn(api, 'fetchConfigDefaults').mockResolvedValue({ config: defaultConfig });
    vi.spyOn(api, 'fetchGitHubAuthStatus').mockResolvedValue({
      connected: false,
      requiresReauth: false,
    });

    render(
      <MemoryRouter initialEntries={['/']}>
        <App />
      </MemoryRouter>,
    );

    await screen.findByLabelText('上传项目 ZIP');

    const githubTab = screen.getByRole('tab', { name: 'GitHub 仓库' });
    await userEvent.click(githubTab);

    expect(screen.getByTestId('github-connect-button')).toBeInTheDocument();
    expect(screen.getByText('未连接')).toBeInTheDocument();
    expect(screen.getByText('请连接 GitHub 账户以使用仓库模式。')).toBeInTheDocument();
  });

  it('shows disconnect button when connected', async () => {
    vi.spyOn(api, 'fetchConfigDefaults').mockResolvedValue({ config: defaultConfig });
    vi.spyOn(api, 'fetchGitHubAuthStatus').mockResolvedValue({
      connected: true,
      username: 'testuser',
      requiresReauth: false,
    });

    render(
      <MemoryRouter initialEntries={['/']}>
        <App />
      </MemoryRouter>,
    );

    await screen.findByLabelText('上传项目 ZIP');

    const githubTab = screen.getByRole('tab', { name: 'GitHub 仓库' });
    await userEvent.click(githubTab);

    expect(screen.getByTestId('disconnect-github-button')).toBeInTheDocument();
    expect(screen.getByText('已连接')).toBeInTheDocument();
    expect(screen.getByText('testuser')).toBeInTheDocument();
  });

  it('shows reauth warning when token requires reauth', async () => {
    vi.spyOn(api, 'fetchConfigDefaults').mockResolvedValue({ config: defaultConfig });
    vi.spyOn(api, 'fetchGitHubAuthStatus').mockResolvedValue({
      connected: false,
      requiresReauth: true,
    });

    render(
      <MemoryRouter initialEntries={['/']}>
        <App />
      </MemoryRouter>,
    );

    await screen.findByLabelText('上传项目 ZIP');

    const githubTab = screen.getByRole('tab', { name: 'GitHub 仓库' });
    await userEvent.click(githubTab);

    expect(screen.getByText('需重新授权')).toBeInTheDocument();
    expect(screen.getByText('授权已过期或失效，请重新连接。')).toBeInTheDocument();
    expect(screen.getByTestId('github-connect-button')).toBeInTheDocument();
  });

  it('disables repo inputs when not connected', async () => {
    vi.spyOn(api, 'fetchConfigDefaults').mockResolvedValue({ config: defaultConfig });
    vi.spyOn(api, 'fetchGitHubAuthStatus').mockResolvedValue({
      connected: false,
      requiresReauth: false,
    });
    vi.spyOn(api, 'fetchGitHubRepositories').mockResolvedValue({ repositories: [] });

    render(
      <MemoryRouter initialEntries={['/']}>
        <App />
      </MemoryRouter>,
    );

    await screen.findByLabelText('上传项目 ZIP');

    const githubTab = screen.getByRole('tab', { name: 'GitHub 仓库' });
    await userEvent.click(githubTab);

    expect(screen.getByTestId('repo-picker-filter')).toBeDisabled();
    expect(screen.getByTestId('java-version-select')).toBeDisabled();
    expect(screen.getByRole('button', { name: '请先连接 GitHub' })).toBeDisabled();
  });

  it('enables repo inputs when connected', async () => {
    vi.spyOn(api, 'fetchConfigDefaults').mockResolvedValue({ config: defaultConfig });
    vi.spyOn(api, 'fetchGitHubAuthStatus').mockResolvedValue({
      connected: true,
      username: 'testuser',
      requiresReauth: false,
    });
    vi.spyOn(api, 'fetchGitHubRepositories').mockResolvedValue({
      repositories: [
        {
          name: 'test-repo',
          fullName: 'testuser/test-repo',
          url: 'https://github.com/testuser/test-repo',
          description: 'A test repository',
          private: false,
          updatedAt: '2024-01-15T10:30:00Z',
        },
      ],
    });

    render(
      <MemoryRouter initialEntries={['/']}>
        <App />
      </MemoryRouter>,
    );

    await screen.findByLabelText('上传项目 ZIP');

    const githubTab = screen.getByRole('tab', { name: 'GitHub 仓库' });
    await userEvent.click(githubTab);

    await screen.findByTestId('repo-picker-filter');
    expect(screen.getByTestId('repo-picker-filter')).not.toBeDisabled();
    expect(screen.getByTestId('java-version-select')).not.toBeDisabled();
    expect(screen.getByRole('button', { name: '启动运行' })).not.toBeDisabled();
  });

  it('disconnects GitHub and updates status', async () => {
    vi.spyOn(api, 'fetchConfigDefaults').mockResolvedValue({ config: defaultConfig });
    vi.spyOn(api, 'fetchGitHubAuthStatus').mockResolvedValue({
      connected: true,
      username: 'testuser',
      requiresReauth: false,
    });
    vi.spyOn(api, 'disconnectGitHubAuth').mockResolvedValue();
    vi.spyOn(api, 'fetchGitHubRepositories').mockResolvedValue({ repositories: [] });

    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={['/']}>
        <App />
      </MemoryRouter>,
    );

    await screen.findByLabelText('上传项目 ZIP');

    const githubTab = screen.getByRole('tab', { name: 'GitHub 仓库' });
    await user.click(githubTab);

    const disconnectButton = screen.getByTestId('disconnect-github-button');
    await user.click(disconnectButton);

    await waitFor(() => {
      expect(screen.getByText('未连接')).toBeInTheDocument();
    });
    expect(screen.getByTestId('github-connect-button')).toBeInTheDocument();
  });

  it('submits GitHub repo run with required fields', async () => {
    vi.spyOn(api, 'fetchConfigDefaults').mockResolvedValue({ config: defaultConfig });
    vi.spyOn(api, 'fetchGitHubAuthStatus').mockResolvedValue({
      connected: true,
      username: 'testuser',
      requiresReauth: false,
    });
    vi.spyOn(api, 'fetchGitHubRepositories').mockResolvedValue({
      repositories: [
        {
          name: 'test-repo',
          fullName: 'testuser/test-repo',
          url: 'https://github.com/testuser/test-repo',
          description: 'A test repository',
          private: false,
          updatedAt: '2024-01-15T10:30:00Z',
        },
      ],
    });
    const createRunSpy = vi.spyOn(api, 'createRun').mockResolvedValue({
      runId: 'run-123',
      status: 'created',
      mode: 'github',
    });
    vi.spyOn(api, 'fetchRunSnapshot').mockResolvedValue({
      runId: 'run-123',
      status: 'created',
      mode: 'github',
      iteration: 0,
      llmCalls: 0,
      budget: 1000,
      decisionReasoning: null,
      currentTarget: null,
      previousTarget: null,
      recentImprovements: [],
      improvementSummary: { count: 0, latest: null },
      metrics: {
        mutationScore: 0,
        globalMutationScore: 0,
        lineCoverage: 0,
        branchCoverage: 0,
        totalTests: 0,
        totalMutants: 0,
        globalTotalMutants: 0,
        killedMutants: 0,
        globalKilledMutants: 0,
        survivedMutants: 0,
        globalSurvivedMutants: 0,
        currentMethodCoverage: null,
      },
      phase: { key: 'queued', label: 'Queued' },
      artifacts: {},
    });
    vi.spyOn(api, 'subscribeToRunEvents').mockReturnValue(() => {});

    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={['/']}>
        <App />
      </MemoryRouter>,
    );

    await screen.findByLabelText('上传项目 ZIP');

    const githubTab = screen.getByRole('tab', { name: 'GitHub 仓库' });
    await user.click(githubTab);

    await screen.findByTestId('repo-item-testuser/test-repo');
    await user.click(screen.getByTestId('repo-item-testuser/test-repo'));
    await user.selectOptions(screen.getByTestId('java-version-select'), '17');

    await user.click(screen.getByRole('button', { name: '启动运行' }));

    await waitFor(() => {
      expect(createRunSpy).toHaveBeenCalledWith(
        expect.objectContaining({
          projectPath: '',
          githubRepoUrl: 'https://github.com/testuser/test-repo',
          selectedJavaVersion: '17',
          config: expect.any(Object),
        }),
      );
    });
  });

  it('shows validation error when repo URL is missing', async () => {
    vi.spyOn(api, 'fetchConfigDefaults').mockResolvedValue({ config: defaultConfig });
    vi.spyOn(api, 'fetchGitHubAuthStatus').mockResolvedValue({
      connected: true,
      username: 'testuser',
      requiresReauth: false,
    });
    vi.spyOn(api, 'fetchGitHubRepositories').mockResolvedValue({
      repositories: [
        {
          name: 'test-repo',
          fullName: 'testuser/test-repo',
          url: 'https://github.com/testuser/test-repo',
          description: 'A test repository',
          private: false,
          updatedAt: '2024-01-15T10:30:00Z',
        },
      ],
    });

    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={['/']}>
        <App />
      </MemoryRouter>,
    );

    await screen.findByLabelText('上传项目 ZIP');

    const githubTab = screen.getByRole('tab', { name: 'GitHub 仓库' });
    await user.click(githubTab);

    await screen.findByTestId('repo-picker-filter');
    await user.selectOptions(screen.getByTestId('java-version-select'), '17');
    await user.click(screen.getByRole('button', { name: '启动运行' }));

    await waitFor(() => {
      expect(screen.getByText('请输入 GitHub 仓库 URL。')).toBeInTheDocument();
    });
  });

  it('shows validation error when Java version is missing', async () => {
    vi.spyOn(api, 'fetchConfigDefaults').mockResolvedValue({ config: defaultConfig });
    vi.spyOn(api, 'fetchGitHubAuthStatus').mockResolvedValue({
      connected: true,
      username: 'testuser',
      requiresReauth: false,
    });
    vi.spyOn(api, 'fetchGitHubRepositories').mockResolvedValue({
      repositories: [
        {
          name: 'test-repo',
          fullName: 'testuser/test-repo',
          url: 'https://github.com/testuser/test-repo',
          description: 'A test repository',
          private: false,
          updatedAt: '2024-01-15T10:30:00Z',
        },
      ],
    });

    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={['/']}>
        <App />
      </MemoryRouter>,
    );

    await screen.findByLabelText('上传项目 ZIP');

    const githubTab = screen.getByRole('tab', { name: 'GitHub 仓库' });
    await user.click(githubTab);

    await screen.findByTestId('repo-item-testuser/test-repo');
    await user.click(screen.getByTestId('repo-item-testuser/test-repo'));
    await user.click(screen.getByRole('button', { name: '启动运行' }));

    await waitFor(() => {
      expect(screen.getByText('请选择目标 Java 版本。')).toBeInTheDocument();
    });
  });

  it('does not allow manual target java home to replace selected Java version', async () => {
    vi.spyOn(api, 'fetchConfigDefaults').mockResolvedValue({ config: defaultConfig });
    vi.spyOn(api, 'fetchGitHubAuthStatus').mockResolvedValue({
      connected: true,
      username: 'testuser',
      requiresReauth: false,
    });
    vi.spyOn(api, 'fetchGitHubRepositories').mockResolvedValue({
      repositories: [
        {
          name: 'test-repo',
          fullName: 'testuser/test-repo',
          url: 'https://github.com/testuser/test-repo',
          description: 'A test repository',
          private: false,
          updatedAt: '2024-01-15T10:30:00Z',
        },
      ],
    });
    const createRunSpy = vi.spyOn(api, 'createRun').mockResolvedValue({
      runId: 'run-123',
      status: 'created',
      mode: 'github',
    });
    vi.spyOn(api, 'fetchRunSnapshot').mockResolvedValue({
      runId: 'run-123',
      status: 'created',
      mode: 'github',
      iteration: 0,
      llmCalls: 0,
      budget: 1000,
      decisionReasoning: null,
      currentTarget: null,
      previousTarget: null,
      recentImprovements: [],
      improvementSummary: { count: 0, latest: null },
      metrics: {
        mutationScore: 0,
        globalMutationScore: 0,
        lineCoverage: 0,
        branchCoverage: 0,
        totalTests: 0,
        totalMutants: 0,
        globalTotalMutants: 0,
        killedMutants: 0,
        globalKilledMutants: 0,
        survivedMutants: 0,
        globalSurvivedMutants: 0,
        currentMethodCoverage: null,
      },
      phase: { key: 'queued', label: 'Queued' },
      artifacts: {},
    });
    vi.spyOn(api, 'subscribeToRunEvents').mockReturnValue(() => {});

    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={['/']}>
        <App />
      </MemoryRouter>,
    );

    await screen.findByLabelText('上传项目 ZIP');

    const githubTab = screen.getByRole('tab', { name: 'GitHub 仓库' });
    await user.click(githubTab);

    await screen.findByTestId('repo-item-testuser/test-repo');
    await user.click(screen.getByTestId('repo-item-testuser/test-repo'));
    expect(screen.queryByLabelText('目标项目 Java 目录')).not.toBeInTheDocument();
    await user.selectOptions(screen.getByTestId('java-version-select'), '17');

    await user.click(screen.getByRole('button', { name: '启动运行' }));

    await waitFor(() => {
      expect(createRunSpy).toHaveBeenCalledWith(
        expect.objectContaining({
          selectedJavaVersion: '17',
        }),
      );
    });
  });

  it('handles OAuth callback success and updates auth status', async () => {
    vi.spyOn(api, 'fetchConfigDefaults').mockResolvedValue({ config: defaultConfig });
    vi.spyOn(api, 'fetchGitHubAuthStatus').mockResolvedValue({
      connected: false,
      requiresReauth: false,
    });
    vi.spyOn(api, 'handleGitHubAuthCallback').mockResolvedValue({
      provider: 'github-oauth-app',
      connected: true,
      requiresReauth: false,
      message: 'GitHub 已连接。',
    });

    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={['/?code=test-code&state=test-state']}>
        <App />
      </MemoryRouter>,
    );

    await screen.findByLabelText('上传项目 ZIP');

    const githubTab = screen.getByRole('tab', { name: 'GitHub 仓库' });
    await user.click(githubTab);

    await waitFor(() => {
      expect(screen.getByText('已连接')).toBeInTheDocument();
    });
    expect(screen.queryByText('未连接')).not.toBeInTheDocument();
  });

  it('handles OAuth callback failure and shows error', async () => {
    vi.spyOn(api, 'fetchConfigDefaults').mockResolvedValue({ config: defaultConfig });
    vi.spyOn(api, 'fetchGitHubAuthStatus').mockResolvedValue({
      connected: false,
      requiresReauth: false,
    });
    vi.spyOn(api, 'handleGitHubAuthCallback').mockResolvedValue({
      provider: 'github-oauth-app',
      connected: false,
      requiresReauth: true,
      message: 'GitHub 授权已失效，请重新授权。',
    });

    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={['/?code=test-code&state=test-state']}>
        <App />
      </MemoryRouter>,
    );

    await screen.findByLabelText('上传项目 ZIP');

    const githubTab = screen.getByRole('tab', { name: 'GitHub 仓库' });
    await user.click(githubTab);

    await waitFor(() => {
      expect(screen.getByText('需重新授权')).toBeInTheDocument();
    });
    expect(screen.getByTestId('github-connect-button')).toBeInTheDocument();
  });

  it('syncs auth status after browser callback redirect success', async () => {
    vi.spyOn(api, 'fetchConfigDefaults').mockResolvedValue({ config: defaultConfig });
    vi.spyOn(api, 'fetchGitHubAuthStatus')
      .mockResolvedValueOnce({
        connected: false,
        requiresReauth: false,
      })
      .mockResolvedValueOnce({
        connected: true,
        requiresReauth: false,
      });

    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={['/?github_oauth=connected']}>
        <App />
      </MemoryRouter>,
    );

    await screen.findByLabelText('上传项目 ZIP');

    const githubTab = screen.getByRole('tab', { name: 'GitHub 仓库' });
    await user.click(githubTab);

    await waitFor(() => {
      expect(screen.getByText('已连接')).toBeInTheDocument();
    });
    expect(screen.queryByText('未连接')).not.toBeInTheDocument();
  });

  it('shows callback error after browser redirect failure', async () => {
    vi.spyOn(api, 'fetchConfigDefaults').mockResolvedValue({ config: defaultConfig });
    vi.spyOn(api, 'fetchGitHubAuthStatus')
      .mockResolvedValueOnce({
        connected: false,
        requiresReauth: false,
      })
      .mockResolvedValueOnce({
        connected: false,
        requiresReauth: true,
        message: 'GitHub 授权已失效，请重新授权。',
      });

    const user = userEvent.setup();
    render(
      <MemoryRouter
        initialEntries={[
          '/?github_oauth=error&message=OAuth%20%E5%9B%9E%E8%B0%83%E7%8A%B6%E6%80%81%E6%97%A0%E6%95%88',
        ]}
      >
        <App />
      </MemoryRouter>,
    );

    await screen.findByLabelText('上传项目 ZIP');

    const githubTab = screen.getByRole('tab', { name: 'GitHub 仓库' });
    await user.click(githubTab);

    await waitFor(() => {
      expect(screen.getByText('需重新授权')).toBeInTheDocument();
    });
    expect(screen.getByText('OAuth 回调状态无效')).toBeInTheDocument();
  });

  it('shows cancellation message after browser redirect cancel', async () => {
    vi.spyOn(api, 'fetchConfigDefaults').mockResolvedValue({ config: defaultConfig });
    vi.spyOn(api, 'fetchGitHubAuthStatus')
      .mockResolvedValueOnce({
        connected: false,
        requiresReauth: false,
      })
      .mockResolvedValueOnce({
        connected: false,
        requiresReauth: false,
        message: '尚未连接 GitHub。',
      });

    const user = userEvent.setup();
    render(
      <MemoryRouter
        initialEntries={[
          '/?github_oauth=error&message=GitHub%20%E6%8E%88%E6%9D%83%E5%B7%B2%E5%8F%96%E6%B6%88%EF%BC%8C%E8%AF%B7%E9%87%8D%E6%96%B0%E5%8F%91%E8%B5%B7%E6%8E%88%E6%9D%83%E3%80%82',
        ]}
      >
        <App />
      </MemoryRouter>,
    );

    await screen.findByLabelText('上传项目 ZIP');

    const githubTab = screen.getByRole('tab', { name: 'GitHub 仓库' });
    await user.click(githubTab);

    await waitFor(() => {
      expect(screen.getByText('GitHub 授权已取消，请重新发起授权。')).toBeInTheDocument();
    });
  });

  it('does not show free-text Java version field in config sections', async () => {
    vi.spyOn(api, 'fetchConfigDefaults').mockResolvedValue({ config: defaultConfig });
    vi.spyOn(api, 'fetchGitHubAuthStatus').mockResolvedValue({
      connected: false,
      requiresReauth: false,
    });

    render(
      <MemoryRouter initialEntries={['/']}>
        <App />
      </MemoryRouter>,
    );

    await screen.findByLabelText('上传项目 ZIP');

    expect(screen.queryByLabelText('目标项目 Java 版本')).not.toBeInTheDocument();
    expect(screen.queryByPlaceholderText('8 | 11 | 17 | 21 | 25')).not.toBeInTheDocument();
  });

  it('shows loading state while fetching repositories', async () => {
    vi.spyOn(api, 'fetchConfigDefaults').mockResolvedValue({ config: defaultConfig });
    vi.spyOn(api, 'fetchGitHubAuthStatus').mockResolvedValue({
      connected: true,
      username: 'testuser',
      requiresReauth: false,
    });
    vi.spyOn(api, 'fetchGitHubRepositories').mockImplementation(
      () => new Promise((resolve) => setTimeout(() => resolve({ repositories: [] }), 100)),
    );

    render(
      <MemoryRouter initialEntries={['/']}>
        <App />
      </MemoryRouter>,
    );

    await screen.findByLabelText('上传项目 ZIP');

    const githubTab = screen.getByRole('tab', { name: 'GitHub 仓库' });
    await userEvent.click(githubTab);

    expect(screen.getByText('正在加载仓库列表...')).toBeInTheDocument();
  });

  it('shows empty state when no repositories available', async () => {
    vi.spyOn(api, 'fetchConfigDefaults').mockResolvedValue({ config: defaultConfig });
    vi.spyOn(api, 'fetchGitHubAuthStatus').mockResolvedValue({
      connected: true,
      username: 'testuser',
      requiresReauth: false,
    });
    vi.spyOn(api, 'fetchGitHubRepositories').mockResolvedValue({ repositories: [] });

    render(
      <MemoryRouter initialEntries={['/']}>
        <App />
      </MemoryRouter>,
    );

    await screen.findByLabelText('上传项目 ZIP');

    const githubTab = screen.getByRole('tab', { name: 'GitHub 仓库' });
    await userEvent.click(githubTab);

    await waitFor(() => {
      expect(screen.getByText('暂无可用仓库')).toBeInTheDocument();
    });
  });

  it('filters repositories by search query', async () => {
    vi.spyOn(api, 'fetchConfigDefaults').mockResolvedValue({ config: defaultConfig });
    vi.spyOn(api, 'fetchGitHubAuthStatus').mockResolvedValue({
      connected: true,
      username: 'testuser',
      requiresReauth: false,
    });
    vi.spyOn(api, 'fetchGitHubRepositories').mockResolvedValue({
      repositories: [
        {
          name: 'alpha-project',
          fullName: 'testuser/alpha-project',
          url: 'https://github.com/testuser/alpha-project',
          description: 'Alpha project',
          private: false,
          updatedAt: '2024-01-15T10:30:00Z',
        },
        {
          name: 'beta-project',
          fullName: 'testuser/beta-project',
          url: 'https://github.com/testuser/beta-project',
          description: 'Beta project',
          private: true,
          updatedAt: '2024-01-16T10:30:00Z',
        },
      ],
    });

    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={['/']}>
        <App />
      </MemoryRouter>,
    );

    await screen.findByLabelText('上传项目 ZIP');

    const githubTab = screen.getByRole('tab', { name: 'GitHub 仓库' });
    await user.click(githubTab);

    await screen.findByTestId('repo-item-testuser/alpha-project');
    await screen.findByTestId('repo-item-testuser/beta-project');

    await user.type(screen.getByTestId('repo-picker-filter'), 'alpha');

    await waitFor(() => {
      expect(screen.getByTestId('repo-item-testuser/alpha-project')).toBeInTheDocument();
      expect(screen.queryByTestId('repo-item-testuser/beta-project')).not.toBeInTheDocument();
    });
  });

  it('shows private badge for private repositories', async () => {
    vi.spyOn(api, 'fetchConfigDefaults').mockResolvedValue({ config: defaultConfig });
    vi.spyOn(api, 'fetchGitHubAuthStatus').mockResolvedValue({
      connected: true,
      username: 'testuser',
      requiresReauth: false,
    });
    vi.spyOn(api, 'fetchGitHubRepositories').mockResolvedValue({
      repositories: [
        {
          name: 'private-repo',
          fullName: 'testuser/private-repo',
          url: 'https://github.com/testuser/private-repo',
          description: 'A private repository',
          private: true,
          updatedAt: '2024-01-15T10:30:00Z',
        },
      ],
    });

    render(
      <MemoryRouter initialEntries={['/']}>
        <App />
      </MemoryRouter>,
    );

    await screen.findByLabelText('上传项目 ZIP');

    const githubTab = screen.getByRole('tab', { name: 'GitHub 仓库' });
    await userEvent.click(githubTab);

    await screen.findByTestId('repo-item-testuser/private-repo');
    expect(screen.getByText('私有')).toBeInTheDocument();
  });

  it('highlights selected repository', async () => {
    vi.spyOn(api, 'fetchConfigDefaults').mockResolvedValue({ config: defaultConfig });
    vi.spyOn(api, 'fetchGitHubAuthStatus').mockResolvedValue({
      connected: true,
      username: 'testuser',
      requiresReauth: false,
    });
    vi.spyOn(api, 'fetchGitHubRepositories').mockResolvedValue({
      repositories: [
        {
          name: 'test-repo',
          fullName: 'testuser/test-repo',
          url: 'https://github.com/testuser/test-repo',
          description: 'A test repository',
          private: false,
          updatedAt: '2024-01-15T10:30:00Z',
        },
      ],
    });

    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={['/']}>
        <App />
      </MemoryRouter>,
    );

    await screen.findByLabelText('上传项目 ZIP');

    const githubTab = screen.getByRole('tab', { name: 'GitHub 仓库' });
    await user.click(githubTab);

    const repoItem = await screen.findByTestId('repo-item-testuser/test-repo');
    await user.click(repoItem);

    expect(repoItem).toHaveClass('repo-picker__item--selected');
  });

  it('fetches repositories after OAuth callback success', async () => {
    vi.spyOn(api, 'fetchConfigDefaults').mockResolvedValue({ config: defaultConfig });
    vi.spyOn(api, 'fetchGitHubAuthStatus')
      .mockResolvedValueOnce({
        connected: false,
        requiresReauth: false,
      })
      .mockResolvedValueOnce({
        connected: true,
        requiresReauth: false,
      });
    vi.spyOn(api, 'handleGitHubAuthCallback').mockResolvedValue({
      provider: 'github-oauth-app',
      connected: true,
      requiresReauth: false,
      message: 'GitHub 已连接。',
    });
    const fetchReposSpy = vi.spyOn(api, 'fetchGitHubRepositories').mockResolvedValue({
      repositories: [
        {
          name: 'test-repo',
          fullName: 'testuser/test-repo',
          url: 'https://github.com/testuser/test-repo',
          description: 'A test repository',
          private: false,
          updatedAt: '2024-01-15T10:30:00Z',
        },
      ],
    });

    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={['/?code=test-code&state=test-state']}>
        <App />
      </MemoryRouter>,
    );

    await screen.findByLabelText('上传项目 ZIP');

    const githubTab = screen.getByRole('tab', { name: 'GitHub 仓库' });
    await user.click(githubTab);

    await waitFor(() => {
      expect(screen.getByText('已连接')).toBeInTheDocument();
    });

    await waitFor(() => {
      expect(fetchReposSpy).toHaveBeenCalled();
    });
  });
});
