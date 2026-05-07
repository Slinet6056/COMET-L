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
        if (url === '/api/auth/me') {
          return jsonResponse({
            user: { id: 1, username: 'tester', role: 'user' },
          });
        }
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
        if (url === '/api/auth/me') {
          return jsonResponse({
            user: { id: 1, username: 'tester', role: 'user' },
          });
        }
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
        if (url === '/api/auth/me') {
          return jsonResponse({
            user: { id: 1, username: 'tester', role: 'user' },
          });
        }
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
        if (url === '/api/auth/me') {
          return jsonResponse({
            user: { id: 1, username: 'tester', role: 'user' },
          });
        }
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

  it('shows PR link when pullRequestUrl is present', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: string | URL | Request) => {
        const url = typeof input === 'string' ? input : input.toString();
        if (url === '/api/auth/me') {
          return jsonResponse({
            user: { id: 1, username: 'tester', role: 'user' },
          });
        }
        if (url === '/api/runs/run-42/results') {
          return jsonResponse(
            buildResults({
              pullRequestUrl: 'https://github.com/example/repo/pull/123',
              reportArtifact: {
                exists: true,
                filename: 'report.md',
                contentType: 'text/markdown',
                sizeBytes: 512,
                updatedAt: '2026-03-10T10:05:02Z',
                downloadUrl: '/api/runs/run-42/artifacts/report',
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

    expect(await screen.findByRole('heading', { name: 'Pull Request 链接' })).toBeInTheDocument();
    const prLink = screen.getByTestId('pr-link');
    expect(prLink).toHaveAttribute('href', 'https://github.com/example/repo/pull/123');
    expect(prLink).toHaveAttribute('target', '_blank');
    expect(prLink).toHaveTextContent('查看 Pull Request');
  });

  it('shows PR failure message when pullRequestUrl is absent but report exists', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: string | URL | Request) => {
        const url = typeof input === 'string' ? input : input.toString();
        if (url === '/api/auth/me') {
          return jsonResponse({
            user: { id: 1, username: 'tester', role: 'user' },
          });
        }
        if (url === '/api/runs/run-42/results') {
          return jsonResponse(
            buildResults({
              pullRequestUrl: null,
              pullRequestError: '创建 GitHub PR 失败: HTTP 422 - Validation Failed',
              reportArtifact: {
                exists: true,
                filename: 'report.md',
                contentType: 'text/markdown',
                sizeBytes: 512,
                updatedAt: '2026-03-10T10:05:02Z',
                downloadUrl: '/api/runs/run-42/artifacts/report',
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

    expect(
      await screen.findByRole('heading', { name: 'Pull Request 创建失败' }),
    ).toBeInTheDocument();
    expect(
      screen.getByText('创建 GitHub PR 失败: HTTP 422 - Validation Failed'),
    ).toBeInTheDocument();
    expect(screen.queryByTestId('pr-link')).not.toBeInTheDocument();
    expect(screen.queryByText(/以失败结束/)).not.toBeInTheDocument();
  });

  it('shows report download link when reportArtifact exists', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: string | URL | Request) => {
        const url = typeof input === 'string' ? input : input.toString();
        if (url === '/api/auth/me') {
          return jsonResponse({
            user: { id: 1, username: 'tester', role: 'user' },
          });
        }
        if (url === '/api/runs/run-42/results') {
          return jsonResponse(
            buildResults({
              reportArtifact: {
                exists: true,
                filename: 'report.md',
                contentType: 'text/markdown',
                sizeBytes: 512,
                updatedAt: '2026-03-10T10:05:02Z',
                downloadUrl: '/api/runs/run-42/artifacts/report',
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

    expect(await screen.findByRole('heading', { name: '工件下载' })).toBeInTheDocument();
    const reportLink = screen.getByTestId('report-download-link');
    expect(reportLink).toHaveAttribute('href', '/api/runs/run-42/artifacts/report');
    expect(reportLink).toHaveTextContent('下载 report.md');
  });

  it('shows final tests archive from top-level finalTestsArchive downloadUrl', async () => {
    const backendProvidedFinalTestsUrl = '/api/runs/run-123/artifacts/final-tests.zip';

    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: string | URL | Request) => {
        const url = typeof input === 'string' ? input : input.toString();
        if (url === '/api/auth/me') {
          return jsonResponse({
            user: { id: 1, username: 'tester', role: 'user' },
          });
        }
        if (url === '/api/runs/run-123/results') {
          return jsonResponse(
            buildResults({
              runId: 'run-123',
              finalTestsArchive: {
                exists: true,
                filename: 'comet-run-run-123-generated-tests.zip',
                contentType: 'application/zip',
                sizeBytes: 1024,
                updatedAt: '2026-03-10T10:05:03Z',
                downloadUrl: backendProvidedFinalTestsUrl,
              },
              artifacts: {
                finalState: {
                  exists: true,
                  filename: 'final_state.json',
                  contentType: 'application/json',
                  sizeBytes: 128,
                  updatedAt: '2026-03-10T10:05:00Z',
                  downloadUrl: '/api/runs/run-123/artifacts/final-state',
                },
                runLog: {
                  exists: true,
                  filename: 'run.log',
                  contentType: 'text/plain; charset=utf-8',
                  sizeBytes: 256,
                  updatedAt: '2026-03-10T10:05:01Z',
                  downloadUrl: '/api/runs/run-123/artifacts/run-log',
                },
              },
            }),
          );
        }

        throw new Error(`Unexpected request: ${url}`);
      }),
    );

    render(
      <MemoryRouter initialEntries={['/runs/run-123/results']}>
        <App />
      </MemoryRouter>,
    );

    expect(await screen.findByText('最终测试包')).toBeInTheDocument();
    // Payload driven: the browser must not synthesize the final tests URL.
    const finalTestsLink = screen.getByRole('link', {
      name: '下载 comet-run-run-123-generated-tests.zip',
    });
    expect(finalTestsLink).toHaveAttribute('href', backendProvidedFinalTestsUrl);
  });

  it('does not render final tests archive when finalTestsArchive is absent', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: string | URL | Request) => {
        const url = typeof input === 'string' ? input : input.toString();
        if (url === '/api/auth/me') {
          return jsonResponse({
            user: { id: 1, username: 'tester', role: 'user' },
          });
        }
        if (url === '/api/runs/run-123/results') {
          return jsonResponse(
            buildResults({
              runId: 'run-123',
              artifacts: {
                finalState: {
                  exists: true,
                  filename: 'final_state.json',
                  contentType: 'application/json',
                  sizeBytes: 128,
                  updatedAt: '2026-03-10T10:05:00Z',
                  downloadUrl: '/api/runs/run-123/artifacts/final-state',
                },
                runLog: {
                  exists: true,
                  filename: 'run.log',
                  contentType: 'text/plain; charset=utf-8',
                  sizeBytes: 256,
                  updatedAt: '2026-03-10T10:05:01Z',
                  downloadUrl: '/api/runs/run-123/artifacts/run-log',
                },
              },
            }),
          );
        }

        throw new Error(`Unexpected request: ${url}`);
      }),
    );

    render(
      <MemoryRouter initialEntries={['/runs/run-123/results']}>
        <App />
      </MemoryRouter>,
    );

    expect(await screen.findByRole('heading', { name: '最终统计' })).toBeInTheDocument();
    expect(screen.queryByText('最终测试包')).not.toBeInTheDocument();
  });

  it('shows Java version badge when selectedJavaVersion is present', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: string | URL | Request) => {
        const url = typeof input === 'string' ? input : input.toString();
        if (url === '/api/auth/me') {
          return jsonResponse({
            user: { id: 1, username: 'tester', role: 'user' },
          });
        }
        if (url === '/api/runs/run-42/results') {
          return jsonResponse(
            buildResults({
              selectedJavaVersion: '17',
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

    expect(await screen.findByTestId('java-version-badge')).toBeInTheDocument();
    expect(screen.getByTestId('java-version-badge')).toHaveTextContent('Java 版本：17');
  });

  it('hides Java version badge when selectedJavaVersion is absent', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: string | URL | Request) => {
        const url = typeof input === 'string' ? input : input.toString();
        if (url === '/api/auth/me') {
          return jsonResponse({
            user: { id: 1, username: 'tester', role: 'user' },
          });
        }
        if (url === '/api/runs/run-42/results') {
          return jsonResponse(buildResults());
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
    expect(screen.queryByTestId('java-version-badge')).not.toBeInTheDocument();
  });

  it('renders pending, stale, cancelled, and succeeded statuses with Chinese copy', async () => {
    const statuses = [
      ['pending', '状态：等待中'],
      ['stale', '状态：已失效'],
      ['cancelled', '状态：已取消'],
      ['succeeded', '状态：已完成'],
    ];

    for (const [status, label] of statuses) {
      vi.stubGlobal(
        'fetch',
        vi.fn(async (input: string | URL | Request) => {
          const url = typeof input === 'string' ? input : input.toString();
          if (url === '/api/auth/me') {
            return jsonResponse({
              user: { id: 1, username: 'tester', role: 'user' },
            });
          }
          if (url === '/api/runs/run-42/results') {
            return jsonResponse(
              buildResults({
                status,
                phase: { key: status, label: status },
              }),
            );
          }

          throw new Error(`Unexpected request: ${url}`);
        }),
      );

      const view = render(
        <MemoryRouter initialEntries={['/runs/run-42/results']}>
          <App />
        </MemoryRouter>,
      );

      expect(await screen.findByText(label)).toBeInTheDocument();
      view.unmount();
      vi.unstubAllGlobals();
    }
  });

  it('uses a generic not-found message for cross-user 404 without leaking backend details', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: string | URL | Request) => {
        const url = typeof input === 'string' ? input : input.toString();
        if (url === '/api/auth/me') {
          return jsonResponse({
            user: { id: 1, username: 'tester', role: 'user' },
          });
        }
        if (url === '/api/runs/secret-run/results') {
          return jsonResponse(
            {
              error: {
                code: 'run_not_found',
                message: 'Run secret-run not found under /home/comet/state/users/alice',
                fieldErrors: [],
              },
            },
            404,
          );
        }

        throw new Error(`Unexpected request: ${url}`);
      }),
    );

    render(
      <MemoryRouter initialEntries={['/runs/secret-run/results']}>
        <App />
      </MemoryRouter>,
    );

    expect(await screen.findByRole('alert')).toHaveTextContent('任务不存在或无权访问');
    expect(screen.queryByText(/secret-run/)).not.toBeInTheDocument();
    expect(screen.queryByText(/\/home|state\/users|alice/)).not.toBeInTheDocument();
  });

  it('returns to the login view when a results request reports an expired session', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: string | URL | Request) => {
        const url = typeof input === 'string' ? input : input.toString();
        if (url === '/api/auth/me') {
          return jsonResponse({
            user: { id: 1, username: 'tester', role: 'user' },
          });
        }
        if (url === '/api/runs/run-42/results') {
          return jsonResponse(
            {
              error: {
                code: 'auth_required',
                message: '请先登录',
                fieldErrors: [],
              },
            },
            401,
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

    expect(await screen.findByText('登录状态已过期，请重新登录。')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '登录' })).toBeInTheDocument();
  });

  it('does not create download links for artifacts missing a server-provided URL', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: string | URL | Request) => {
        const url = typeof input === 'string' ? input : input.toString();
        if (url === '/api/auth/me') {
          return jsonResponse({
            user: { id: 1, username: 'tester', role: 'user' },
          });
        }
        if (url === '/api/runs/run-42/results') {
          return jsonResponse(
            buildResults({
              reportArtifact: {
                exists: true,
                filename: 'report.md',
                contentType: 'text/markdown',
                sizeBytes: 512,
                updatedAt: '2026-03-10T10:05:02Z',
                path: '/home/comet/output/users/alice/report.md',
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

    expect(await screen.findByRole('heading', { name: '工件下载' })).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: '下载 report.md' })).not.toBeInTheDocument();
    expect(screen.getByText('服务器未提供安全下载链接。')).toBeInTheDocument();
    expect(screen.queryByText(/\/home|output\/users/)).not.toBeInTheDocument();
  });
});
