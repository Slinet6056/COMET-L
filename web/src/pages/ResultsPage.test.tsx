import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { App } from '../App';

function expectMetricValue(label: string, value: string) {
  const heading = screen.getByText(label);
  const card = heading.closest('article');
  expect(card).not.toBeNull();
  expect(card).toHaveTextContent(value);
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function buildResults(overrides: Record<string, unknown> = {}) {
  return {
    runId: 'run-42',
    status: 'completed',
    mode: 'standard',
    iteration: 4,
    llmCalls: 13,
    budget: 88,
    phase: {
      key: 'completed',
      label: 'Completed',
      createdAt: '2026-03-10T10:00:00Z',
      startedAt: '2026-03-10T10:01:00Z',
      completedAt: '2026-03-10T10:05:00Z',
      failedAt: null,
    },
    summary: {
      metrics: {
        mutationScore: 0.5,
        globalMutationScore: 0.8,
        lineCoverage: 0.9,
        branchCoverage: 0.75,
        totalTests: 7,
        totalMutants: 2,
        globalTotalMutants: 5,
        killedMutants: 1,
        globalKilledMutants: 4,
        survivedMutants: 1,
        globalSurvivedMutants: 1,
        currentMethodCoverage: 0.75,
      },
      tests: {
        totalCases: 1,
        compiledCases: 1,
        totalMethods: 2,
        targetMethods: 1,
      },
      mutants: {
        total: 2,
        evaluated: 2,
        killed: 1,
        survived: 1,
        pending: 0,
        valid: 1,
        invalid: 0,
        outdated: 0,
      },
      coverage: {
        latestIteration: 3,
        methodsTracked: 1,
        averageLineCoverage: 0.75,
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
        updatedAt: '2026-03-10T10:05:00Z',
        downloadUrl: '/api/runs/run-42/artifacts/final-state',
      },
      runLog: {
        exists: true,
        filename: 'run.log',
        contentType: 'text/plain; charset=utf-8',
        sizeBytes: 256,
        updatedAt: '2026-03-10T10:05:01Z',
        downloadUrl: '/api/runs/run-42/artifacts/run-log',
      },
    },
    ...overrides,
  };
}

describe('Run results page', () => {
  beforeEach(() => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: string | URL | Request) => {
        const url = typeof input === 'string' ? input : input.toString();
        if (url === '/api/runs/run-42/results') {
          return jsonResponse(buildResults());
        }

        throw new Error(`Unexpected request: ${url}`);
      }),
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('renders final metrics and artifact download links', async () => {
    render(
      <MemoryRouter initialEntries={['/runs/run-42/results']}>
        <App />
      </MemoryRouter>,
    );

    expect(await screen.findByRole('heading', { name: '最终统计' })).toBeInTheDocument();
    expect(screen.getByText('状态：已完成')).toBeInTheDocument();
    expect(screen.getByText('变异分数')).toBeInTheDocument();
    expect(screen.getByText('行覆盖率')).toBeInTheDocument();
    expectMetricValue('变异分数', '50.0%');
    expectMetricValue('变异体总数', '2');
    expect(screen.getByRole('link', { name: '下载 final_state.json' })).toHaveAttribute(
      'href',
      '/api/runs/run-42/artifacts/final-state',
    );
    expect(screen.getByRole('link', { name: '下载 run.log' })).toHaveAttribute(
      'href',
      '/api/runs/run-42/artifacts/run-log',
    );
    expect(screen.getByText('标准单目标演化')).toBeInTheDocument();
  });

  it('handles failed terminal state and missing artifacts gracefully', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: string | URL | Request) => {
        const url = typeof input === 'string' ? input : input.toString();
        if (url === '/api/runs/run-42/results') {
          return jsonResponse(
            buildResults({
              status: 'failed',
              phase: { key: 'failed', label: 'Failed' },
              mode: 'parallel',
              artifacts: {
                finalState: {
                  exists: false,
                  filename: 'final_state.json',
                  contentType: 'application/json',
                  sizeBytes: null,
                  updatedAt: null,
                  downloadUrl: '/api/runs/run-42/artifacts/final-state',
                },
                runLog: {
                  exists: true,
                  filename: 'run.log',
                  contentType: 'text/plain; charset=utf-8',
                  sizeBytes: 256,
                  updatedAt: '2026-03-10T10:05:01Z',
                  downloadUrl: '/api/runs/run-42/artifacts/run-log',
                },
              },
            }),
          );
        }

        throw new Error(`Unexpected request: ${url}`);
      }),
    );

    render(
      <MemoryRouter initialEntries={['/runs/run-42/results']}>
        <App />
      </MemoryRouter>,
    );

    expect(await screen.findByText(/以失败结束/)).toBeInTheDocument();
    expect(screen.getByText('并行批次演化')).toBeInTheDocument();
    expect(screen.getByText('本次运行未生成该工件。')).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: '下载 final_state.json' })).not.toBeInTheDocument();
  });

  it('prefers global and database-backed mutant totals when local final metrics are zero', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: string | URL | Request) => {
        const url = typeof input === 'string' ? input : input.toString();
        if (url === '/api/runs/run-42/results') {
          return jsonResponse(
            buildResults({
              mode: 'parallel',
              summary: {
                metrics: {
                  mutationScore: 0,
                  globalMutationScore: 0.8,
                  lineCoverage: 0.9,
                  branchCoverage: 0.75,
                  totalTests: 7,
                  totalMutants: 0,
                  globalTotalMutants: 5,
                  killedMutants: 0,
                  globalKilledMutants: 4,
                  survivedMutants: 0,
                  globalSurvivedMutants: 1,
                  currentMethodCoverage: 0.75,
                },
                tests: {
                  totalCases: 1,
                  compiledCases: 1,
                  totalMethods: 2,
                  targetMethods: 1,
                },
                mutants: {
                  total: 5,
                  evaluated: 5,
                  killed: 4,
                  survived: 1,
                  pending: 0,
                  valid: 5,
                  invalid: 0,
                  outdated: 0,
                },
                coverage: {
                  latestIteration: 3,
                  methodsTracked: 1,
                  averageLineCoverage: 0.75,
                  averageBranchCoverage: 0.5,
                },
                sources: {
                  finalState: true,
                  database: true,
                  runLog: true,
                },
              },
            }),
          );
        }

        throw new Error(`Unexpected request: ${url}`);
      }),
    );

    render(
      <MemoryRouter initialEntries={['/runs/run-42/results']}>
        <App />
      </MemoryRouter>,
    );

    expect(await screen.findByRole('heading', { name: '最终统计' })).toBeInTheDocument();
    expectMetricValue('变异分数', '80.0%');
    expectMetricValue('变异体总数', '5');
  });

  it('shows disabled mutation semantics and avoids fallback score recomputation', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: string | URL | Request) => {
        const url = typeof input === 'string' ? input : input.toString();
        if (url === '/api/runs/run-42/results') {
          return jsonResponse(
            buildResults({
              mutationEnabled: false,
              summary: {
                metrics: {
                  mutationScore: null,
                  globalMutationScore: null,
                  lineCoverage: 0.9,
                  branchCoverage: 0.75,
                  totalTests: 7,
                  totalMutants: null,
                  globalTotalMutants: null,
                  killedMutants: null,
                  globalKilledMutants: null,
                  survivedMutants: null,
                  globalSurvivedMutants: null,
                  currentMethodCoverage: 0.75,
                },
                tests: {
                  totalCases: 1,
                  compiledCases: 1,
                  totalMethods: 2,
                  targetMethods: 1,
                },
                mutants: {
                  total: 5,
                  evaluated: 5,
                  killed: 4,
                  survived: 1,
                  pending: 0,
                  valid: 5,
                  invalid: 0,
                  outdated: 0,
                },
                coverage: {
                  latestIteration: 3,
                  methodsTracked: 1,
                  averageLineCoverage: 0.75,
                  averageBranchCoverage: 0.5,
                },
                sources: {
                  finalState: true,
                  database: true,
                  runLog: true,
                },
              },
            }),
          );
        }

        throw new Error(`Unexpected request: ${url}`);
      }),
    );

    render(
      <MemoryRouter initialEntries={['/runs/run-42/results']}>
        <App />
      </MemoryRouter>,
    );

    expect(await screen.findByRole('heading', { name: '最终统计' })).toBeInTheDocument();
    expectMetricValue('变异分析状态', '未启用');
    expectMetricValue('变异体状态', '未启用');
    expect(screen.queryByText('80.0%')).not.toBeInTheDocument();
  });
});
