import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { LogViewer } from './LogViewer';
import * as api from '../lib/api';

function buildStream(
  taskId: string,
  status: 'pending' | 'running' | 'completed' | 'failed',
  overrides: Partial<api.RunLogStream> = {},
): api.RunLogStream {
  const startedAt = overrides.startedAt ?? (status === 'pending' ? null : '2026-03-10T10:00:00Z');
  const endedAt =
    overrides.endedAt ??
    (status === 'completed' || status === 'failed' ? '2026-03-10T10:00:05Z' : null);

  return {
    taskId,
    order: overrides.order ?? 0,
    status,
    startedAt,
    completedAt: overrides.completedAt ?? (status === 'completed' ? endedAt : null),
    failedAt: overrides.failedAt ?? (status === 'failed' ? endedAt : null),
    endedAt,
    durationSeconds:
      overrides.durationSeconds ?? (status === 'completed' || status === 'failed' ? 5 : null),
    firstEntryAt: overrides.firstEntryAt ?? startedAt,
    lastEntryAt: overrides.lastEntryAt ?? endedAt ?? startedAt,
    bufferedEntryCount: overrides.bufferedEntryCount ?? 0,
    totalEntryCount: overrides.totalEntryCount ?? 0,
  };
}

function buildSummary(runId: string, streams: api.RunLogStream[]) {
  return {
    runId,
    streams: {
      taskIds: streams.map((stream) => stream.taskId),
      counts: Object.fromEntries(streams.map((stream) => [stream.taskId, stream.totalEntryCount])),
      maxEntriesPerStream: 50,
      items: streams,
      byTaskId: Object.fromEntries(streams.map((stream) => [stream.taskId, stream])),
    },
  };
}

function buildStreamResponse(
  taskId: string,
  messages: string[],
  stream: api.RunLogStream,
  availableTaskIds: string[],
) {
  return {
    runId: 'run-logs-test',
    taskId,
    availableTaskIds,
    maxEntriesPerStream: 50,
    stream,
    entries: messages.map((message, index) => ({
      sequence: index + 1,
      timestamp: `2026-03-10T10:00:0${index}Z`,
      taskId,
      logger: `comet.${taskId}`,
      level: 'INFO',
      message,
    })),
  };
}

