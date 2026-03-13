import { useEffect, useMemo, useRef, useState } from 'react';

import {
  fetchRunLogs,
  fetchRunLogsForTask,
  type RunLogEntry,
  type RunLogStream,
  type RunLogsStreamResponse,
  type RunLogStreamsSummary,
} from '../lib/api';

type LogViewerProps = {
  runId: string;
  runStatus: string;
};

type LogViewerView = 'main' | 'running' | 'finished';

type AxisRange = {
  min: number | null;
  max: number | null;
  span: number;
};

type TimelineView = Exclude<LogViewerView, 'main'>;

const LIVE_LOG_POLL_MS = 1000;
const AUTO_SCROLL_THRESHOLD_PX = 24;
const FINISHED_STREAMS_PAGE_SIZE = 20;

function formatTaskLabel(taskId: string): string {
  return taskId === 'main' ? 'main' : taskId;
}

function formatStatusLabel(value: string): string {
  if (value.length === 0) {
    return '未知';
  }

  const labels: Record<string, string> = {
    pending: '等待中',
    running: '运行中',
    completed: '已完成',
    failed: '失败',
  };

  return labels[value] ?? value;
}

function formatTimestamp(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }

  return date.toLocaleTimeString([], {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });
}

function formatAxisTimestamp(value: number | null): string {
  if (value === null) {
    return '--:--:--';
  }

  return formatTimestamp(new Date(value).toISOString());
}

function formatDuration(value: number | null | undefined): string {
  if (typeof value !== 'number' || Number.isNaN(value)) {
    return '实时';
  }

  return `${value.toFixed(value >= 10 ? 0 : 1)}s`;
}

function formatStreamDuration(stream: RunLogStream, runStatus: string): string {
  if (typeof stream.durationSeconds === 'number' && !Number.isNaN(stream.durationSeconds)) {
    return formatDuration(stream.durationSeconds);
  }

  if (stream.endedAt || stream.completedAt || stream.failedAt) {
    return '已结束';
  }

  if ((runStatus === 'completed' || runStatus === 'failed') && stream.status !== 'pending') {
    return '已结束';
  }

  return '实时';
}

function toMillis(value: string | null | undefined): number | null {
  if (!value) {
    return null;
  }

  const parsed = Date.parse(value);
  return Number.isNaN(parsed) ? null : parsed;
}

function buildFallbackStream(taskId: string, summary: RunLogStreamsSummary | null): RunLogStream {
  return {
    taskId,
    order: summary?.taskIds.indexOf(taskId) ?? 0,
    status: taskId === 'main' ? 'running' : 'pending',
    startedAt: null,
    completedAt: null,
    failedAt: null,
    endedAt: null,
    durationSeconds: null,
    firstEntryAt: null,
    lastEntryAt: null,
    bufferedEntryCount: summary?.counts[taskId] ?? 0,
    totalEntryCount: summary?.counts[taskId] ?? 0,
  };
}

function getStreamStatusTone(status: string): 'success' | 'error' | 'running' {
  if (status === 'completed') {
    return 'success';
  }

  if (status === 'failed') {
    return 'error';
  }

  return 'running';
}

function normalizeStreamStatus(stream: RunLogStream, runStatus: string): string {
  if (runStatus === 'completed' && stream.status === 'running') {
    return 'completed';
  }

  if (runStatus === 'failed' && stream.status === 'running') {
    return 'failed';
  }

  return stream.status;
}

function getStreamStatusBucket(status: string, runStatus: string): number {
  if (runStatus === 'completed' || runStatus === 'failed') {
    return 0;
  }

  return status === 'completed' || status === 'failed' ? 1 : 0;
}

function getStreamLogStateLabel(stream: RunLogStream, runStatus: string): string {
  if (stream.totalEntryCount > 0) {
    return `${stream.totalEntryCount} 条日志`;
  }

  if (stream.status === 'pending') {
    return '等待启动';
  }

  if (stream.status === 'completed') {
    return '未捕获到日志';
  }

  if (stream.status === 'failed') {
    return '日志前即失败';
  }

  if (runStatus === 'completed' || runStatus === 'failed') {
    return '运行结束但无日志';
  }

  return '等待首条日志';
}

