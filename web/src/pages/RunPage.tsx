import { useEffect, useMemo, useState } from 'react';
import { Link, useParams } from 'react-router-dom';

import {
  fetchRunSnapshot,
  subscribeToRunEvents,
  type RunEvent,
  type RunActiveTarget,
  type RunMetrics,
  type RunPhase,
  type RunSnapshot,
  type RunWorkerCard,
} from '../lib/api';
import { LogViewer } from '../components/LogViewer';

type ConnectionState = 'idle' | 'connecting' | 'live' | 'unavailable' | 'error';

type ActionEntry = {
  id: string;
  title: string;
  detail: string;
};

type ParallelSnapshotData = {
  currentBatch: number;
  parallelStats: Record<string, unknown>;
  activeTargets: RunActiveTarget[];
  workerCards: RunWorkerCard[];
  batchResults: Array<Array<Record<string, unknown>>>;
};

function formatPercent(value: number | null | undefined): string {
  if (typeof value !== 'number' || Number.isNaN(value)) {
    return 'N/A';
  }

  return `${(value * 100).toFixed(1)}%`;
}

function formatMetricValue(value: number | null | undefined): string {
  if (typeof value !== 'number' || Number.isNaN(value)) {
    return 'N/A';
  }

  return value.toLocaleString();
}

function formatTarget(target: Record<string, unknown> | null | undefined): string {
  if (!target) {
    return 'No active target';
  }

  const className = String(target.class_name ?? target.className ?? 'UnknownClass');
  const methodName = String(target.method_name ?? target.methodName ?? 'unknownMethod');
  return `${className}.${methodName}`;
}

function formatTargetId(target: RunActiveTarget): string {
  const targetId = target.targetId ?? target.target_id;
  if (typeof targetId === 'string' && targetId.length > 0) {
    return targetId;
  }

  return formatTarget(target);
}

function formatKeyLabel(key: string): string {
  return key
    .replace(/([A-Z])/g, ' $1')
    .replace(/_/g, ' ')
    .replace(/^./, (character) => character.toUpperCase());
}

function formatValue(value: unknown): string {
  if (typeof value === 'number') {
    return Number.isInteger(value) ? value.toString() : value.toFixed(3);
  }
  if (typeof value === 'boolean') {
    return value ? 'Yes' : 'No';
  }
  if (value === null || value === undefined) {
    return 'N/A';
  }
  if (typeof value === 'object') {
    return JSON.stringify(value);
  }
  return String(value);
}

function formatDuration(seconds: number | null | undefined): string {
  if (typeof seconds !== 'number' || Number.isNaN(seconds)) {
    return 'N/A';
  }

  return `${seconds.toFixed(1)}s`;
}

function getParallelSnapshot(snapshot: RunSnapshot): ParallelSnapshotData {
  return {
    currentBatch: snapshot.parallel?.currentBatch ?? snapshot.currentBatch ?? 0,
    parallelStats: snapshot.parallel?.parallelStats ?? snapshot.parallelStats ?? {},
    activeTargets: snapshot.parallel?.activeTargets ?? snapshot.activeTargets ?? [],
    workerCards: snapshot.parallel?.workerCards ?? snapshot.workerCards ?? [],
    batchResults: snapshot.parallel?.batchResults ?? snapshot.batchResults ?? [],
  };
}

function withParallelSnapshot(
  snapshot: RunSnapshot,
  parallel: Partial<ParallelSnapshotData>,
): RunSnapshot {
  const current = getParallelSnapshot(snapshot);
  const nextParallel: ParallelSnapshotData = {
    currentBatch: parallel.currentBatch ?? current.currentBatch,
    parallelStats: parallel.parallelStats ?? current.parallelStats,
    activeTargets: parallel.activeTargets ?? current.activeTargets,
    workerCards: parallel.workerCards ?? current.workerCards,
    batchResults: parallel.batchResults ?? current.batchResults,
  };

  return {
    ...snapshot,
    parallel: nextParallel,
    currentBatch: nextParallel.currentBatch,
    parallelStats: nextParallel.parallelStats,
    activeTargets: nextParallel.activeTargets,
    workerCards: nextParallel.workerCards,
    batchResults: nextParallel.batchResults,
  };
}

