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
    maven_home: null,
  },
  paths: {
    cache: './cache',
    output: './output',
    sandbox: './sandbox',
  },
  evolution: {
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
    vector_db: {
      type: 'chromadb',
      persist_directory: './cache/chromadb',
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

describe('Config page', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('uploads YAML and backfills the form with parsed config', async () => {
    vi.spyOn(api, 'fetchConfigDefaults').mockResolvedValue({ config: defaultConfig });
    vi.spyOn(api, 'parseConfigFile').mockResolvedValue({
      config: {
        ...defaultConfig,
        llm: {
          ...defaultConfig.llm,
          api_key: 'yaml-key',
          model: 'gpt-4o-mini',
        },
        evolution: {
          ...defaultConfig.evolution,
          max_iterations: 7,
        },
      },
    });

    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={['/']}>
        <App />
      </MemoryRouter>,
    );

    const apiKeyInput = await screen.findByLabelText('API 密钥');
    expect(apiKeyInput).toHaveValue('default-key');

    const uploadInput = screen.getByLabelText('上传 YAML');
    const file = new File(['llm:\n  api_key: yaml-key\n'], 'config.yaml', {
      type: 'application/x-yaml',
    });

    await user.upload(uploadInput, file);

    await waitFor(() => {
      expect(screen.getByLabelText('API 密钥')).toHaveValue('yaml-key');
    });
    expect(screen.getByLabelText('模型')).toHaveValue('gpt-4o-mini');
    expect(screen.getByLabelText('最大迭代次数')).toHaveValue(7);
    expect(screen.getByText('config.yaml 已解析并回填到表单中。')).toBeInTheDocument();
  });

  it('shows backend project path validation errors', async () => {
    vi.spyOn(api, 'fetchConfigDefaults').mockResolvedValue({ config: defaultConfig });
    vi.spyOn(api, 'createRun').mockRejectedValue(
      new api.ApiError(422, {
        error: {
          code: 'invalid_project_path',
          message: 'Project path validation failed.',
          fieldErrors: [
            {
              path: ['projectPath'],
              code: 'path_not_found',
              message: 'Project path does not exist.',
            },
          ],
        },
      }),
    );

    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={['/']}>
        <App />
      </MemoryRouter>,
    );

    await screen.findByLabelText('项目路径');
    await user.type(screen.getByLabelText('项目路径'), '/tmp/missing-project');
    await user.click(screen.getByRole('button', { name: '启动运行' }));

    expect(await screen.findByText('Project path does not exist.')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '运行配置首页' })).toBeInTheDocument();
  });

  it('submits config and navigates to the created run page', async () => {
    vi.spyOn(api, 'fetchConfigDefaults').mockResolvedValue({ config: defaultConfig });
    const createRunSpy = vi.spyOn(api, 'createRun').mockResolvedValue({
      runId: 'run-123',
      status: 'created',
      mode: 'standard',
    });
    const fetchRunSnapshotSpy = vi.spyOn(api, 'fetchRunSnapshot').mockResolvedValue({
      runId: 'run-123',
      status: 'created',
      mode: 'standard',
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
    await user.click(screen.getByRole('button', { name: '计算器示例' }));
    await user.type(screen.getByLabelText('缺陷报告目录'), 'examples/calculator-demo/bug-reports');
    await user.click(screen.getByRole('button', { name: '启动运行' }));

    await waitFor(() => {
      expect(createRunSpy).toHaveBeenCalledWith(
        expect.objectContaining({
          projectPath: 'examples/calculator-demo',
          bugReportsDir: 'examples/calculator-demo/bug-reports',
        }),
      );
      expect(fetchRunSnapshotSpy).toHaveBeenCalledWith('run-123');
    });

    expect(await screen.findByRole('heading', { name: '决策面板' })).toBeInTheDocument();
    expect(screen.getByText('run-123')).toBeInTheDocument();
  });
});