function getEmptyStateCopy(
  stream: RunLogStream,
  runStatus: string,
): { title: string; detail: string } {
  if (stream.taskId === 'main') {
    if (stream.totalEntryCount > 0) {
      return {
        title: '当前没有可用的缓冲日志行。',
        detail: '该流报告过活动，但当前缓冲区为空。',
      };
    }

    if (runStatus === 'completed' || runStatus === 'failed') {
      return {
        title: '未捕获到主协调器日志。',
        detail: '运行结束时，主协调器流尚未产生可缓冲的日志输出。',
      };
    }

    return {
      title: '主协调器暂未输出日志。',
      detail: '主协调器流当前还没有输出任何可缓冲的日志内容。',
    };
  }

  if (stream.totalEntryCount > 0) {
    return {
      title: '当前没有可用的工作线程缓冲日志行。',
      detail: `工作线程 ${stream.taskId} 报告过活动，但当前缓冲区为空。`,
    };
  }

  if (stream.status === 'pending') {
    return {
      title: '工作线程尚未开始记录日志。',
      detail: `工作线程 ${stream.taskId} 尚未启动，因此没有可显示的缓冲输出。`,
    };
  }

  if (stream.status === 'completed') {
    return {
      title: '工作线程已完成，但没有缓冲日志。',
      detail: `工作线程 ${stream.taskId} 已完成任务，但未输出可缓冲的日志内容。`,
    };
  }

  if (stream.status === 'failed') {
    return {
      title: '工作线程在捕获日志前失败。',
      detail: `工作线程 ${stream.taskId} 在记录任何缓冲日志输出前就已停止。`,
    };
  }

  if (runStatus === 'completed' || runStatus === 'failed') {
    return {
      title: '运行结束时尚未捕获到工作线程日志。',
      detail: `在运行结束前，工作线程 ${stream.taskId} 没有留下可缓冲的日志输出。`,
    };
  }

  return {
    title: '工作线程正在等待输出日志。',
    detail: `工作线程 ${stream.taskId} 当前处于活动状态，但尚未输出可缓冲的日志内容。`,
  };
}

function getStreamEnd(stream: RunLogStream, now: number): number | null {
  return (
    toMillis(stream.endedAt) ??
    toMillis(stream.completedAt) ??
    toMillis(stream.lastEntryAt) ??
    (toMillis(stream.startedAt) !== null ? now : null)
  );
}

function isPanelNearBottom(panel: HTMLDivElement): boolean {
  return panel.scrollHeight - (panel.scrollTop + panel.clientHeight) <= AUTO_SCROLL_THRESHOLD_PX;
}

function buildAxisRange(streamItems: RunLogStream[], now: number): AxisRange {
  const starts = streamItems
    .map((stream) => toMillis(stream.startedAt))
    .filter((value): value is number => value !== null);
  const ends = streamItems
    .map((stream) => getStreamEnd(stream, now))
    .filter((value): value is number => value !== null);

  const min = starts.length > 0 ? Math.min(...starts) : null;
  const max = ends.length > 0 ? Math.max(...ends) : min;

  if (min === null || max === null) {
    return { min: null, max: null, span: 1 };
  }

  return { min, max, span: Math.max(max - min, 1) };
}

function getViewEmptyState(view: Exclude<LogViewerView, 'main'>): {
  title: string;
  detail: string;
} {
  if (view === 'running') {
    return {
      title: '当前没有运行中的工作线程。',
      detail: '新的工作线程启动后，会在这里显示实时状态和缓冲日志。',
    };
  }

  return {
    title: '当前没有已结束的工作线程。',
    detail: '已完成或失败的工作线程会按页展示在这里。',
  };
}

function isTerminalRun(runStatus: string): boolean {
  return runStatus === 'completed' || runStatus === 'failed';
}

function isRunningStream(stream: RunLogStream, runStatus: string): boolean {
  if (isTerminalRun(runStatus)) {
    return false;
  }

  return stream.status === 'running' || stream.status === 'pending';
}