function buildActionEntry(event: RunEvent): ActionEntry {
  const eventName = event.type.replace('run.', '');

  if (event.type === 'run.snapshot') {
    return {
      id: `event-${event.sequence ?? 'snapshot'}`,
      title: 'Snapshot synced',
      detail: `Rebuilt from ${event.snapshot?.phase.label ?? event.status ?? 'current'} state.`,
    };
  }

  if (event.type === 'run.phase') {
    return {
      id: `event-${event.sequence ?? 'phase'}`,
      title: 'Phase updated',
      detail: event.phase?.label ?? event.phase?.key ?? 'Run phase changed.',
    };
  }

  if (event.type === 'run.failed') {
    return {
      id: `event-${event.sequence ?? 'failed'}`,
      title: 'Run failed',
      detail: event.error ?? 'The run reported a failure.',
    };
  }

  return {
    id: `event-${event.sequence ?? eventName}`,
    title: formatKeyLabel(eventName),
    detail:
      event.decisionReasoning ??
      (event.currentTarget ? `Target ${formatTarget(event.currentTarget)}.` : 'Live event received.'),
  };
}

function mergePhase(current: RunPhase, update?: Partial<RunPhase>): RunPhase {
  return update ? { ...current, ...update } : current;
}

function mergeMetrics(current: RunMetrics, update?: Partial<RunMetrics>): RunMetrics {
  return update ? { ...current, ...update } : current;
}

function applyRunEvent(snapshot: RunSnapshot, event: RunEvent): RunSnapshot {
  if (event.snapshot) {
    return event.snapshot;
  }

  const nextSnapshot: RunSnapshot = {
    ...snapshot,
    status: event.status ?? snapshot.status,
    mode: event.mode ?? snapshot.mode,
    iteration: event.iteration ?? snapshot.iteration,
    llmCalls: event.llmCalls ?? snapshot.llmCalls,
    budget: event.budget ?? snapshot.budget,
    decisionReasoning:
      event.decisionReasoning !== undefined ? event.decisionReasoning : snapshot.decisionReasoning,
    currentTarget: event.currentTarget !== undefined ? event.currentTarget : snapshot.currentTarget,
    previousTarget:
      event.previousTarget !== undefined ? event.previousTarget : snapshot.previousTarget,
    recentImprovements: event.recentImprovements ?? snapshot.recentImprovements,
    improvementSummary: event.improvementSummary ?? snapshot.improvementSummary,
    phase: mergePhase(snapshot.phase, event.phase),
    metrics: mergeMetrics(snapshot.metrics, event.metrics),
  };

  const hasParallelUpdate =
    event.currentBatch !== undefined ||
    event.parallelStats !== undefined ||
    event.activeTargets !== undefined ||
    event.workerCards !== undefined ||
    event.batchResults !== undefined;

  const snapshotWithParallel = hasParallelUpdate
    ? withParallelSnapshot(nextSnapshot, {
        currentBatch: event.currentBatch,
        parallelStats: event.parallelStats,
        activeTargets: event.activeTargets,
        workerCards: event.workerCards,
        batchResults: event.batchResults,
      })
    : nextSnapshot;

  if (event.type === 'run.completed') {
    snapshotWithParallel.status = 'completed';
    snapshotWithParallel.phase = mergePhase(snapshotWithParallel.phase, {
      key: 'completed',
      label: 'Completed',
    });
  }

  if (event.type === 'run.failed') {
    snapshotWithParallel.status = 'failed';
    snapshotWithParallel.phase = mergePhase(snapshotWithParallel.phase, {
      key: 'failed',
      label: 'Failed',
    });
  }

  if (event.type === 'run.started' && snapshotWithParallel.phase.key === 'queued') {
    snapshotWithParallel.phase = mergePhase(snapshotWithParallel.phase, {
      key: 'running',
      label: 'Running',
    });
    snapshotWithParallel.status = 'running';
  }

  return snapshotWithParallel;
}

function buildImprovementSummary(snapshot: RunSnapshot): Array<{ label: string; value: string }> {
  const latest = snapshot.improvementSummary.latest;
  const summary: Array<{ label: string; value: string }> = [
    {
      label: 'Recorded improvements',
      value: formatValue(snapshot.improvementSummary.count),
    },
  ];

  if (latest && typeof latest === 'object') {
    Object.entries(latest).forEach(([key, value]) => {
      summary.push({ label: formatKeyLabel(key), value: formatValue(value) });
    });
  }

  return summary;
}

function buildParallelStatsSummary(
  parallelStats: Record<string, unknown>,
): Array<{ label: string; value: string }> {
  return Object.entries(parallelStats).map(([key, value]) => ({
    label: formatKeyLabel(key),
    value: formatValue(value),
  }));
}

