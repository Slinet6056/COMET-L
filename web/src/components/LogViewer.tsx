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
function formatTaskLabel(taskId: string): string {
  return taskId === 'main' ? 'main' : taskId;
}

function formatStatusLabel(value: string): string {
  if (value.length === 0) {
    return 'Unknown';
  }

  return value.charAt(0).toUpperCase() + value.slice(1);
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
    return 'live';
  }

  return `${value.toFixed(value >= 10 ? 0 : 1)}s`;
}

function formatStreamDuration(stream: RunLogStream, runStatus: string): string {
  if (typeof stream.durationSeconds === 'number' && !Number.isNaN(stream.durationSeconds)) {
    return formatDuration(stream.durationSeconds);
  }

  if (stream.endedAt || stream.completedAt || stream.failedAt) {
    return 'ended';
  }

  if ((runStatus === 'completed' || runStatus === 'failed') && stream.status !== 'pending') {
    return 'ended';
  }

  return 'live';
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

function getStreamLogStateLabel(stream: RunLogStream, runStatus: string): string {
  if (stream.totalEntryCount > 0) {
    return `${stream.totalEntryCount} ${stream.totalEntryCount === 1 ? 'entry' : 'entries'}`;
  }

  if (stream.status === 'pending') {
    return 'Waiting for start';
  }

  if (stream.status === 'completed') {
    return 'No logs captured';
  }

  if (stream.status === 'failed') {
    return 'Failed before logs';
  }

  if (runStatus === 'completed' || runStatus === 'failed') {
    return 'Run finished without logs';
  }

  return 'Awaiting first log';
}

function getEmptyStateCopy(stream: RunLogStream, runStatus: string): { title: string; detail: string } {
  if (stream.taskId === 'main') {
    if (stream.totalEntryCount > 0) {
      return {
        title: 'No buffered log lines available.',
        detail: 'This stream reported activity, but the current buffer is empty.',
      };
    }

    if (runStatus === 'completed' || runStatus === 'failed') {
      return {
        title: 'No coordinator logs were captured.',
        detail: 'The run finished before the coordinator stream produced buffered log output.',
      };
    }

    return {
      title: 'No coordinator logs yet.',
      detail: 'The coordinator stream has not emitted any buffered log output yet.',
    };
  }

  if (stream.totalEntryCount > 0) {
    return {
      title: 'No buffered worker log lines available.',
      detail: `Worker ${stream.taskId} reported activity, but the current buffer is empty.`,
    };
  }

  if (stream.status === 'pending') {
    return {
      title: 'Worker has not started logging.',
      detail: `Worker ${stream.taskId} has not started yet, so there is no buffered output to show.`,
    };
  }

  if (stream.status === 'completed') {
    return {
      title: 'Worker completed without buffered logs.',
      detail: `Worker ${stream.taskId} finished its work without emitting buffered log output.`,
    };
  }

  if (stream.status === 'failed') {
    return {
      title: 'Worker failed before logs were captured.',
      detail: `Worker ${stream.taskId} stopped before any buffered log output was recorded.`,
    };
  }

  if (runStatus === 'completed' || runStatus === 'failed') {
    return {
      title: 'Run finished before worker logs were captured.',
      detail: `Worker ${stream.taskId} did not leave buffered log output before the run finished.`,
    };
  }

  return {
    title: 'Worker is waiting to emit logs.',
    detail: `Worker ${stream.taskId} is active, but it has not emitted buffered log output yet.`,
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
  const isLiveRun = runStatus !== 'completed' && runStatus !== 'failed';

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

        setSummaryError(loadError instanceof Error ? loadError.message : 'Unable to load run logs.');
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
    return taskIds.map((taskId) => streams?.byTaskId[taskId] ?? buildFallbackStream(taskId, streams));
  }, [streams, taskIds]);

  const expandedStreamIds = useMemo(
    () => taskIds.filter((taskId) => expandedTaskIds[taskId]),
    [expandedTaskIds, taskIds],
  );

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
    if (isSummaryLoading || summaryError || expandedStreamIds.length === 0) {
      return;
    }

    let active = true;
    let intervalId: number | null = null;

    async function loadExpandedEntries(showLoading = false) {
      if (showLoading) {
        setLoadingByTaskId((current) => {
          const next = { ...current };
          expandedStreamIds.forEach((taskId) => {
            next[taskId] = true;
          });
          return next;
        });
      }

      const responses: Array<
        | { taskId: string; response: RunLogsStreamResponse }
        | { taskId: string; error: string }
      > = await Promise.all(
        expandedStreamIds.map(async (taskId) => {
          try {
            const response = await fetchRunLogsForTask(runId, taskId);
            return { taskId, response };
          } catch (loadError) {
            return {
              taskId,
              error:
                loadError instanceof Error
                  ? loadError.message
                  : 'Unable to load log stream details.',
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
            next[result.taskId] = result.response.stream ?? streams?.byTaskId[result.taskId] ?? null;
          }
        });
        return next;
      });

      setErrorByTaskId((current) => {
        const next = { ...current };
        responses.forEach((result) => {
          next[result.taskId] = 'error' in result ? result.error ?? 'Unable to load log stream details.' : null;
        });
        return next;
      });

      setLoadingByTaskId((current) => {
        const next = { ...current };
        expandedStreamIds.forEach((taskId) => {
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
  }, [expandedStreamIds, isLiveRun, isSummaryLoading, runId, streams, summaryError]);

  useEffect(() => {
    expandedStreamIds.forEach((taskId) => {
      const panel = logPanelRefs.current[taskId];
      const entryCount = entriesByTaskId[taskId]?.length ?? 0;
      if (panel && !loadingByTaskId[taskId] && entryCount >= 0) {
        panel.scrollTop = panel.scrollHeight;
      }
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
          <p className="eyebrow">Logs</p>
          <h3 id="run-logs-panel">Log Timeline</h3>
        </div>
      </div>

      <p className="muted-copy run-log-viewer__hint">
        Expand any stream row to inspect its buffered log output.
      </p>

      {isSummaryLoading ? <p className="muted-copy">Loading log streams...</p> : null}
      {!isSummaryLoading && summaryError ? <p role="alert">{summaryError}</p> : null}

      {!isSummaryLoading && !summaryError ? (
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
                const resolvedStream = streamByTaskId[stream.taskId] ?? stream;
                const displayStream = {
                  ...resolvedStream,
                  status: normalizeStreamStatus(resolvedStream, runStatus),
                };
                const entries = entriesByTaskId[stream.taskId] ?? [];
                const isEntriesLoading = loadingByTaskId[stream.taskId] ?? false;
                const entryError = errorByTaskId[stream.taskId];
                const emptyState = getEmptyStateCopy(displayStream, runStatus);
                const start = toMillis(displayStream.startedAt);
                const end = getStreamEnd(displayStream, now);
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
                          <strong title={displayStream.taskId}>{formatTaskLabel(displayStream.taskId)}</strong>
                          <span className={`worker-pill worker-pill--${getStreamStatusTone(displayStream.status)}`}>
                            {formatStatusLabel(displayStream.status)}
                          </span>
                        </span>
                        <span className="run-log-row__stats">
                          <span>{getStreamLogStateLabel(displayStream, runStatus)}</span>
                          <span>{formatStreamDuration(displayStream, runStatus)}</span>
                          <span>{displayStream.taskId === 'main' ? 'coordinator' : 'worker'}</span>
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
                          <span className="run-log-row__idle">No timing yet</span>
                        )}
                      </span>

                      <span className="run-log-row__toggle">{isExpanded ? 'Hide logs' : 'Show logs'}</span>
                    </button>

                    {isExpanded ? (
                      <section id={panelId} className="run-log-row__panel" aria-label={`Logs for ${displayStream.taskId}`}>
                        {isEntriesLoading ? <p className="muted-copy">Loading log entries...</p> : null}
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
                              aria-label={`Log entries for ${displayStream.taskId}`}
                            >
                              {entries.map((entry) => (
                                <div key={`${entry.taskId}-${entry.sequence}`} className="run-log-line">
                                  <span className="run-log-line__time">{formatTimestamp(entry.timestamp)}</span>
                                  <span className="run-log-line__level">{entry.level}</span>
                                  <span className="run-log-line__logger">{entry.logger}</span>
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
