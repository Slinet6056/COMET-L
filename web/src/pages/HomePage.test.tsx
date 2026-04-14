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
};

describe('HomePage GitHub auth flow', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
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

    await screen.findByLabelText('项目路径');

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

    await screen.findByLabelText('项目路径');

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

    await screen.findByLabelText('项目路径');

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

    await screen.findByLabelText('项目路径');

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

    await screen.findByLabelText('项目路径');

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

    await screen.findByLabelText('项目路径');

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

    await screen.findByLabelText('项目路径');

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

    await screen.findByLabelText('项目路径');

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

    await screen.findByLabelText('项目路径');

    const githubTab = screen.getByRole('tab', { name: 'GitHub 仓库' });
    await user.click(githubTab);

    await screen.findByTestId('repo-item-testuser/test-repo');
    await user.click(screen.getByTestId('repo-item-testuser/test-repo'));
    await user.click(screen.getByRole('button', { name: '启动运行' }));

    await waitFor(() => {
      expect(screen.getByText('请选择目标 Java 版本。')).toBeInTheDocument();
    });
  });

  it('allows manual target java home to replace selected Java version', async () => {
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

    await screen.findByLabelText('项目路径');

    const githubTab = screen.getByRole('tab', { name: 'GitHub 仓库' });
    await user.click(githubTab);

    await screen.findByTestId('repo-item-testuser/test-repo');
    await user.click(screen.getByTestId('repo-item-testuser/test-repo'));
    await user.type(screen.getByLabelText('目标项目 Java 目录'), '/custom/jdk');

    await user.click(screen.getByRole('button', { name: '启动运行' }));

    await waitFor(() => {
      expect(createRunSpy).toHaveBeenCalledWith(
        expect.objectContaining({
          selectedJavaVersion: null,
          config: expect.objectContaining({
            execution: expect.objectContaining({
              target_java_home: '/custom/jdk',
            }),
          }),
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

    await screen.findByLabelText('项目路径');

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

    await screen.findByLabelText('项目路径');

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

    await screen.findByLabelText('项目路径');

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

    await screen.findByLabelText('项目路径');

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

    await screen.findByLabelText('项目路径');

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

    await screen.findByLabelText('项目路径');

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

    await screen.findByLabelText('项目路径');

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

    await screen.findByLabelText('项目路径');

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

    await screen.findByLabelText('项目路径');

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

    await screen.findByLabelText('项目路径');

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

    await screen.findByLabelText('项目路径');

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

    await screen.findByLabelText('项目路径');

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