function isFinishedStream(stream: RunLogStream, runStatus: string): boolean {
  return stream.taskId !== 'main' && !isRunningStream(stream, runStatus);
}

export function LogViewer({ runId, runStatus }: LogViewerProps) {
  const [streams, setStreams] = useState<RunLogStreamsSummary | null>(null);
  const [isSummaryLoading, setIsSummaryLoading] = useState(true);
  const [summaryError, setSummaryError] = useState<string | null>(null);
  const [currentView, setCurrentView] = useState<LogViewerView>('main');
  const [selectedRunningTaskId, setSelectedRunningTaskId] = useState<string | null>(null);
  const [selectedFinishedTaskId, setSelectedFinishedTaskId] = useState<string | null>(null);
  const [finishedPage, setFinishedPage] = useState(0);
  const [entriesByTaskId, setEntriesByTaskId] = useState<Record<string, RunLogEntry[]>>({});
  const [streamByTaskId, setStreamByTaskId] = useState<Record<string, RunLogStream | null>>({});
  const [loadingByTaskId, setLoadingByTaskId] = useState<Record<string, boolean>>({});
  const [errorByTaskId, setErrorByTaskId] = useState<Record<string, string | null>>({});
  const logPanelRefs = useRef<Record<string, HTMLDivElement | null>>({});
  const latestStreamsRef = useRef<RunLogStreamsSummary | null>(null);
  const previousEntryCountByTaskId = useRef<Record<string, number>>({});
  const shouldAutoScrollByTaskId = useRef<Record<string, boolean>>({});
  const isLiveRun = !isTerminalRun(runStatus);

  useEffect(() => {
    if (runId.length === 0) {
      return;
    }

    setStreams(null);
    setIsSummaryLoading(true);
    setSummaryError(null);
    setCurrentView('main');
    setSelectedRunningTaskId(null);
    setSelectedFinishedTaskId(null);
    setFinishedPage(0);
    setEntriesByTaskId({});
    setStreamByTaskId({});
    setLoadingByTaskId({});
    setErrorByTaskId({});
    logPanelRefs.current = {};
    latestStreamsRef.current = null;
    previousEntryCountByTaskId.current = {};
    shouldAutoScrollByTaskId.current = {};
  }, [runId]);

  useEffect(() => {
    latestStreamsRef.current = streams;
  }, [streams]);

  useEffect(() => {
    let active = true;
    let intervalId: number | null = null;

    async function loadSummary(showLoading = false) {
      if (showLoading) {
        setIsSummaryLoading(true);
      }

      try {
        const response = await fetchRunLogs(runId);
        if (!active) {
          return;
        }

        setStreams(response.streams);
        setSummaryError(null);
      } catch (loadError) {
        if (!active) {
          return;
        }

        setSummaryError(loadError instanceof Error ? loadError.message : '无法加载运行日志。');
      } finally {
        if (active && showLoading) {
          setIsSummaryLoading(false);
        }
      }
    }

    void loadSummary(true);

    if (isLiveRun) {
      intervalId = window.setInterval(() => {
        void loadSummary(false);
      }, LIVE_LOG_POLL_MS);
    }

    return () => {
      active = false;
      if (intervalId !== null) {
        window.clearInterval(intervalId);
      }
    };
  }, [isLiveRun, runId]);

  const taskIds = useMemo(() => {
    return streams?.taskIds.length ? streams.taskIds : ['main'];
  }, [streams]);

  const streamItems = useMemo(() => {
    return taskIds
      .map((taskId, index) => {
        const stream = streams?.byTaskId[taskId] ?? buildFallbackStream(taskId, streams);
        const normalizedStatus = normalizeStreamStatus(stream, runStatus);
        return {
          taskId,
          index,
          sortOrder: typeof stream.order === 'number' ? stream.order : index,
          stream: {
            ...stream,
            status: normalizedStatus,
          },
        };
      })
      .sort((left, right) => {
        const bucketDiff =
          getStreamStatusBucket(left.stream.status, runStatus) -
          getStreamStatusBucket(right.stream.status, runStatus);

        if (bucketDiff !== 0) {
          return bucketDiff;
        }

        if (left.sortOrder !== right.sortOrder) {
          return left.sortOrder - right.sortOrder;
        }

        return left.index - right.index;
      })
      .map((item) => item.stream);
  }, [runStatus, streams, taskIds]);

  const mainStream = useMemo(() => {
    return (
      streamItems.find((stream) => stream.taskId === 'main') ?? buildFallbackStream('main', streams)
    );
  }, [streamItems, streams]);

  const runningStreams = useMemo(() => {
    return streamItems.filter(
      (stream) => stream.taskId !== 'main' && isRunningStream(stream, runStatus),
    );
  }, [runStatus, streamItems]);

  const finishedStreams = useMemo(() => {
    return streamItems.filter((stream) => isFinishedStream(stream, runStatus));
  }, [runStatus, streamItems]);

  const finishedPageCount = Math.max(
    1,
    Math.ceil(finishedStreams.length / FINISHED_STREAMS_PAGE_SIZE),
  );

  useEffect(() => {
    setFinishedPage((current) => Math.min(current, finishedPageCount - 1));
  }, [finishedPageCount]);

  const visibleFinishedStreams = useMemo(() => {
    const startIndex = finishedPage * FINISHED_STREAMS_PAGE_SIZE;
    return finishedStreams.slice(startIndex, startIndex + FINISHED_STREAMS_PAGE_SIZE);
  }, [finishedPage, finishedStreams]);

  useEffect(() => {
    if (runningStreams.length === 0) {
      setSelectedRunningTaskId(null);
      return;
    }

    setSelectedRunningTaskId((current) =>
      current && runningStreams.some((stream) => stream.taskId === current) ? current : null,
    );
  }, [runningStreams]);

  useEffect(() => {
    if (visibleFinishedStreams.length === 0) {
      setSelectedFinishedTaskId(null);
      return;
    }

    setSelectedFinishedTaskId((current) =>
      current && visibleFinishedStreams.some((stream) => stream.taskId === current)
        ? current
        : null,
    );
  }, [visibleFinishedStreams]);

  const activeDetailTaskIds = useMemo(() => {
    if (currentView === 'main') {
      return ['main'];
    }

    if (currentView === 'running' && selectedRunningTaskId) {
      return [selectedRunningTaskId];
    }

    if (currentView === 'finished' && selectedFinishedTaskId) {
      return [selectedFinishedTaskId];
    }

    return [];
  }, [currentView, selectedFinishedTaskId, selectedRunningTaskId]);

  const now = Date.now();
  const runningAxisRange = useMemo(
    () => buildAxisRange(runningStreams, now),
    [now, runningStreams],
  );
  const finishedAxisRange = useMemo(
    () => buildAxisRange(visibleFinishedStreams, now),
    [now, visibleFinishedStreams],
  );

  useEffect(() => {
    if (isSummaryLoading || activeDetailTaskIds.length === 0) {
      return;
    }

    let active = true;
    let intervalId: number | null = null;

    async function loadEntries(showLoading = false) {
      if (showLoading) {
        setLoadingByTaskId((current) => {
          const next = { ...current };
          activeDetailTaskIds.forEach((taskId) => {
            next[taskId] = true;
          });
          return next;
        });
      }

      const responses: Array<
        { taskId: string; response: RunLogsStreamResponse } | { taskId: string; error: string }
      > = await Promise.all(
        activeDetailTaskIds.map(async (taskId) => {
          try {
            const response = await fetchRunLogsForTask(runId, taskId);
            return { taskId, response };
          } catch (loadError) {
            return {
              taskId,
              error: loadError instanceof Error ? loadError.message : '无法加载日志流详情。',
            };
          }
        }),
      );

      if (!active) {
        return;
      }

      setEntriesByTaskId((current) => {
        const next = { ...current };
        responses.forEach((result) => {
          if ('response' in result) {
            next[result.taskId] = result.response.entries;
          }
        });
        return next;
      });

      setStreamByTaskId((current) => {
        const next = { ...current };
        responses.forEach((result) => {
          if ('response' in result) {
            next[result.taskId] =
              result.response.stream ?? latestStreamsRef.current?.byTaskId[result.taskId] ?? null;
          }
        });
        return next;
      });

      setErrorByTaskId((current) => {
        const next = { ...current };
        responses.forEach((result) => {
          next[result.taskId] = 'error' in result ? (result.error ?? '无法加载日志流详情。') : null;
        });
        return next;
      });

      setLoadingByTaskId((current) => {
        const next = { ...current };
        activeDetailTaskIds.forEach((taskId) => {
          next[taskId] = false;
        });
        return next;
      });
    }

    void loadEntries(true);

    if (isLiveRun) {
      intervalId = window.setInterval(() => {
        void loadEntries(false);
      }, LIVE_LOG_POLL_MS);
    }

    return () => {
      active = false;
      if (intervalId !== null) {
        window.clearInterval(intervalId);
      }
    };
  }, [activeDetailTaskIds, isLiveRun, isSummaryLoading, runId]);

  useEffect(() => {
    activeDetailTaskIds.forEach((taskId) => {
      const panel = logPanelRefs.current[taskId];
      const entryCount = entriesByTaskId[taskId]?.length ?? 0;

      if (!panel || loadingByTaskId[taskId]) {
        return;
      }

      const previousEntryCount = previousEntryCountByTaskId.current[taskId] ?? 0;
      const shouldAutoScroll =
        previousEntryCount === 0 || (shouldAutoScrollByTaskId.current[taskId] ?? false);

      if (shouldAutoScroll && entryCount >= 0) {
        panel.scrollTop = panel.scrollHeight;
        shouldAutoScrollByTaskId.current[taskId] = true;
      }

      previousEntryCountByTaskId.current[taskId] = entryCount;
    });
  }, [activeDetailTaskIds, entriesByTaskId, loadingByTaskId]);

  function renderLogPanel(stream: RunLogStream, panelId: string, labelledBy?: string) {
    const detailStream = streamByTaskId[stream.taskId] ?? null;
    const displayStream = {
      ...(detailStream ?? stream),
      status: normalizeStreamStatus(detailStream ?? stream, runStatus),
    };
    const entries = entriesByTaskId[stream.taskId] ?? [];
    const isEntriesLoading = loadingByTaskId[stream.taskId] ?? false;
    const entryError = errorByTaskId[stream.taskId];
    const emptyState = getEmptyStateCopy(displayStream, runStatus);

    return (
      <section
        id={panelId}
        className="run-log-viewer__detail"
        aria-label={`${displayStream.taskId} 的日志`}
        aria-labelledby={labelledBy}
      >
        {isEntriesLoading ? <p className="muted-copy">正在加载日志条目...</p> : null}
        {!isEntriesLoading && entryError ? <p role="alert">{entryError}</p> : null}
        {!isEntriesLoading && !entryError ? (
          entries.length > 0 ? (
            <div
              ref={(node) => {
                logPanelRefs.current[stream.taskId] = node;
              }}
              className="run-log-terminal"
              role="log"
              aria-live="polite"
              aria-label={`${displayStream.taskId} 的日志条目`}
              onScroll={(event) => {
                shouldAutoScrollByTaskId.current[stream.taskId] = isPanelNearBottom(
                  event.currentTarget,
                );
              }}
            >
              {entries.map((entry) => (
                <div key={`${entry.taskId}-${entry.sequence}`} className="run-log-line">
                  <span className="run-log-line__time">{formatTimestamp(entry.timestamp)}</span>
                  <span className="run-log-line__level">{entry.level}</span>
                  <code>{entry.message}</code>
                </div>
              ))}
            </div>
          ) : (
            <div className="run-log-terminal run-log-terminal--empty">
              <strong>{emptyState.title}</strong>
              <p className="muted-copy">{emptyState.detail}</p>
            </div>
          )
        ) : null}
      </section>
    );
  }

  function renderTimelineRows(
    view: TimelineView,
    streamList: RunLogStream[],
    axisRange: AxisRange,
    selectedTaskId: string | null,
    onSelectTaskId: (taskId: string | null) => void,
  ) {
    const axisMid = axisRange.min === null ? null : axisRange.min + axisRange.span / 2;

    return (
      <div className="run-log-timeline">
        <div className="run-log-timeline__axis" aria-hidden="true">
          <span className="run-log-timeline__axis-spacer" />
          <span className="run-log-timeline__axis-labels">
            <span>{formatAxisTimestamp(axisRange.min)}</span>
            <span>{formatAxisTimestamp(axisMid)}</span>
            <span>{formatAxisTimestamp(axisRange.max)}</span>
          </span>
          <span className="run-log-timeline__axis-spacer" />
        </div>

        <ul className="run-log-timeline__rows">
          {streamList.map((stream) => {
            const isSelected = selectedTaskId === stream.taskId;
            const detailPanelId = `${view}-log-panel-${stream.taskId}`;
            const buttonId = `${view}-log-trigger-${stream.taskId}`;
            const start = toMillis(stream.startedAt);
            const end = getStreamEnd(stream, now);
            const axisStart = axisRange.min;
            const hasBar = axisStart !== null && start !== null && end !== null;
            const offset = hasBar ? ((start - axisStart) / axisRange.span) * 100 : 0;
            const width = hasBar ? Math.max(((end - start) / axisRange.span) * 100, 0.6) : 0;

            return (
              <li key={stream.taskId} className="run-log-row-group">
                <button
                  type="button"
                  id={buttonId}
                  className={isSelected ? 'run-log-row run-log-row--expanded' : 'run-log-row'}
                  aria-expanded={isSelected}
                  aria-controls={detailPanelId}
                  onClick={() => onSelectTaskId(isSelected ? null : stream.taskId)}
                >
                  <span className="run-log-row__info">
                    <span className="run-log-row__title">
                      <strong title={stream.taskId}>{formatTaskLabel(stream.taskId)}</strong>
                      <span
                        className={`worker-pill worker-pill--${getStreamStatusTone(stream.status)}`}
                      >
                        {formatStatusLabel(stream.status)}
                      </span>
                    </span>
                    <span className="run-log-row__stats">
                      <span>{getStreamLogStateLabel(stream, runStatus)}</span>
                      <span>{formatStreamDuration(stream, runStatus)}</span>
                      <span>工作线程</span>
                    </span>
                  </span>

                  <span className="run-log-row__timeline" aria-hidden="true">
                    <span className="run-log-row__track" />
                    {hasBar ? (
                      <span
                        className="run-log-row__bar"
                        style={{
                          left: `${offset}%`,
                          width: `${Math.min(width, 100 - offset)}%`,
                        }}
                      />
                    ) : (
                      <span className="run-log-row__idle">暂无时间数据</span>
                    )}
                  </span>

                  <span className="run-log-row__toggle">
                    {isSelected ? '收起日志' : '查看日志'}
                  </span>
                </button>

                {isSelected ? renderLogPanel(stream, detailPanelId, buttonId) : null}
              </li>
            );
          })}
        </ul>
      </div>
    );
  }

  const mainButtonLabel = '主日志';
  const runningButtonLabel = `运行中${runningStreams.length > 0 ? ` (${runningStreams.length})` : ''}`;
  const finishedButtonLabel = `已结束${finishedStreams.length > 0 ? ` (${finishedStreams.length})` : ''}`;
  const activeMainStream = {
    ...(streamByTaskId.main ?? mainStream),
    status: normalizeStreamStatus(streamByTaskId.main ?? mainStream, runStatus),
  };
  const activeTabId = `run-log-tab-${currentView}`;
  const activePanelId = `run-log-tabpanel-${currentView}`;

  return (
    <section className="run-card" aria-labelledby="run-logs-panel">
      <div className="run-card__header run-card__header--compact">
        <div>
          <p className="eyebrow">日志</p>
          <h3 id="run-logs-panel">日志查看器</h3>
        </div>
      </div>

      <p className="muted-copy run-log-viewer__hint">
        一次只显示一个视图，可在主日志、运行中和已结束之间切换。
      </p>

      <div className="run-log-viewer__toolbar-row">
        <div className="run-log-viewer__toolbar" role="tablist" aria-label="日志视图切换">
          <button
            type="button"
            id="run-log-tab-main"
            role="tab"
            aria-controls="run-log-tabpanel-main"
            aria-selected={currentView === 'main'}
            className={
              currentView === 'main'
                ? 'secondary-button run-log-viewer__tab run-log-viewer__tab--active'
                : 'secondary-button run-log-viewer__tab'
            }
            onClick={() => setCurrentView('main')}
          >
            {mainButtonLabel}
          </button>
          <button
            type="button"
            id="run-log-tab-running"
            role="tab"
            aria-controls="run-log-tabpanel-running"
            aria-selected={currentView === 'running'}
            className={
              currentView === 'running'
                ? 'secondary-button run-log-viewer__tab run-log-viewer__tab--active'
                : 'secondary-button run-log-viewer__tab'
            }
            onClick={() => setCurrentView('running')}
          >
            {runningButtonLabel}
          </button>
          <button
            type="button"
            id="run-log-tab-finished"
            role="tab"
            aria-controls="run-log-tabpanel-finished"
            aria-selected={currentView === 'finished'}
            className={
              currentView === 'finished'
                ? 'secondary-button run-log-viewer__tab run-log-viewer__tab--active'
                : 'secondary-button run-log-viewer__tab'
            }
            onClick={() => setCurrentView('finished')}
          >
            {finishedButtonLabel}
          </button>
        </div>

        {currentView === 'finished' && finishedStreams.length > 0 ? (
          <div className="run-log-viewer__pagination">
            <button
              type="button"
              className="run-log-viewer__pager-button"
              onClick={() => setFinishedPage((current) => Math.max(current - 1, 0))}
              disabled={finishedPage === 0}
            >
              上一页
            </button>
            <span className="muted-copy">
              第 {finishedPage + 1} / {finishedPageCount} 页
            </span>
            <button
              type="button"
              className="run-log-viewer__pager-button"
              onClick={() =>
                setFinishedPage((current) => Math.min(current + 1, finishedPageCount - 1))
              }
              disabled={finishedPage >= finishedPageCount - 1}
            >
              下一页
            </button>
          </div>
        ) : null}
      </div>

      {isSummaryLoading ? <p className="muted-copy">正在加载日志流...</p> : null}
      {!isSummaryLoading && summaryError ? <p role="alert">{summaryError}</p> : null}

      {!isSummaryLoading && streams ? (
        <div
          id={activePanelId}
          className="run-log-viewer__content"
          role="tabpanel"
          aria-labelledby={activeTabId}
        >
          {currentView === 'main'
            ? renderLogPanel(activeMainStream, 'main-log-panel', 'run-log-tab-main')
            : null}

          {currentView === 'running'
            ? runningStreams.length > 0
              ? renderTimelineRows(
                  'running',
                  runningStreams,
                  runningAxisRange,
                  selectedRunningTaskId,
                  setSelectedRunningTaskId,
                )
              : (() => {
                  const emptyState = getViewEmptyState('running');
                  return (
                    <div className="run-log-terminal run-log-terminal--empty">
                      <strong>{emptyState.title}</strong>
                      <p className="muted-copy">{emptyState.detail}</p>
                    </div>
                  );
                })()
            : null}

          {currentView === 'finished' ? (
            finishedStreams.length > 0 ? (
              <>
                {renderTimelineRows(
                  'finished',
                  visibleFinishedStreams,
                  finishedAxisRange,
                  selectedFinishedTaskId,
                  setSelectedFinishedTaskId,
                )}
              </>
            ) : (
              (() => {
                const emptyState = getViewEmptyState('finished');
                return (
                  <div className="run-log-terminal run-log-terminal--empty">
                    <strong>{emptyState.title}</strong>
                    <p className="muted-copy">{emptyState.detail}</p>
                  </div>
                );
              })()
            )
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
