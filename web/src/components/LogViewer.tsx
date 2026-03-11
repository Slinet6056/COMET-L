import { useEffect, useMemo, useRef, useState } from 'react';

import {
  fetchRunLogs,
  fetchRunLogsForTask,
  type RunLogEntry,
  type RunLogStream,
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
  const [selectedTaskId, setSelectedTaskId] = useState('main');
  const [entries, setEntries] = useState<RunLogEntry[]>([]);
  const [selectedStream, setSelectedStream] = useState<RunLogStream | null>(null);
  const [isSummaryLoading, setIsSummaryLoading] = useState(true);
  const [isEntriesLoading, setIsEntriesLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const logPanelRef = useRef<HTMLDivElement | null>(null);
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
        const nextTaskIds = nextStreams.taskIds.length > 0 ? nextStreams.taskIds : ['main'];

        setStreams(nextStreams);
        setSelectedTaskId((current) => (nextTaskIds.includes(current) ? current : nextTaskIds[0]));
        setError(null);
      } catch (loadError) {
        if (!active) {
          return;
        }

        setError(loadError instanceof Error ? loadError.message : 'Unable to load run logs.');
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

  useEffect(() => {
    let active = true;
    let intervalId: number | null = null;

    async function loadEntries(showLoading = false) {
      if (isSummaryLoading || error) {
        return;
      }

      if (showLoading) {
        setIsEntriesLoading(true);
      }

      try {
        const response = await fetchRunLogsForTask(runId, selectedTaskId);
        if (!active) {
          return;
        }

        setEntries(response.entries);
        setSelectedStream(response.stream ?? streams?.byTaskId[selectedTaskId] ?? null);
        setError(null);
      } catch (loadError) {
        if (!active) {
          return;
        }

        setError(loadError instanceof Error ? loadError.message : 'Unable to load selected log stream.');
        setEntries([]);
        setSelectedStream(streams?.byTaskId[selectedTaskId] ?? null);
      } finally {
        if (active && showLoading) {
          setIsEntriesLoading(false);
        }
      }
    }

    void loadEntries(true);

    if (isLiveRun && !isSummaryLoading && !error) {
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
  }, [error, isLiveRun, isSummaryLoading, runId, selectedTaskId, streams]);

  const taskIds = useMemo(() => {
    return streams?.taskIds.length ? streams.taskIds : ['main'];
  }, [streams]);

  const streamItems = useMemo(() => {
    return taskIds.map((taskId) => streams?.byTaskId[taskId] ?? buildFallbackStream(taskId, streams));
  }, [streams, taskIds]);

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

  const hasWorkerStreams = taskIds.some((taskId) => taskId !== 'main');
  const entryCount = selectedStream?.bufferedEntryCount ?? streams?.counts[selectedTaskId] ?? entries.length;
  const maxEntriesPerStream = streams?.maxEntriesPerStream ?? 0;

  useEffect(() => {
    if (!logPanelRef.current || isEntriesLoading) {
      return;
    }

    logPanelRef.current.scrollTop = logPanelRef.current.scrollHeight;
  });

  return (
    <section className="run-card" aria-labelledby="run-logs-panel">
      <div className="run-card__header run-card__header--compact">
        <div>
          <p className="eyebrow">Logs</p>
          <h3 id="run-logs-panel">Log Timeline</h3>
        </div>
        <div className="run-log-viewer__meta">
          <span className="run-badge">Selected: {formatTaskLabel(selectedTaskId)}</span>
          <span className="run-badge">Visible: {entries.length}</span>
          {maxEntriesPerStream > 0 ? (
            <span className="run-badge">Buffered: {entryCount} / {maxEntriesPerStream}</span>
          ) : null}
        </div>
      </div>

      {isSummaryLoading ? <p className="muted-copy">Loading log streams...</p> : null}
      {!isSummaryLoading && error ? <p role="alert">{error}</p> : null}

      {!isSummaryLoading && !error ? (
        <>
          {hasWorkerStreams ? (
            <div className="run-log-timeline">
              <div className="run-log-timeline__axis" aria-hidden="true">
                <span>{formatAxisTimestamp(axisRange.min)}</span>
                <span>{formatAxisTimestamp(axisRange.min !== null ? axisRange.min + axisRange.span / 2 : null)}</span>
                <span>{formatAxisTimestamp(axisRange.max)}</span>
              </div>

              <ul className="run-log-timeline__rows">
                {streamItems.map((stream) => {
                  const isSelected = stream.taskId === selectedTaskId;
                  const start = toMillis(stream.startedAt);
                  const end = getStreamEnd(stream, now);
                  const hasBar = axisRange.min !== null && start !== null && end !== null;
                  const offset = hasBar ? ((start - axisRange.min) / axisRange.span) * 100 : 0;
                  const width = hasBar ? Math.max(((end - start) / axisRange.span) * 100, 1.2) : 0;

                  return (
                    <li key={stream.taskId}>
                      <button
                        type="button"
                        className={isSelected ? 'run-log-row run-log-row--selected' : 'run-log-row'}
                        aria-pressed={isSelected}
                        onClick={() => setSelectedTaskId(stream.taskId)}
                      >
                      <span className="run-log-row__info">
                        <span className="run-log-row__title">
                          <strong title={stream.taskId}>{formatTaskLabel(stream.taskId)}</strong>
                          <span className={`worker-pill worker-pill--${stream.status === 'failed' ? 'error' : stream.status === 'completed' ? 'success' : 'running'}`}>
                            {stream.status}
                          </span>
                        </span>
                        <span className="run-log-row__stats">
                          <span>{stream.totalEntryCount} entries</span>
                          <span>{formatDuration(stream.durationSeconds)}</span>
                          <span>{stream.taskId === 'main' ? 'coordinator' : 'worker'}</span>
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
                      </button>
                    </li>
                  );
                })}
              </ul>
            </div>
          ) : (
            <p className="muted-copy run-log-viewer__hint">
              Only the main log stream is available for this run.
            </p>
          )}

          {selectedStream ? (
            <div className="run-log-stream-meta">
              <span className="run-badge">Status: {selectedStream.status}</span>
              <span className="run-badge">Start: {selectedStream.startedAt ? formatTimestamp(selectedStream.startedAt) : 'pending'}</span>
              <span className="run-badge">Duration: {formatDuration(selectedStream.durationSeconds)}</span>
            </div>
          ) : null}

          {isEntriesLoading ? <p className="muted-copy">Loading log entries...</p> : null}

          {!isEntriesLoading ? (
            entries.length > 0 ? (
              <div
                ref={logPanelRef}
                id={`run-log-panel-${selectedTaskId}`}
                className="run-log-terminal"
                role="log"
                aria-live="polite"
                aria-label={`Log entries for ${selectedTaskId}`}
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
              <div
                ref={logPanelRef}
                id={`run-log-panel-${selectedTaskId}`}
                className="run-log-terminal run-log-terminal--empty"
              >
                <strong>No log entries yet.</strong>
                <p className="muted-copy">
                  {selectedTaskId === 'main'
                    ? 'The coordinator stream has not written any messages yet.'
                    : `Worker ${selectedTaskId} has not emitted logs yet.`}
                </p>
              </div>
            )
          ) : null}
        </>
      ) : null}
    </section>
  );
}
