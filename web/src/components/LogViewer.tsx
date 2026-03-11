import { useEffect, useMemo, useState } from 'react';

import {
  fetchRunLogs,
  fetchRunLogsForTask,
  type RunLogEntry,
  type RunLogStreamsSummary,
} from '../lib/api';

type LogViewerProps = {
  runId: string;
};

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

export function LogViewer({ runId }: LogViewerProps) {
  const [streams, setStreams] = useState<RunLogStreamsSummary | null>(null);
  const [selectedTaskId, setSelectedTaskId] = useState('main');
  const [entries, setEntries] = useState<RunLogEntry[]>([]);
  const [isSummaryLoading, setIsSummaryLoading] = useState(true);
  const [isEntriesLoading, setIsEntriesLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;

    async function loadSummary() {
      setIsSummaryLoading(true);
      setError(null);

      try {
        const response = await fetchRunLogs(runId);
        if (!active) {
          return;
        }

        const nextStreams = response.streams;
        const nextTaskIds = nextStreams.taskIds.length > 0 ? nextStreams.taskIds : ['main'];

        setStreams(nextStreams);
        setSelectedTaskId((current) => (nextTaskIds.includes(current) ? current : nextTaskIds[0]));
      } catch (loadError) {
        if (!active) {
          return;
        }

        setError(loadError instanceof Error ? loadError.message : 'Unable to load run logs.');
      } finally {
        if (active) {
          setIsSummaryLoading(false);
        }
      }
    }

    void loadSummary();

    return () => {
      active = false;
    };
  }, [runId]);

  useEffect(() => {
    let active = true;

    async function loadEntries() {
      if (isSummaryLoading || error) {
        return;
      }

      setIsEntriesLoading(true);

      try {
        const response = await fetchRunLogsForTask(runId, selectedTaskId);
        if (!active) {
          return;
        }

        setEntries(response.entries);
      } catch (loadError) {
        if (!active) {
          return;
        }

        setError(loadError instanceof Error ? loadError.message : 'Unable to load selected log stream.');
        setEntries([]);
      } finally {
        if (active) {
          setIsEntriesLoading(false);
        }
      }
    }

    void loadEntries();

    return () => {
      active = false;
    };
  }, [error, isSummaryLoading, runId, selectedTaskId]);

  const taskIds = useMemo(() => {
    if (streams?.taskIds.length) {
      return streams.taskIds;
    }

    return ['main'];
  }, [streams]);

  const hasWorkerTabs = taskIds.some((taskId) => taskId !== 'main');
  const entryCount = streams?.counts[selectedTaskId] ?? entries.length;
  const maxEntriesPerStream = streams?.maxEntriesPerStream ?? 0;

  return (
    <section className="run-card" aria-labelledby="run-logs-panel">
      <div className="run-card__header">
        <div>
          <p className="eyebrow">Logs</p>
          <h3 id="run-logs-panel">Log Viewer</h3>
        </div>
        <div className="run-log-viewer__meta">
          <span className="run-badge">Stream: {formatTaskLabel(selectedTaskId)}</span>
          {maxEntriesPerStream > 0 ? (
            <span className="run-badge">Buffered: {entryCount} / {maxEntriesPerStream}</span>
          ) : null}
        </div>
      </div>

      {isSummaryLoading ? <p className="muted-copy">Loading log streams...</p> : null}
      {!isSummaryLoading && error ? <p role="alert">{error}</p> : null}

      {!isSummaryLoading && !error ? (
        <>
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
            <p className="muted-copy">Only the main log stream is available for this run.</p>
          )}

          {isEntriesLoading ? <p className="muted-copy">Loading log entries...</p> : null}

          {!isEntriesLoading ? (
            entries.length > 0 ? (
              <ul className="run-log-list" aria-label={`Log entries for ${selectedTaskId}`}>
                {entries.map((entry) => (
                  <li key={`${entry.taskId}-${entry.sequence}`} className="run-log-entry">
                    <div className="run-log-entry__meta">
                      <span>{formatTimestamp(entry.timestamp)}</span>
                      <span>{entry.level}</span>
                      <span>{entry.logger}</span>
                    </div>
                    <code>{entry.message}</code>
                  </li>
                ))}
              </ul>
            ) : (
              <div className="run-log-empty" role="status">
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
