import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { App } from './App';

class IdleEventSource {
  constructor(_url: string) {}

  addEventListener(_type: string, _listener: EventListenerOrEventListenerObject) {}

  close() {}
}

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

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

describe('App routing scaffold', () => {
  beforeEach(() => {
    vi.stubGlobal('EventSource', IdleEventSource as unknown as typeof EventSource);
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: string | URL | Request) => {
        const url = typeof input === 'string' ? input : input.toString();
        if (url === '/api/config/defaults') {
          return jsonResponse({ config: defaultConfig });
        }
        if (url === '/api/runs/run-42') {
          return jsonResponse({
            runId: 'run-42',
            status: 'running',
            mode: 'standard',
            iteration: 0,
            llmCalls: 0,
            budget: 10,
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
        }
        if (url === '/api/runs/run-historical') {
          return jsonResponse({
            runId: 'run-historical',
            status: 'completed',
            mode: 'parallel',
            iteration: 1,
            llmCalls: 2,
            budget: 10,
            decisionReasoning: null,
            currentTarget: null,
            previousTarget: null,
            recentImprovements: [],
            improvementSummary: { count: 0, latest: null },
            metrics: {
              mutationScore: 0.4,
              globalMutationScore: 0.6,
              lineCoverage: 0.7,
              branchCoverage: 0.5,
              totalTests: 3,
              totalMutants: 5,
              globalTotalMutants: 5,
              killedMutants: 2,
              globalKilledMutants: 3,
              survivedMutants: 3,
              globalSurvivedMutants: 2,
              currentMethodCoverage: null,
            },
            phase: { key: 'completed', label: 'Completed' },
            artifacts: {
              log: {
                exists: true,
                downloadUrl: '/api/runs/run-historical/artifacts/run-log',
              },
            },
            isHistorical: true,
            currentBatch: 0,
            parallelStats: {},
            activeTargets: [],
            workerCards: [],
            batchResults: [],
          });
        }
        if (url === '/api/runs/history') {
          return jsonResponse({
            items: [
              {
                runId: 'run-42',
                status: 'completed',
                mode: 'standard',
                projectPath: 'examples/calculator-demo',
                configPath: 'config.yaml',
                createdAt: '2026-03-10T10:00:00Z',
                startedAt: '2026-03-10T10:01:00Z',
                completedAt: '2026-03-10T10:05:00Z',
                failedAt: null,
                error: null,
                iteration: 2,
                llmCalls: 4,
                budget: 10,
                phase: { key: 'completed', label: 'Completed' },
                metrics: {
                  mutationScore: 0.5,
                  globalMutationScore: 0.5,
                  lineCoverage: 0.8,
                  branchCoverage: 0.6,
                  totalTests: 4,
                  totalMutants: 6,
                  globalTotalMutants: 6,
                  killedMutants: 3,
                  globalKilledMutants: 3,
                  survivedMutants: 3,
                  globalSurvivedMutants: 3,
                  currentMethodCoverage: 0.7,
                },
                artifacts: {
                  finalState: {
                    exists: true,
                    downloadUrl: '/api/runs/run-42/artifacts/final-state',
                  },
                  log: {
                    exists: true,
                    downloadUrl: '/api/runs/run-42/artifacts/run-log',
                  },
                },
              },
            ],
          });
        }
        if (url === '/api/runs/run-42/results') {
          return jsonResponse({
            runId: 'run-42',
            status: 'completed',
            mode: 'standard',
            iteration: 2,
            llmCalls: 4,
            budget: 10,
            phase: { key: 'completed', label: 'Completed' },
            summary: {
              metrics: {
                mutationScore: 0.5,
                globalMutationScore: 0.5,
                lineCoverage: 0.8,
                branchCoverage: 0.6,
                totalTests: 4,
                totalMutants: 6,
                globalTotalMutants: 6,
                killedMutants: 3,
                globalKilledMutants: 3,
                survivedMutants: 3,
                globalSurvivedMutants: 3,
                currentMethodCoverage: 0.7,
              },
              tests: {
                totalCases: 2,
                compiledCases: 2,
                totalMethods: 3,
                targetMethods: 1,
              },
              mutants: {
                total: 6,
                evaluated: 6,
                killed: 3,
                survived: 3,
                pending: 0,
                valid: 6,
                invalid: 0,
                outdated: 0,
              },
              coverage: {
                latestIteration: 2,
                methodsTracked: 1,
                averageLineCoverage: 0.7,
                averageBranchCoverage: 0.5,
              },
              sources: {
                finalState: true,
                database: true,
                runLog: true,
              },
            },
            artifacts: {
              finalState: {
                exists: true,
                filename: 'final_state.json',
                contentType: 'application/json',
                sizeBytes: 128,
                updatedAt: '2026-03-10T10:00:00Z',
                downloadUrl: '/api/runs/run-42/artifacts/final-state',
              },
              runLog: {
                exists: true,
                filename: 'run.log',
                contentType: 'text/plain; charset=utf-8',
                sizeBytes: 256,
                updatedAt: '2026-03-10T10:00:01Z',
                downloadUrl: '/api/runs/run-42/artifacts/run-log',
              },
            },
          });
        }

        throw new Error(`Unexpected request: ${url}`);
      }),
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('renders the home route', async () => {
    render(
      <MemoryRouter initialEntries={['/']}>
        <App />
      </MemoryRouter>,
    );

    expect(await screen.findByLabelText('项目路径')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: '运行记录' })).toHaveAttribute('href', '/runs/history');
  });

  it('renders the run route', async () => {
    render(
      <MemoryRouter initialEntries={['/runs/run-42']}>
        <App />
      </MemoryRouter>,
    );

    expect(await screen.findByRole('heading', { name: '决策面板' })).toBeInTheDocument();
    expect(screen.getByText('run-42')).toBeInTheDocument();
  });

  it('degrades historical run logs to artifact download', async () => {
    render(
      <MemoryRouter initialEntries={['/runs/run-historical']}>
        <App />
      </MemoryRouter>,
    );

    expect(await screen.findByRole('heading', { name: '历史日志' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: '下载 run.log' })).toHaveAttribute(
      'href',
      '/api/runs/run-historical/artifacts/run-log',
    );
  });

  it('renders the results route', async () => {
    render(
      <MemoryRouter initialEntries={['/runs/run-42/results']}>
        <App />
      </MemoryRouter>,
    );

    expect(await screen.findByText(/终态摘要/)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: '下载 final_state.json' })).toHaveAttribute(
      'href',
      '/api/runs/run-42/artifacts/final-state',
    );
  });
});