describe('LogViewer', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it('uses the main view by default and switches between grouped views one at a time', async () => {
    const user = userEvent.setup();
    const mainStream = buildStream('main', 'running', {
      order: 0,
      totalEntryCount: 2,
      bufferedEntryCount: 2,
    });
    const runningStream = buildStream('task-live', 'running', {
      order: 1,
      startedAt: '2026-03-10T10:00:02Z',
      lastEntryAt: '2026-03-10T10:00:04Z',
      totalEntryCount: 1,
      bufferedEntryCount: 1,
    });
    const finishedStream = buildStream('task-done', 'completed', {
      order: 2,
      startedAt: '2026-03-10T10:00:05Z',
      endedAt: '2026-03-10T10:00:08Z',
      completedAt: '2026-03-10T10:00:08Z',
      durationSeconds: 3,
      totalEntryCount: 1,
      bufferedEntryCount: 1,
    });

    vi.spyOn(api, 'fetchRunLogs').mockResolvedValue(
      buildSummary('run-logs-test', [mainStream, runningStream, finishedStream]),
    );
    const fetchRunLogsForTaskSpy = vi
      .spyOn(api, 'fetchRunLogsForTask')
      .mockImplementation(async (_runId, taskId) => {
        const stream =
          taskId === 'main' ? mainStream : taskId === 'task-live' ? runningStream : finishedStream;
        const message =
          taskId === 'main'
            ? 'main log line'
            : taskId === 'task-live'
              ? 'running worker line'
              : 'finished worker line';

        return buildStreamResponse(taskId, [message], stream, ['main', 'task-live', 'task-done']);
      });

    const view = render(<LogViewer runId="run-logs-test" runStatus="running" />);

    expect(await screen.findByText('main log line')).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: '主日志' })).toHaveAttribute('aria-selected', 'true');
    expect(view.container.querySelector('.run-log-timeline__axis')).toBeNull();
    expect(view.container.querySelector('.run-log-row__bar')).toBeNull();
    expect(screen.queryByRole('button', { name: /task-live/i })).not.toBeInTheDocument();

    await user.click(screen.getByRole('tab', { name: '运行中 (1)' }));

    const runningButton = await screen.findByRole('button', { name: /task-live/i });
    expect(runningButton).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByText('running worker line')).not.toBeInTheDocument();

    await user.click(runningButton);

    expect(runningButton).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByText('running worker line')).toBeInTheDocument();
    expect(screen.queryByText('main log line')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /task-done/i })).not.toBeInTheDocument();

    await user.click(screen.getByRole('tab', { name: '已结束 (1)' }));

    const finishedButton = await screen.findByRole('button', { name: /task-done/i });
    expect(finishedButton).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByText('finished worker line')).not.toBeInTheDocument();

    await user.click(finishedButton);

    expect(finishedButton).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByText('finished worker line')).toBeInTheDocument();
    expect(screen.queryByText('running worker line')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /task-live/i })).not.toBeInTheDocument();
    expect(fetchRunLogsForTaskSpy).toHaveBeenCalledWith('run-logs-test', 'main');
    expect(fetchRunLogsForTaskSpy).toHaveBeenCalledWith('run-logs-test', 'task-live');
    expect(fetchRunLogsForTaskSpy).toHaveBeenCalledWith('run-logs-test', 'task-done');
  });

  it('paginates finished streams and scales the timeline from the visible page only', async () => {
    const user = userEvent.setup();
    const mainStream = buildStream('main', 'completed', {
      order: 0,
      endedAt: '2026-03-10T10:00:02Z',
      completedAt: '2026-03-10T10:00:02Z',
    });
    const finishedStreams = Array.from({ length: 21 }, (_, index) => {
      const taskNumber = index + 1;
      const startSecond = index < 20 ? index * 2 : 120;
      const endSecond = index < 20 ? startSecond + 2 : startSecond + 10;
      const isoPrefix = '2026-03-10T10:';
      const startedAt = `${isoPrefix}${String(Math.floor(startSecond / 60)).padStart(2, '0')}:${String(startSecond % 60).padStart(2, '0')}Z`;
      const endedAt = `${isoPrefix}${String(Math.floor(endSecond / 60)).padStart(2, '0')}:${String(endSecond % 60).padStart(2, '0')}Z`;

      return buildStream(`task-${taskNumber}`, taskNumber === 20 ? 'failed' : 'completed', {
        order: taskNumber,
        startedAt,
        endedAt,
        completedAt: taskNumber === 20 ? null : endedAt,
        failedAt: taskNumber === 20 ? endedAt : null,
        durationSeconds: index < 20 ? 2 : 10,
      });
    });

    vi.spyOn(api, 'fetchRunLogs').mockResolvedValue(
      buildSummary('run-logs-test', [mainStream, ...finishedStreams]),
    );
    vi.spyOn(api, 'fetchRunLogsForTask').mockImplementation(async (_runId, taskId) => {
      const stream =
        taskId === 'main' ? mainStream : finishedStreams.find((item) => item.taskId === taskId)!;
      return buildStreamResponse(taskId, [`${taskId} line`], stream, [
        'main',
        ...finishedStreams.map((item) => item.taskId),
      ]);
    });

    const view = render(<LogViewer runId="run-logs-test" runStatus="completed" />);

    await screen.findByRole('log', { name: 'main 的日志条目' });
    await user.click(screen.getByRole('tab', { name: '已结束 (21)' }));

    expect(await screen.findByText('第 1 / 2 页')).toBeInTheDocument();
    const firstPageButtons = view.container.querySelectorAll('[id^="finished-log-trigger-"]');
    expect(firstPageButtons).toHaveLength(20);
    expect(view.container.querySelector('#finished-log-trigger-task-1')).not.toBeNull();
    expect(view.container.querySelector('#finished-log-trigger-task-20')).not.toBeNull();
    expect(view.container.querySelector('#finished-log-trigger-task-21')).toBeNull();

    const firstBar = view.container.querySelector('#finished-log-trigger-task-1 .run-log-row__bar');
    expect(firstBar).not.toBeNull();
    expect(firstBar).toHaveAttribute('style', expect.stringContaining('width: 5%'));

    await user.click(screen.getByRole('button', { name: '下一页' }));

    expect(await screen.findByText('第 2 / 2 页')).toBeInTheDocument();
    expect(view.container.querySelector('#finished-log-trigger-task-21')).not.toBeNull();
    expect(view.container.querySelector('#finished-log-trigger-task-1')).toBeNull();

    const lastBar = view.container.querySelector('#finished-log-trigger-task-21 .run-log-row__bar');
    expect(lastBar).not.toBeNull();
    expect(lastBar).toHaveAttribute('style', expect.stringContaining('width: 100%'));
    expect(view.container.querySelectorAll('.run-log-row').length).toBe(1);
  });

  it('keeps the main stream accessible when it is the only available stream', async () => {
    const mainStream = buildStream('main', 'completed', {
      order: 0,
      startedAt: '2026-03-10T10:00:00Z',
      endedAt: '2026-03-10T10:00:05Z',
      completedAt: '2026-03-10T10:00:05Z',
      totalEntryCount: 0,
      bufferedEntryCount: 0,
      firstEntryAt: null,
      lastEntryAt: null,
    });

    vi.spyOn(api, 'fetchRunLogs').mockResolvedValue(buildSummary('run-logs-test', [mainStream]));
    vi.spyOn(api, 'fetchRunLogsForTask').mockResolvedValue(
      buildStreamResponse('main', [], mainStream, ['main']),
    );

    render(<LogViewer runId="run-logs-test" runStatus="completed" />);

    await waitFor(() => {
      expect(screen.queryByText('正在加载日志条目...')).not.toBeInTheDocument();
    });

    expect(screen.getByRole('tab', { name: '主日志' })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByText('未捕获到主协调器日志。')).toBeInTheDocument();
    expect(
      screen.getByText('运行结束时，主协调器流尚未产生可缓冲的日志输出。'),
    ).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: '运行中' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: '已结束' })).toBeInTheDocument();
  });

  it('moves pending workers into the finished view once the run is terminal', async () => {
    const user = userEvent.setup();
    const mainStream = buildStream('main', 'completed', {
      order: 0,
      endedAt: '2026-03-10T10:00:05Z',
      completedAt: '2026-03-10T10:00:05Z',
    });
    const pendingStream = buildStream('task-pending', 'pending', {
      order: 1,
      totalEntryCount: 0,
      bufferedEntryCount: 0,
      firstEntryAt: null,
      lastEntryAt: null,
    });

    vi.spyOn(api, 'fetchRunLogs').mockResolvedValue(
      buildSummary('run-logs-test', [mainStream, pendingStream]),
    );
    vi.spyOn(api, 'fetchRunLogsForTask').mockImplementation(async (_runId, taskId) => {
      const stream = taskId === 'main' ? mainStream : pendingStream;
      return buildStreamResponse(taskId, [], stream, ['main', 'task-pending']);
    });

    render(<LogViewer runId="run-logs-test" runStatus="completed" />);

    expect(await screen.findByRole('tabpanel')).toHaveAttribute(
      'aria-labelledby',
      'run-log-tab-main',
    );

    await user.click(screen.getByRole('tab', { name: '运行中' }));
    expect(screen.getByText('当前没有运行中的工作线程。')).toBeInTheDocument();

    await user.click(screen.getByRole('tab', { name: '已结束 (1)' }));
    const pendingButton = screen.getByRole('button', { name: /task-pending/i });
    expect(pendingButton).toHaveTextContent('等待中');
    expect(pendingButton).toHaveAttribute('aria-expanded', 'false');
    expect(pendingButton).toHaveAttribute('aria-controls', 'finished-log-panel-task-pending');
  });

  it('keeps polling the currently selected detail stream in the active view', async () => {
    const mainStream = buildStream('main', 'running', {
      order: 0,
      totalEntryCount: 1,
      bufferedEntryCount: 1,
    });
    const runningStream = buildStream('task-live', 'running', {
      order: 1,
      startedAt: '2026-03-10T10:00:01Z',
      lastEntryAt: '2026-03-10T10:00:03Z',
      totalEntryCount: 2,
      bufferedEntryCount: 2,
    });

    vi.spyOn(api, 'fetchRunLogs').mockImplementation(async () =>
      buildSummary('run-logs-test', [mainStream, runningStream]),
    );

    let taskLiveCalls = 0;
    const fetchRunLogsForTaskSpy = vi
      .spyOn(api, 'fetchRunLogsForTask')
      .mockImplementation(async (_runId, taskId) => {
        if (taskId === 'main') {
          return buildStreamResponse('main', ['main line'], mainStream, ['main', 'task-live']);
        }

        taskLiveCalls += 1;

        return buildStreamResponse(
          'task-live',
          taskLiveCalls > 1 ? ['first worker line', 'second worker line'] : ['first worker line'],
          {
            ...runningStream,
            totalEntryCount: taskLiveCalls > 1 ? 3 : 2,
            bufferedEntryCount: taskLiveCalls > 1 ? 3 : 2,
          },
          ['main', 'task-live'],
        );
      });

    render(<LogViewer runId="run-logs-test" runStatus="running" />);

    await act(async () => {
      await Promise.resolve();
    });

    await act(async () => {
      fireEvent.click(screen.getByRole('tab', { name: '运行中 (1)' }));
      await Promise.resolve();
    });

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /task-live/i }));
      await Promise.resolve();
    });

    await waitFor(
      () => {
        expect(screen.getByText('first worker line')).toBeInTheDocument();
      },
      { timeout: 2000 },
    );

    await waitFor(
      () => {
        expect(fetchRunLogsForTaskSpy).toHaveBeenCalledWith('run-logs-test', 'task-live');
        expect(screen.getByText('second worker line')).toBeInTheDocument();
      },
      { timeout: 2000 },
    );

    expect(fetchRunLogsForTaskSpy).toHaveBeenCalledWith('run-logs-test', 'main');
    expect(screen.queryByText('main line')).not.toBeInTheDocument();
  });
});
