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

const LIVE_LOG_POLL_MS = 1000;
const AUTO_SCROLL_THRESHOLD_PX = 24;
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

function getEmptyStateCopy(stream: RunLogStream, runStatus: string): { title: string; detail: string } {
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

export function LogViewer({ runId, runStatus }: LogViewerProps) {
  const [streams, setStreams] = useState<RunLogStreamsSummary | null>(null);
  const [isSummaryLoading, setIsSummaryLoading] = useState(true);
  const [summaryError, setSummaryError] = useState<string | null>(null);
  const [expandedTaskIds, setExpandedTaskIds] = useState<Record<string, boolean>>({});
  const [entriesByTaskId, setEntriesByTaskId] = useState<Record<string, RunLogEntry[]>>({});
  const [streamByTaskId, setStreamByTaskId] = useState<Record<string, RunLogStream | null>>({});
  const [loadingByTaskId, setLoadingByTaskId] = useState<Record<string, boolean>>({});
  const [errorByTaskId, setErrorByTaskId] = useState<Record<string, string | null>>({});
  const logPanelRefs = useRef<Record<string, HTMLDivElement | null>>({});
  const latestStreamsRef = useRef<RunLogStreamsSummary | null>(null);
  const previousEntryCountByTaskId = useRef<Record<string, number>>({});
  const shouldAutoScrollByTaskId = useRef<Record<string, boolean>>({});
  const isLiveRun = runStatus !== 'completed' && runStatus !== 'failed';

  useEffect(() => {
    if (runId.length === 0) {
      return;
    }

    setStreams(null);
    setIsSummaryLoading(true);
    setSummaryError(null);
    setExpandedTaskIds({});
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

        const nextStreams = response.streams;

        setStreams(nextStreams);
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

  const expandedStreamIds = useMemo(
    () => taskIds.filter((taskId) => expandedTaskIds[taskId]).sort(),
    [expandedTaskIds, taskIds],
  );
  const expandedStreamIdsKey = useMemo(() => JSON.stringify(expandedStreamIds), [expandedStreamIds]);

  const now = Date.now();
  const axisRange = useMemo(() => {
    const starts = streamItems.map((stream) => toMillis(stream.startedAt)).filter((value) => value !== null);
    const ends = streamItems
      .map((stream) => getStreamEnd(stream, now))
      .filter((value) => value !== null);

    const min = starts.length > 0 ? Math.min(...starts) : null;
    const max = ends.length > 0 ? Math.max(...ends) : min;

    if (min === null || max === null) {
      return { min: null, max: null, span: 1 };
    }

    return { min, max, span: Math.max(max - min, 1) };
  }, [now, streamItems]);

  useEffect(() => {
    const expandedIds = JSON.parse(expandedStreamIdsKey) as string[];

    if (isSummaryLoading || expandedIds.length === 0) {
      return;
    }

    let active = true;
    let intervalId: number | null = null;

    async function loadExpandedEntries(showLoading = false) {
      if (showLoading) {
        setLoadingByTaskId((current) => {
          const next = { ...current };
          expandedIds.forEach((taskId) => {
            next[taskId] = true;
          });
          return next;
        });
      }

      const responses: Array<
        | { taskId: string; response: RunLogsStreamResponse }
        | { taskId: string; error: string }
      > = await Promise.all(
        expandedIds.map(async (taskId) => {
          try {
            const response = await fetchRunLogsForTask(runId, taskId);
            return { taskId, response };
          } catch (loadError) {
            return {
              taskId,
              error:
                loadError instanceof Error
                  ? loadError.message
                  : '无法加载日志流详情。',
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
            next[result.taskId] = 'error' in result ? result.error ?? '无法加载日志流详情。' : null;
        });
        return next;
      });

      setLoadingByTaskId((current) => {
        const next = { ...current };
        expandedIds.forEach((taskId) => {
          next[taskId] = false;
        });
        return next;
      });
    }

    void loadExpandedEntries(true);

    if (isLiveRun) {
      intervalId = window.setInterval(() => {
        void loadExpandedEntries(false);
      }, LIVE_LOG_POLL_MS);
    }

    return () => {
      active = false;
      if (intervalId !== null) {
        window.clearInterval(intervalId);
      }
    };
  }, [expandedStreamIdsKey, isLiveRun, isSummaryLoading, runId]);

  useEffect(() => {
    expandedStreamIds.forEach((taskId) => {
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
  }, [entriesByTaskId, expandedStreamIds, loadingByTaskId]);

  function toggleTask(taskId: string) {
    setExpandedTaskIds((current) => ({
      ...current,
      [taskId]: !current[taskId],
    }));
  }

  return (
    <section className="run-card" aria-labelledby="run-logs-panel">
      <div className="run-card__header run-card__header--compact">
        <div>
          <p className="eyebrow">日志</p>
          <h3 id="run-logs-panel">日志时间线</h3>
        </div>
      </div>

      <p className="muted-copy run-log-viewer__hint">
        展开任意流行即可查看其缓冲日志输出。
      </p>

      {isSummaryLoading ? <p className="muted-copy">正在加载日志流...</p> : null}
      {!isSummaryLoading && summaryError ? <p role="alert">{summaryError}</p> : null}

      {!isSummaryLoading && streams ? (
        <>
          <div className="run-log-timeline">
            <div className="run-log-timeline__axis" aria-hidden="true">
              <span>{formatAxisTimestamp(axisRange.min)}</span>
              <span>{formatAxisTimestamp(axisRange.min !== null ? axisRange.min + axisRange.span / 2 : null)}</span>
              <span>{formatAxisTimestamp(axisRange.max)}</span>
            </div>

            <ul className="run-log-timeline__rows">
              {streamItems.map((stream) => {
                const isExpanded = Boolean(expandedTaskIds[stream.taskId]);
                const panelId = `run-log-panel-${stream.taskId}`;
                const detailStream = streamByTaskId[stream.taskId] ?? null;
                const resolvedStream = detailStream ?? stream;
                const rowStream = {
                  ...stream,
                  status: normalizeStreamStatus(stream, runStatus),
                };
                const displayStream = {
                  ...resolvedStream,
                  status: normalizeStreamStatus(resolvedStream, runStatus),
                };
                const entries = entriesByTaskId[stream.taskId] ?? [];
                const isEntriesLoading = loadingByTaskId[stream.taskId] ?? false;
                const entryError = errorByTaskId[stream.taskId];
                const emptyState = getEmptyStateCopy(displayStream, runStatus);
                const start = toMillis(rowStream.startedAt);
                const end = getStreamEnd(rowStream, now);
                const hasBar = axisRange.min !== null && start !== null && end !== null;
                const offset = hasBar ? ((start - axisRange.min) / axisRange.span) * 100 : 0;
                const width = hasBar ? Math.max(((end - start) / axisRange.span) * 100, 0.6) : 0;

                return (
                  <li key={stream.taskId} className="run-log-row-group">
                    <button
                      type="button"
                      className={isExpanded ? 'run-log-row run-log-row--expanded' : 'run-log-row'}
                      aria-expanded={isExpanded}
                      aria-controls={panelId}
                      onClick={() => toggleTask(stream.taskId)}
                    >
                      <span className="run-log-row__info">
                        <span className="run-log-row__title">
                          <strong title={rowStream.taskId}>{formatTaskLabel(rowStream.taskId)}</strong>
                          <span className={`worker-pill worker-pill--${getStreamStatusTone(rowStream.status)}`}>
                            {formatStatusLabel(rowStream.status)}
                          </span>
                        </span>
                        <span className="run-log-row__stats">
                          <span>{getStreamLogStateLabel(rowStream, runStatus)}</span>
                          <span>{formatStreamDuration(rowStream, runStatus)}</span>
                          <span>{rowStream.taskId === 'main' ? '主协调器' : '工作线程'}</span>
                        </span>
                      </span>

                      <span className="run-log-row__timeline" aria-hidden="true">
                        <span className="run-log-row__track" />
                        {hasBar ? (
                          <span
                            className="run-log-row__bar"
                            style={{ left: `${offset}%`, width: `${Math.min(width, 100 - offset)}%` }}
                          />
                        ) : (
                          <span className="run-log-row__idle">暂无时间数据</span>
                        )}
                      </span>

                      <span className="run-log-row__toggle">{isExpanded ? '收起日志' : '展开日志'}</span>
                    </button>

                    {isExpanded ? (
                      <section id={panelId} className="run-log-row__panel" aria-label={`${displayStream.taskId} 的日志`}>
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
                    ) : null}
                  </li>
                );
              })}
            </ul>
          </div>
        </>
      ) : null}
    </section>
  );
}
