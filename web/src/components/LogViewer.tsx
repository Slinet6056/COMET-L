import { useEffect, useMemo, useRef, useState } from 'react';

import {
  fetchRunLogs,
  fetchRunLogsForTask,
  type RunLogEntry,
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

export function LogViewer({ runId, runStatus }: LogViewerProps) {
  const [streams, setStreams] = useState<RunLogStreamsSummary | null>(null);
  const [selectedTaskId, setSelectedTaskId] = useState('main');
  const [entries, setEntries] = useState<RunLogEntry[]>([]);
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
        setError(null);
      } catch (loadError) {
        if (!active) {
          return;
        }

        setError(loadError instanceof Error ? loadError.message : 'Unable to load selected log stream.');
        setEntries([]);
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
  }, [error, isLiveRun, isSummaryLoading, runId, selectedTaskId]);

  const taskIds = useMemo(() => {
    if (streams?.taskIds.length) {
      return streams.taskIds;
    }

    return ['main'];
  }, [streams]);

  const hasWorkerTabs = taskIds.some((taskId) => taskId !== 'main');
  const entryCount = streams?.counts[selectedTaskId] ?? entries.length;
  const maxEntriesPerStream = streams?.maxEntriesPerStream ?? 0;

  useEffect(() => {
    if (!logPanelRef.current || isEntriesLoading) {
      return;
    }

    logPanelRef.current.scrollTop = logPanelRef.current.scrollHeight;
  });

  return (
    <section className="run-card" aria-labelledby="run-logs-panel">
      <div className="run-card__header">
        <div>
          <p className="eyebrow">Logs</p>
          <h3 id="run-logs-panel">Log Viewer</h3>
        </div>
      </div>

      {isSummaryLoading ? <p className="muted-copy">Loading log streams...</p> : null}
      {!isSummaryLoading && error ? <p role="alert">{error}</p> : null}

      {!isSummaryLoading && !error ? (
        <>
          <div className="run-log-viewer__toolbar">
            {hasWorkerTabs ? (
              <div className="run-log-tabs" role="tablist" aria-label="Log streams">
                {taskIds.map((taskId) => {
                  const isSelected = taskId === selectedTaskId;
                  return (
                    <button
                      key={taskId}
                      type="button"
                      role="tab"
                      aria-selected={isSelected}
                      className={isSelected ? 'run-log-tab run-log-tab--active' : 'run-log-tab'}
                      onClick={() => setSelectedTaskId(taskId)}
                    >
                      {formatTaskLabel(taskId)}
                    </button>
                  );
                })}
              </div>
            ) : (
              <p className="muted-copy run-log-viewer__hint">
                Only the main log stream is available for this run.
              </p>
            )}

            <div className="run-log-viewer__meta">
              <span className="run-badge">Selected: {formatTaskLabel(selectedTaskId)}</span>
              <span className="run-badge">Visible: {entries.length}</span>
              {maxEntriesPerStream > 0 ? (
                <span className="run-badge">Buffered: {entryCount} / {maxEntriesPerStream}</span>
              ) : null}
            </div>
          </div>

          {isEntriesLoading ? <p className="muted-copy">Loading log entries...</p> : null}

          {!isEntriesLoading ? (
            entries.length > 0 ? (
              <div
                ref={logPanelRef}
                className="run-log-terminal"
                role="log"
                aria-live="polite"
                aria-label={`Log entries for ${selectedTaskId}`}
              >
                {entries.map((entry) => (
                  <div key={`${entry.taskId}-${entry.sequence}`} className="run-log-line">
                    <span className="run-log-line__meta">
                      {formatTimestamp(entry.timestamp)} {entry.level} {entry.logger}
                    </span>
                    <code>{entry.message}</code>
                  </div>
                ))}
              </div>
            ) : (
              <div
                ref={logPanelRef}
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
