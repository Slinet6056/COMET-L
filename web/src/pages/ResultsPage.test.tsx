import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { App } from '../App';

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

    expect(await screen.findByRole('heading', { name: 'Final Statistics' })).toBeInTheDocument();
    expect(screen.getByText('Status: completed')).toBeInTheDocument();
    expect(screen.getByText('Mutation score')).toBeInTheDocument();
    expect(screen.getByText('Line coverage')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Download final_state.json' })).toHaveAttribute(
      'href',
      '/api/runs/run-42/artifacts/final-state',
    );
    expect(screen.getByRole('link', { name: 'Download run.log' })).toHaveAttribute(
      'href',
      '/api/runs/run-42/artifacts/run-log',
    );
    expect(screen.getByText('Standard single-target evolution')).toBeInTheDocument();
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

    expect(await screen.findByText(/ended in failure/i)).toBeInTheDocument();
    expect(screen.getByText('Parallel batch evolution')).toBeInTheDocument();
    expect(screen.getByText('This artifact was not generated for the run.')).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'Download final_state.json' })).not.toBeInTheDocument();
  });
});