function StandardRunView(props: {
  runId: string;
  snapshot: RunSnapshot;
  connectionState: ConnectionState;
  actionHistory: ActionEntry[];
  improvementSummary: Array<{ label: string; value: string }>;
}) {
  const { runId, snapshot, connectionState, actionHistory, improvementSummary } = props;

  return (
    <>
      <div className="run-page__hero">
        <div>
          <p className="eyebrow">Run</p>
          <h2>Run Status</h2>
          <p className="run-page__lead">
            Standard-mode snapshot for <code>{snapshot.runId}</code>, rebuilt from the latest
            backend state before live updates resume.
          </p>
        </div>

        <div className="run-status-badges">
          <span className="run-badge">Status: {snapshot.status}</span>
          <span className="run-badge">Phase: {snapshot.phase.label}</span>
          <span className="run-badge">Live: {connectionState}</span>
        </div>
      </div>

      <div className="run-layout">
        <div className="run-layout__main">
          <section className="run-card" aria-labelledby="run-decision-panel">
            <p className="eyebrow">Decision</p>
            <h3 id="run-decision-panel">Decision Panel</h3>
            <dl className="detail-grid">
              <div>
                <dt>Current target</dt>
                <dd>{formatTarget(snapshot.currentTarget)}</dd>
              </div>
              <div>
                <dt>Previous target</dt>
                <dd>{formatTarget(snapshot.previousTarget)}</dd>
              </div>
              <div>
                <dt>Phase</dt>
                <dd>{snapshot.phase.label}</dd>
              </div>
              <div>
                <dt>LLM calls</dt>
                <dd>
                  {snapshot.llmCalls} / {snapshot.budget}
                </dd>
              </div>
            </dl>
            <div className="decision-reasoning">
              <strong>Decision reasoning</strong>
              <p>{snapshot.decisionReasoning ?? 'No reasoning has been published yet.'}</p>
            </div>
          </section>

          <section className="run-card" aria-labelledby="run-metrics-panel">
            <p className="eyebrow">Metrics</p>
            <h3 id="run-metrics-panel">Core Metrics</h3>
            <div className="metric-grid">
              <article>
                <span>Mutation score</span>
                <strong>{formatPercent(snapshot.metrics.mutationScore)}</strong>
              </article>
              <article>
                <span>Line coverage</span>
                <strong>{formatPercent(snapshot.metrics.lineCoverage)}</strong>
              </article>
              <article>
                <span>Branch coverage</span>
                <strong>{formatPercent(snapshot.metrics.branchCoverage)}</strong>
              </article>
              <article>
                <span>Total tests</span>
                <strong>{formatMetricValue(snapshot.metrics.totalTests)}</strong>
              </article>
              <article>
                <span>Killed mutants</span>
                <strong>{formatMetricValue(snapshot.metrics.killedMutants)}</strong>
              </article>
              <article>
                <span>Survived mutants</span>
                <strong>{formatMetricValue(snapshot.metrics.survivedMutants)}</strong>
              </article>
            </div>
          </section>

          <section className="run-card" aria-labelledby="run-actions-panel">
            <p className="eyebrow">History</p>
            <h3 id="run-actions-panel">Action History Summary</h3>
            {actionHistory.length > 0 ? (
              <ol className="summary-list">
                {actionHistory.map((entry) => (
                  <li key={entry.id}>
                    <strong>{entry.title}</strong>
                    <span>{entry.detail}</span>
                  </li>
                ))}
              </ol>
            ) : (
              <p className="muted-copy">Waiting for live run events.</p>
            )}
          </section>
        </div>

        <aside className="run-layout__sidebar">
          <section className="run-card" aria-labelledby="run-improvements-panel">
            <p className="eyebrow">Improvements</p>
            <h3 id="run-improvements-panel">Recent Improvements</h3>
            <ul className="summary-list summary-list--compact">
              {improvementSummary.map((entry) => (
                <li key={entry.label}>
                  <strong>{entry.label}</strong>
                  <span>{entry.value}</span>
                </li>
              ))}
            </ul>
          </section>

          <section className="run-card" aria-labelledby="run-state-panel">
            <p className="eyebrow">State</p>
            <h3 id="run-state-panel">Run State</h3>
            <ul className="summary-list summary-list--compact">
              <li>
                <strong>Status</strong>
                <span>{snapshot.status}</span>
              </li>
              <li>
                <strong>Phase</strong>
                <span>{snapshot.phase.label}</span>
              </li>
              <li>
                <strong>Iteration</strong>
                <span>{snapshot.iteration}</span>
              </li>
              <li>
                <strong>Current method coverage</strong>
                <span>{formatPercent(snapshot.metrics.currentMethodCoverage)}</span>
              </li>
            </ul>
          </section>

          <Link to={`/runs/${runId}/results`}>Go to results skeleton</Link>
        </aside>
      </div>
    </>
  );
}

