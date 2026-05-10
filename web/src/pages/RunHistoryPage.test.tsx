import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { RunHistoryPage } from './RunHistoryPage';
import * as api from '../lib/api';

const baseEntry = {
  runId: 'run-example',
  status: 'completed',
  mode: 'standard',
  projectPath: '/workspace/project',
  configPath: '/workspace/config.yaml',
  createdAt: '2026-05-08T10:00:00Z',
  startedAt: '2026-05-08T10:00:01Z',
  completedAt: '2026-05-08T10:00:05Z',
  failedAt: null,
  error: null,
  iteration: 1,
  llmCalls: 2,
  budget: 100,
  phase: { key: 'completed', label: 'Completed' },
  metrics: {
    mutationScore: 0.5,
    globalMutationScore: 0.5,
    lineCoverage: 0.75,
    branchCoverage: 0.25,
    totalTests: 3,
    totalMutants: 4,
    globalTotalMutants: 4,
    killedMutants: 2,
    globalKilledMutants: 2,
    survivedMutants: 2,
    globalSurvivedMutants: 2,
  },
  artifacts: {},
};

function mockHistory(items: Array<Record<string, unknown>>) {
  vi.spyOn(api, 'fetchRunHistory').mockResolvedValue({
    items: items as api.RunHistoryEntry[],
  });
}

function renderHistory() {
  render(
    <MemoryRouter>
      <RunHistoryPage />
    </MemoryRouter>,
  );
}

describe('RunHistoryPage source labels', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('renders example history with the Chinese example display name without leaking container paths', async () => {
    mockHistory([
      {
        ...baseEntry,
        projectSourceType: 'example',
        projectPath: '/opt/comet-l/examples/calculator-demo',
        sourceMetadata: {
          example_project_id: 'calculator-demo',
          display_name: '计算器示例',
        },
      },
    ]);

    renderHistory();

    expect(await screen.findByText('示例项目 · 计算器示例')).toBeInTheDocument();
    expect(screen.queryByText('/opt/comet-l/examples/calculator-demo')).not.toBeInTheDocument();
    expect(screen.queryByText(/\/opt\/comet-l\/examples/)).not.toBeInTheDocument();
  });

  it('uses compatible example display metadata when snake_case display_name is absent', async () => {
    mockHistory([
      {
        ...baseEntry,
        runId: 'run-multi-file',
        projectSourceType: 'example',
        projectPath: '/opt/comet-l/examples/multi-file-demo',
        sourceMetadata: {
          example_project_id: 'multi-file-demo',
          example_project_display_name: '多文件示例',
        },
      },
    ]);

    renderHistory();

    expect(await screen.findByText('示例项目 · 多文件示例')).toBeInTheDocument();
    expect(screen.queryByText(/\/opt\/comet-l\/examples/)).not.toBeInTheDocument();
  });

  it('falls back to the example source label when metadata is missing', async () => {
    mockHistory([
      {
        ...baseEntry,
        runId: 'run-example-missing-metadata',
        projectSourceType: 'example',
        projectPath: '/opt/comet-l/examples/calculator-demo',
      },
    ]);

    renderHistory();

    expect(await screen.findByText('示例项目')).toBeInTheDocument();
    expect(screen.queryByText(/\/opt\/comet-l\/examples/)).not.toBeInTheDocument();
  });

  it('keeps upload, local, and GitHub source labels unchanged', async () => {
    mockHistory([
      { ...baseEntry, runId: 'run-upload', projectSourceType: 'upload' },
      { ...baseEntry, runId: 'run-local', projectSourceType: 'local' },
      { ...baseEntry, runId: 'run-github', projectSourceType: 'github' },
    ]);

    renderHistory();

    expect(await screen.findByText('上传项目')).toBeInTheDocument();
    expect(screen.getByText('本地路径')).toBeInTheDocument();
    expect(screen.getByText('GitHub 仓库')).toBeInTheDocument();
  });

  it('disables result entry for non-terminal runs and keeps terminal runs linked', async () => {
    mockHistory([
      {
        ...baseEntry,
        runId: 'run-running',
        status: 'running',
        completedAt: null,
      },
      {
        ...baseEntry,
        runId: 'run-completed',
        status: 'completed',
      },
    ]);

    renderHistory();

    expect(await screen.findByRole('button', { name: '结果' })).toBeDisabled();
    expect(screen.getByRole('link', { name: '结果' })).toHaveAttribute(
      'href',
      '/runs/run-completed/results',
    );
    const resultLinks = screen.queryAllByRole('link', { name: '结果' });
    expect(
      resultLinks.some((link) => link.getAttribute('href') === '/runs/run-running/results'),
    ).toBe(false);
  });
});
