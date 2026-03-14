import { Profiler } from 'react';
import { act, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { App } from '../App';

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function buildSnapshot(overrides: Record<string, unknown> = {}) {
  return {
    runId: 'run-42',
    status: 'running',
    mode: 'standard',
    iteration: 3,
    llmCalls: 12,
    budget: 100,
    decisionReasoning: 'Prioritize Calculator.add because recent mutants survived.',
    currentTarget: {
      class_name: 'Calculator',
      method_name: 'add',
      method_signature: 'int add(int a, int b)',
    },
    previousTarget: {
      class_name: 'Calculator',
      method_name: 'subtract',
      method_signature: 'int subtract(int a, int b)',
    },
    recentImprovements: [{ iteration: 2, mutation_score_delta: 0.1 }],
    improvementSummary: {
      count: 1,
      latest: {
        mutation_score_delta: 0.1,
        coverage_delta: 0.05,
      },
    },
    metrics: {
      mutationScore: 0.45,
      globalMutationScore: 0.45,
      lineCoverage: 0.7,
      branchCoverage: 0.55,
      totalTests: 8,
      totalMutants: 12,
      globalTotalMutants: 12,
      killedMutants: 5,
      globalKilledMutants: 5,
      survivedMutants: 7,
      globalSurvivedMutants: 7,
      currentMethodCoverage: 0.75,
    },
    phase: {
      key: 'running',
      label: 'Running',
      createdAt: '2026-03-10T10:00:00Z',
      startedAt: '2026-03-10T10:00:05Z',
      completedAt: null,
      failedAt: null,
    },
    artifacts: {
      log: { path: '/tmp/run.log', exists: true },
      resolvedConfig: { path: '/tmp/resolved_config.json', exists: true },
    },
    ...overrides,
  };
}

class MockEventSource {
  static instances: MockEventSource[] = [];

  url: string;
  onerror: (() => void) | null = null;
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

  close() {}

  emit(type: string, payload: unknown) {
    const event = new MessageEvent(type, { data: JSON.stringify(payload) });
    for (const listener of this.listeners.get(type) ?? []) {
      listener(event);
    }
  }
}

describe('Run page standard mode', () => {
  beforeEach(() => {
    MockEventSource.instances = [];
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => jsonResponse(buildSnapshot())),
    );
    vi.stubGlobal('EventSource', MockEventSource as unknown as typeof EventSource);
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it('renders the standard snapshot before subscribing to live updates', async () => {
    render(
      <MemoryRouter initialEntries={['/runs/run-42']}>
        <App />
      </MemoryRouter>,
    );

    expect(await screen.findByRole('heading', { name: '决策面板' })).toBeInTheDocument();
    expect(
      screen.getByText('Prioritize Calculator.add because recent mutants survived.'),
    ).toBeInTheDocument();
    expect(screen.getByText('Calculator.add [int add(int a, int b)]')).toBeInTheDocument();
    expect(screen.getByText('阶段：运行中')).toBeInTheDocument();
    expect(screen.getByText('45.0%')).toBeInTheDocument();
    expect(screen.getByText('记录的改进')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '核心指标' })).toBeInTheDocument();

    await waitFor(() => {
      expect(MockEventSource.instances).toHaveLength(1);
    });
  });

  it('applies SSE updates and records action history summaries', async () => {
    render(
      <MemoryRouter initialEntries={['/runs/run-42']}>
        <App />
      </MemoryRouter>,
    );

    await screen.findByRole('heading', { name: '决策面板' });
    await waitFor(() => {
      expect(MockEventSource.instances).toHaveLength(1);
    });

    const stream = MockEventSource.instances[0];
    await act(async () => {
      stream.emit('run.phase', {
        type: 'run.phase',
        sequence: 1,
        status: 'running',
        phase: { key: 'running', label: 'Running' },
        iteration: 4,
        decisionReasoning: 'Switch to Calculator.divide after coverage plateaued.',
        currentTarget: {
          class_name: 'Calculator',
          method_name: 'divide',
          method_signature: 'int divide(int a, int b)',
        },
        recentImprovements: [{ iteration: 4, mutation_score_delta: 0.2 }],
        improvementSummary: {
          count: 2,
          latest: {
            mutation_score_delta: 0.2,
            coverage_delta: 0.12,
          },
        },
        metrics: {
          mutationScore: 0.6,
          lineCoverage: 0.82,
          branchCoverage: 0.65,
          totalTests: 10,
          killedMutants: 8,
          survivedMutants: 4,
          currentMethodCoverage: 0.9,
        },
      });
    });

    expect(
      await screen.findByText('Switch to Calculator.divide after coverage plateaued.'),
    ).toBeInTheDocument();
    expect(screen.getByText('Calculator.divide [int divide(int a, int b)]')).toBeInTheDocument();
    expect(screen.getByText('60.0%')).toBeInTheDocument();
    expect(screen.getByText('阶段已更新')).toBeInTheDocument();
  });

  it('keeps terminal runs from being reported as realtime connection errors', async () => {
    render(
      <MemoryRouter initialEntries={['/runs/run-42']}>
        <App />
      </MemoryRouter>,
    );

    await screen.findByRole('heading', { name: '决策面板' });
    await waitFor(() => {
      expect(MockEventSource.instances).toHaveLength(1);
    });

    const stream = MockEventSource.instances[0];
    await act(async () => {
      stream.emit('run.completed', {
        type: 'run.completed',
        sequence: 2,
        status: 'completed',
        phase: { key: 'completed', label: 'Completed' },
      });
    });

    expect(screen.getByText('状态：已完成')).toBeInTheDocument();
    expect(screen.getByText('实时连接：已结束')).toBeInTheDocument();

    await act(async () => {
      stream.onerror?.();
    });

    expect(screen.getByText('实时连接：已结束')).toBeInTheDocument();
    expect(screen.queryByText('实时连接：异常')).not.toBeInTheDocument();
  });

  it('marks initially completed runs as having ended realtime updates', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        jsonResponse(
          buildSnapshot({
            status: 'completed',
            phase: {
              key: 'completed',
              label: 'Completed',
              createdAt: '2026-03-10T10:00:00Z',
              startedAt: '2026-03-10T10:00:05Z',
              completedAt: '2026-03-10T10:05:00Z',
              failedAt: null,
            },
          }),
        ),
      ),
    );

    render(
      <MemoryRouter initialEntries={['/runs/run-42']}>
        <App />
      </MemoryRouter>,
    );

    expect(await screen.findByText('状态：已完成')).toBeInTheDocument();
    expect(screen.getByText('实时连接：已结束')).toBeInTheDocument();
    expect(MockEventSource.instances).toHaveLength(0);
  });

  it('shows the standard decision panel without parallel worker content', async () => {
    render(
      <MemoryRouter initialEntries={['/runs/run-42']}>
        <App />
      </MemoryRouter>,
    );

    expect(await screen.findByRole('heading', { name: '决策面板' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '核心指标' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '操作历史摘要' })).toBeInTheDocument();
    expect(screen.queryByText('当前批次')).not.toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: '工作线程输出' })).not.toBeInTheDocument();
  });

  it('falls back to snapshot polling when live events are unavailable', async () => {
    let fetchCalls = 0;
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        fetchCalls += 1;
        return jsonResponse(
          fetchCalls === 1
            ? buildSnapshot()
            : buildSnapshot({
                iteration: 4,
                phase: {
                  key: 'preprocessing',
                  label: 'Preprocessing',
                },
                decisionReasoning:
                  'Collect preprocessing artifacts before resuming target selection.',
              }),
        );
      }),
    );
    vi.stubGlobal('EventSource', undefined);

    render(
      <MemoryRouter initialEntries={['/runs/run-42']}>
        <App />
      </MemoryRouter>,
    );

    expect(await screen.findByText('阶段：运行中')).toBeInTheDocument();

    await act(async () => {
      await new Promise((resolve) => window.setTimeout(resolve, 1600));
    });

    await waitFor(() => {
      expect(
        screen.getByText('Collect preprocessing artifacts before resuming target selection.'),
      ).toBeInTheDocument();
      expect(screen.getByText('阶段：预处理中')).toBeInTheDocument();
    });
  });

  it('skips fallback rerenders when polled snapshots are unchanged', async () => {
    vi.useFakeTimers();

    let fetchCalls = 0;
    const onRender = vi.fn();
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        fetchCalls += 1;
        return jsonResponse(buildSnapshot());
      }),
    );
    vi.stubGlobal('EventSource', undefined);

    render(
      <Profiler id="run-page" onRender={onRender}>
        <MemoryRouter initialEntries={['/runs/run-42']}>
          <App />
        </MemoryRouter>
      </Profiler>,
    );

    await act(async () => {
      await Promise.resolve();
    });

    expect(screen.getByRole('heading', { name: '决策面板' })).toBeInTheDocument();
    const renderCountAfterLoad = onRender.mock.calls.length;
    expect(renderCountAfterLoad).toBeGreaterThan(0);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1600);
    });

    expect(fetchCalls).toBe(2);
    expect(onRender).toHaveBeenCalledTimes(renderCountAfterLoad);
  });

  it('keeps the current run view visible when a fallback poll fails', async () => {
    vi.useFakeTimers();

    let fetchCalls = 0;
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        fetchCalls += 1;

        if (fetchCalls === 1) {
          return jsonResponse(buildSnapshot());
        }

        throw new Error('fallback poll failed');
      }),
    );
    vi.stubGlobal('EventSource', undefined);

    render(
      <MemoryRouter initialEntries={['/runs/run-42']}>
        <App />
      </MemoryRouter>,
    );

    await act(async () => {
      await Promise.resolve();
    });

    expect(screen.getByRole('heading', { name: '决策面板' })).toBeInTheDocument();
    expect(
      screen.getByText('Prioritize Calculator.add because recent mutants survived.'),
    ).toBeInTheDocument();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1600);
    });

    expect(fetchCalls).toBe(2);
    expect(screen.getByRole('heading', { name: '决策面板' })).toBeInTheDocument();
    expect(
      screen.getByText('Prioritize Calculator.add because recent mutants survived.'),
    ).toBeInTheDocument();
    expect(screen.getByRole('alert')).toHaveTextContent('fallback poll failed');
  });
});