function ParallelRunView(props: {
  runId: string;
  snapshot: RunSnapshot;
  connectionState: ConnectionState;
}) {
  const { runId, snapshot, connectionState } = props;
  const parallel = getParallelSnapshot(snapshot);
  const parallelStatsSummary = buildParallelStatsSummary(parallel.parallelStats);

  return (
    <>
      <div className="run-page__hero">
        <div>
          <p className="eyebrow">Run</p>
          <h2>Parallel Run Status</h2>
          <p className="run-page__lead">
            Parallel-mode snapshot for <code>{snapshot.runId}</code>, restored from the latest
            batch-aware backend state before live updates resume.
          </p>
        </div>

        <div className="run-status-badges">
          <span className="run-badge">Status: {snapshot.status}</span>
          <span className="run-badge">Phase: {snapshot.phase.label}</span>
          <span className="run-badge">Current batch: {parallel.currentBatch}</span>
          <span className="run-badge">Live: {connectionState}</span>
        </div>
      </div>

      <div className="run-layout">
        <div className="run-layout__main">
          <section className="run-card" aria-labelledby="run-parallel-overview-panel">
            <p className="eyebrow">Parallel</p>
            <h3 id="run-parallel-overview-panel">Current Batch</h3>
            <dl className="detail-grid">
              <div>
                <dt>Current batch</dt>
                <dd>{parallel.currentBatch}</dd>
              </div>
              <div>
                <dt>Active targets</dt>
                <dd>{parallel.activeTargets.length}</dd>
              </div>
              <div>
                <dt>Latest worker cards</dt>
                <dd>{parallel.workerCards.length}</dd>
              </div>
              <div>
                <dt>Completed batch groups</dt>
                <dd>{parallel.batchResults.length}</dd>
              </div>
            </dl>
            <div className="decision-reasoning">
              <strong>Decision reasoning</strong>
              <p>{snapshot.decisionReasoning ?? 'No reasoning has been published yet.'}</p>
            </div>
          </section>

          <section className="run-card" aria-labelledby="run-worker-panel">
            <p className="eyebrow">Workers</p>
            <h3 id="run-worker-panel">Worker Cards</h3>
            {parallel.workerCards.length > 0 ? (
              <div className="worker-card-grid">
                {parallel.workerCards.map((worker) => (
                  <article key={worker.targetId} className="worker-card">
                    <div className="worker-card__header">
                      <strong>{worker.targetId}</strong>
                      <span className={worker.success ? 'worker-pill worker-pill--success' : 'worker-pill worker-pill--error'}>
                        {worker.success ? 'Completed' : 'Failed'}
                      </span>
                    </div>
                    <dl className="worker-card__details">
                      <div>
                        <dt>Tests</dt>
                        <dd>{worker.testsGenerated}</dd>
                      </div>
                      <div>
                        <dt>Mutants</dt>
                        <dd>{worker.mutantsGenerated}</dd>
                      </div>
                      <div>
                        <dt>Killed</dt>
                        <dd>{worker.mutantsKilled}</dd>
                      </div>
                      <div>
                        <dt>Local score</dt>
                        <dd>{formatPercent(worker.localMutationScore)}</dd>
                      </div>
                      <div>
                        <dt>Runtime</dt>
                        <dd>{formatDuration(worker.processingTime)}</dd>
                      </div>
                    </dl>
                    {worker.error ? <p className="worker-card__error">{worker.error}</p> : null}
                  </article>
                ))}
              </div>
            ) : (
              <p className="muted-copy">Waiting for the first worker batch to finish.</p>
            )}
          </section>

          <LogViewer runId={runId} />
        </div>

        <aside className="run-layout__sidebar">
          <section className="run-card" aria-labelledby="run-parallel-stats-panel">
            <p className="eyebrow">Stats</p>
            <h3 id="run-parallel-stats-panel">Parallel Stats</h3>
            <ul className="summary-list summary-list--compact">
              {parallelStatsSummary.map((entry) => (
                <li key={entry.label}>
                  <strong>{entry.label}</strong>
                  <span>{entry.value}</span>
                </li>
              ))}
            </ul>
          </section>

          <section className="run-card" aria-labelledby="run-active-targets-panel">
            <p className="eyebrow">Targets</p>
            <h3 id="run-active-targets-panel">Active Targets</h3>
            {parallel.activeTargets.length > 0 ? (
              <ul className="summary-list summary-list--compact">
                {parallel.activeTargets.map((target) => (
                  <li key={formatTargetId(target)}>
                    <strong>{formatTargetId(target)}</strong>
                    <span>
                      Coverage {formatPercent(Number(target.method_coverage ?? target.methodCoverage ?? null))}
                    </span>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="muted-copy">No targets are currently assigned to workers.</p>
            )}
          </section>

          <section className="run-card" aria-labelledby="run-state-panel">
            <p className="eyebrow">State</p>
            <h3 id="run-state-panel">Run State</h3>
            <ul className="summary-list summary-list--compact">
              <li>
                <strong>Status</strong>
                <span>{snapshot.status}</span>
              </li>
              <li>
                <strong>Phase</strong>
                <span>{snapshot.phase.label}</span>
              </li>
              <li>
                <strong>Iteration</strong>
                <span>{snapshot.iteration}</span>
              </li>
              <li>
                <strong>Global mutation score</strong>
                <span>{formatPercent(snapshot.metrics.globalMutationScore)}</span>
              </li>
            </ul>
          </section>

          <Link to={`/runs/${runId}/results`}>Go to results skeleton</Link>
        </aside>
      </div>
    </>
  );
}

export function RunPage() {
  const { runId = '' } = useParams();
  const [snapshot, setSnapshot] = useState<RunSnapshot | null>(null);
  const [pageError, setPageError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [connectionState, setConnectionState] = useState<ConnectionState>('idle');
  const [actionHistory, setActionHistory] = useState<ActionEntry[]>([]);

  useEffect(() => {
    let active = true;
    let teardown: (() => void) | null = null;

    async function loadRun() {
      setIsLoading(true);
      setPageError(null);
      setActionHistory([]);

      try {
        const initialSnapshot = await fetchRunSnapshot(runId);
        if (!active) {
          return;
        }

        setSnapshot(initialSnapshot);
        setIsLoading(false);

        if (typeof EventSource === 'undefined') {
          setConnectionState('unavailable');
          return;
        }

        setConnectionState('connecting');

        try {
          teardown = subscribeToRunEvents(runId, {
            onEvent: (event) => {
              if (!active) {
                return;
              }

              setSnapshot((current) => (current ? applyRunEvent(current, event) : current));
              setActionHistory((current) => [buildActionEntry(event), ...current].slice(0, 6));
              setConnectionState('live');
            },
            onError: () => {
              if (active) {
                setConnectionState('error');
              }
            },
          });
        } catch {
          setConnectionState('unavailable');
        }
      } catch (error) {
        if (!active) {
          return;
        }

        setPageError(error instanceof Error ? error.message : 'Unable to load run snapshot.');
        setIsLoading(false);
        setConnectionState('error');
      }
    }

    void loadRun();

    return () => {
      active = false;
      teardown?.();
    };
  }, [runId]);

  const improvementSummary = useMemo(
    () => (snapshot ? buildImprovementSummary(snapshot) : []),
    [snapshot],
  );

  if (isLoading) {
    return (
      <section className="panel run-page">
        <p className="eyebrow">Run</p>
        <h2>Run Status</h2>
        <p>Loading snapshot for <code>{runId}</code>...</p>
      </section>
    );
  }

  if (pageError || snapshot === null) {
    return (
      <section className="panel run-page">
        <p className="eyebrow">Run</p>
        <h2>Run Status</h2>
        <p role="alert">{pageError ?? 'Run snapshot is unavailable.'}</p>
      </section>
    );
  }

  return (
    <section className="panel run-page">
      {snapshot.mode === 'parallel' ? (
        <ParallelRunView runId={runId} snapshot={snapshot} connectionState={connectionState} />
      ) : (
        <StandardRunView
          runId={runId}
          snapshot={snapshot}
          connectionState={connectionState}
          actionHistory={actionHistory}
          improvementSummary={improvementSummary}
        />
      )}
    </section>
  );
}
