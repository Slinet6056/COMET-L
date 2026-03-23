import { useEffect, useMemo, useRef, useState } from 'react';
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

type ConnectionState = 'idle' | 'connecting' | 'live' | 'ended' | 'unavailable' | 'error';
const SNAPSHOT_POLL_MS = 1500;

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

type WorkerOutputRow = {
  key: string;
  targetId: string;
  className: string;
  methodName: string;
  success: boolean;
  error: string | null;
  testsGenerated: number;
  mutantsGenerated: number;
  mutantsKilled: number;
  localMutationScore: number | null;
  methodCoverage: number | null;
};

const STATUS_LABELS: Record<string, string> = {
  created: '已创建',
  queued: '排队中',
  preprocessing: '预处理中',
  running: '运行中',
  completed: '已完成',
  failed: '失败',
};

const CONNECTION_LABELS: Record<ConnectionState, string> = {
  idle: '空闲',
  connecting: '连接中',
  live: '实时同步中',
  ended: '已结束',
  unavailable: '不可用',
  error: '异常',
};

const WORKER_OUTPUT_PAGE_SIZE = 15;

function isTerminalRunStatus(status: string | null | undefined): boolean {
  return status === 'completed' || status === 'failed';
}

const KEY_LABELS: Record<string, string> = {
  mutation_score_delta: '变异分数提升',
  coverage_delta: '覆盖率提升',
  total_batches: '总批次数',
  total_workers_spawned: '累计工作线程数',
  total_targets_processed: '已处理目标数',
  failed_targets_in_parallel: '并行失败目标数',
  snapshot: '快照同步',
  phase: '阶段更新',
  started: '运行开始',
  completed: '运行完成',
  failed: '运行失败',
};

function formatPercent(value: number | null | undefined): string {
  if (typeof value !== 'number' || Number.isNaN(value)) {
    return '暂无';
  }

  return `${(value * 100).toFixed(1)}%`;
}

function formatMetricValue(value: number | null | undefined): string {
  if (typeof value !== 'number' || Number.isNaN(value)) {
    return '暂无';
  }

  return value.toLocaleString();
}

function formatTarget(target: Record<string, unknown> | null | undefined): string {
  if (!target) {
    return '当前没有活动目标';
  }

  const targetId = target.target_id ?? target.targetId;
  if (typeof targetId === 'string' && targetId.length > 0) {
    return targetId;
  }

  const className = String(target.class_name ?? target.className ?? 'UnknownClass');
  const methodName = String(target.method_name ?? target.methodName ?? 'unknownMethod');
  const methodSignature = target.method_signature ?? target.methodSignature;
  if (typeof methodSignature === 'string' && methodSignature.length > 0) {
    return `${className}.${methodName} [${methodSignature}]`;
  }

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
  if (key in KEY_LABELS) {
    return KEY_LABELS[key];
  }

  return key
    .replace(/([A-Z])/g, ' $1')
    .replace(/_/g, ' ')
    .replace(/^./, (character) => character.toUpperCase());
}

function translateStatus(value: string | null | undefined): string {
  if (!value) {
    return '未知';
  }

  return STATUS_LABELS[value] ?? value;
}

function translatePhaseLabel(phase: Pick<RunPhase, 'key' | 'label'> | null | undefined): string {
  if (!phase) {
    return '未知';
  }

  return STATUS_LABELS[phase.key] ?? STATUS_LABELS[phase.label.toLowerCase()] ?? phase.label;
}

function formatValue(value: unknown): string {
  if (typeof value === 'number') {
    return Number.isInteger(value) ? value.toString() : value.toFixed(3);
  }
  if (typeof value === 'boolean') {
    return value ? '是' : '否';
  }
  if (value === null || value === undefined) {
    return '暂无';
  }
  if (typeof value === 'object') {
    return JSON.stringify(value);
  }
  return String(value);
}

function formatWorkerStatusLabel(success: boolean): string {
  return success ? '已完成' : '失败';
}

function toStringValue(value: unknown): string | null {
  return typeof value === 'string' && value.length > 0 ? value : null;
}

function toBooleanValue(value: unknown): boolean {
  return value === true;
}

function toNumericValue(value: unknown): number | null {
  if (typeof value === 'number' && !Number.isNaN(value)) {
    return value;
  }

  if (typeof value === 'string' && value.trim().length > 0) {
    const parsed = Number(value);
    return Number.isNaN(parsed) ? null : parsed;
  }

  return null;
}

function getTargetParts(targetId: string | null): { className: string; methodName: string } {
  if (!targetId) {
    return { className: 'UnknownClass', methodName: 'unknownMethod' };
  }

  const [memberPath] = targetId.split('#');
  const separatorIndex = memberPath.lastIndexOf('.');

  if (separatorIndex === -1) {
    return { className: memberPath, methodName: 'unknownMethod' };
  }

  return {
    className: memberPath.slice(0, separatorIndex),
    methodName: memberPath.slice(separatorIndex + 1),
  };
}

function toCoverageValue(value: unknown): number | null {
  if (typeof value === 'number' && !Number.isNaN(value)) {
    return value;
  }

  if (typeof value === 'string' && value.trim().length > 0) {
    const parsed = Number(value);
    return Number.isNaN(parsed) ? null : parsed;
  }

  return null;
}

function buildWorkerOutputRows(parallel: ParallelSnapshotData): WorkerOutputRow[] {
  return parallel.batchResults.flatMap((batch, batchIndex) =>
    batch.map((result, resultIndex) => {
      const targetId = toStringValue(result.targetId ?? result.target_id) ?? '';
      const targetParts = getTargetParts(targetId || null);

      return {
        key: targetId || `batch-${batchIndex}-row-${resultIndex}`,
        targetId,
        className: toStringValue(result.className ?? result.class_name) ?? targetParts.className,
        methodName:
          toStringValue(result.methodName ?? result.method_name) ?? targetParts.methodName,
        success: toBooleanValue(result.success),
        error: toStringValue(result.error),
        testsGenerated: toNumericValue(result.testsGenerated ?? result.tests_generated) ?? 0,
        mutantsGenerated: toNumericValue(result.mutantsGenerated ?? result.mutants_generated) ?? 0,
        mutantsKilled: toNumericValue(result.mutantsKilled ?? result.mutants_killed) ?? 0,
        localMutationScore: toNumericValue(
          result.localMutationScore ?? result.local_mutation_score,
        ),
        methodCoverage: toCoverageValue(result.methodCoverage ?? result.method_coverage),
      };
    }),
  );
}

function buildWorkerCoverageLookup(parallel: ParallelSnapshotData): Map<string, number> {
  const coverageByTarget = new Map<string, number>();

  parallel.workerCards.forEach((worker) => {
    const coverage = toCoverageValue(worker.methodCoverage);

    if (coverage !== null) {
      coverageByTarget.set(worker.targetId, coverage);
    }
  });

  parallel.batchResults.forEach((batch) => {
    batch.forEach((result) => {
      const targetId = typeof result.targetId === 'string' ? result.targetId : null;
      const coverage = toCoverageValue(result.method_coverage ?? result.methodCoverage);

      if (targetId && coverage !== null) {
        coverageByTarget.set(targetId, coverage);
      }
    });
  });

  parallel.activeTargets.forEach((target) => {
    const coverage = toCoverageValue(target.method_coverage ?? target.methodCoverage);

    if (coverage !== null) {
      coverageByTarget.set(formatTargetId(target), coverage);
    }
  });

  return coverageByTarget;
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
      title: '快照已同步',
      detail: `已根据 ${translatePhaseLabel(event.snapshot?.phase ?? null) || translateStatus(event.status) || '当前'} 状态重建视图。`,
    };
  }

  if (event.type === 'run.phase') {
    return {
      id: `event-${event.sequence ?? 'phase'}`,
      title: '阶段已更新',
      detail: event.phase ? translatePhaseLabel(event.phase as RunPhase) : '运行阶段已变化。',
    };
  }

  if (event.type === 'run.failed') {
    return {
      id: `event-${event.sequence ?? 'failed'}`,
      title: '运行失败',
      detail: event.error ?? '本次运行报告了失败。',
    };
  }

  return {
    id: `event-${event.sequence ?? eventName}`,
    title: formatKeyLabel(eventName),
    detail:
      event.decisionReasoning ??
      (event.currentTarget ? `目标 ${formatTarget(event.currentTarget)}。` : '已收到实时事件。'),
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
      label: '已完成',
    });
  }

  if (event.type === 'run.failed') {
    snapshotWithParallel.status = 'failed';
    snapshotWithParallel.phase = mergePhase(snapshotWithParallel.phase, {
      key: 'failed',
      label: '失败',
    });
  }

  if (event.type === 'run.started' && snapshotWithParallel.phase.key === 'queued') {
    snapshotWithParallel.phase = mergePhase(snapshotWithParallel.phase, {
      key: 'running',
      label: '运行中',
    });
    snapshotWithParallel.status = 'running';
  }

  return snapshotWithParallel;
}

function buildImprovementSummary(snapshot: RunSnapshot): Array<{ label: string; value: string }> {
  const latest = snapshot.improvementSummary.latest;
  const summary: Array<{ label: string; value: string }> = [
    {
      label: '记录的改进',
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

function isMutationDisabled(mutationEnabled: boolean | null | undefined): boolean {
  return mutationEnabled === false;
}

function getMutationStatusText(mutationEnabled: boolean | null | undefined): string {
  return isMutationDisabled(mutationEnabled) ? '未启用（测试生成消融模式）' : '已启用';
}

function HistoricalLogNotice(props: { runId: string }) {
  const { runId } = props;

  return (
    <section className="run-card" aria-labelledby="run-history-log-panel">
      <p className="eyebrow">日志</p>
      <h3 id="run-history-log-panel">历史日志</h3>
      <p className="muted-copy">
        这次运行是从已落盘记录中恢复出来的，实时日志缓冲区不会再重建。你仍然可以直接下载本次运行的
        <code> run.log </code>
        查看完整日志。
      </p>
      <Link to={`/api/runs/${runId}/artifacts/run-log`} className="artifact-link">
        下载 run.log
      </Link>
    </section>
  );
}

function StandardRunView(props: {
  runId: string;
  snapshot: RunSnapshot;
  connectionState: ConnectionState;
  actionHistory: ActionEntry[];
  improvementSummary: Array<{ label: string; value: string }>;
}) {
  const { runId, snapshot, connectionState, actionHistory, improvementSummary } = props;
  const mutationDisabled = isMutationDisabled(snapshot.mutationEnabled);

  return (
    <>
      <div className="run-page__hero">
        <div>
          <p className="eyebrow">运行</p>
          <h2>运行状态</h2>
          <p className="run-page__lead">
            <code>{snapshot.runId}</code> 的标准模式快照，页面会先根据最新后端状态重建，
            {snapshot.isHistorical
              ? '当前展示的是历史快照，不会再恢复实时更新。'
              : '然后再恢复实时更新。'}
          </p>
        </div>

        <div className="run-status-badges">
          <span className="run-badge">状态：{translateStatus(snapshot.status)}</span>
          <span className="run-badge">阶段：{translatePhaseLabel(snapshot.phase)}</span>
          <span className="run-badge">实时连接：{CONNECTION_LABELS[connectionState]}</span>
        </div>
      </div>

      <section className="run-card" aria-labelledby="run-metrics-panel">
        <p className="eyebrow">指标</p>
        <h3 id="run-metrics-panel">核心指标</h3>
        <div className="metric-grid metric-grid--hero">
          <article>
            <span>变异分数</span>
            <strong>
              {mutationDisabled
                ? '未启用（测试生成消融模式）'
                : formatPercent(snapshot.metrics.mutationScore)}
            </strong>
          </article>
          <article>
            <span>行覆盖率</span>
            <strong>{formatPercent(snapshot.metrics.lineCoverage)}</strong>
          </article>
          <article>
            <span>分支覆盖率</span>
            <strong>{formatPercent(snapshot.metrics.branchCoverage)}</strong>
          </article>
        </div>
        <dl className="detail-grid detail-grid--compact">
          <div>
            <dt>变异分析</dt>
            <dd>{getMutationStatusText(snapshot.mutationEnabled)}</dd>
          </div>
          <div>
            <dt>当前目标</dt>
            <dd>{formatTarget(snapshot.currentTarget)}</dd>
          </div>
          <div>
            <dt>上一个目标</dt>
            <dd>{formatTarget(snapshot.previousTarget)}</dd>
          </div>
          <div>
            <dt>测试总数</dt>
            <dd>{formatMetricValue(snapshot.metrics.totalTests)}</dd>
          </div>
          <div>
            <dt>当前方法覆盖率</dt>
            <dd>{formatPercent(snapshot.metrics.currentMethodCoverage)}</dd>
          </div>
          {mutationDisabled ? null : (
            <>
              <div>
                <dt>已杀死变异体</dt>
                <dd>{formatMetricValue(snapshot.metrics.killedMutants)}</dd>
              </div>
              <div>
                <dt>存活变异体</dt>
                <dd>{formatMetricValue(snapshot.metrics.survivedMutants)}</dd>
              </div>
            </>
          )}
          <div>
            <dt>LLM 调用次数</dt>
            <dd>
              {snapshot.llmCalls} / {snapshot.budget}
            </dd>
          </div>
          <div>
            <dt>迭代次数</dt>
            <dd>{snapshot.iteration}</dd>
          </div>
        </dl>
      </section>

      <section className="run-card" aria-labelledby="run-decision-panel">
        <p className="eyebrow">决策</p>
        <h3 id="run-decision-panel">决策面板</h3>
        <div className="decision-reasoning">
          <strong>决策说明</strong>
          <p>{snapshot.decisionReasoning ?? '暂未发布决策说明。'}</p>
        </div>
      </section>

      <section className="run-card" aria-labelledby="run-improvements-panel">
        <p className="eyebrow">改进</p>
        <h3 id="run-improvements-panel">最近改进</h3>
        <ul className="summary-list summary-list--compact summary-list--two-column">
          {improvementSummary.map((entry) => (
            <li key={entry.label}>
              <strong>{entry.label}</strong>
              <span>{entry.value}</span>
            </li>
          ))}
        </ul>
      </section>

      <section className="run-card" aria-labelledby="run-actions-panel">
        <p className="eyebrow">历史</p>
        <h3 id="run-actions-panel">操作历史摘要</h3>
        {actionHistory.length > 0 ? (
          <ol className="summary-list summary-list--compact summary-list--two-column">
            {actionHistory.map((entry) => (
              <li key={entry.id}>
                <strong>{entry.title}</strong>
                <span>{entry.detail}</span>
              </li>
            ))}
          </ol>
        ) : (
          <p className="muted-copy">正在等待实时运行事件。</p>
        )}
      </section>

      {snapshot.isHistorical ? <HistoricalLogNotice runId={runId} /> : null}

      <Link to={`/runs/${runId}/results`}>前往结果页</Link>
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
  const workerCoverageLookup = useMemo(() => buildWorkerCoverageLookup(parallel), [parallel]);
  const workerOutputRows = useMemo(() => buildWorkerOutputRows(parallel), [parallel]);
  const workerPageCount = Math.ceil(workerOutputRows.length / WORKER_OUTPUT_PAGE_SIZE);
  const [workerPage, setWorkerPage] = useState(0);
  const isPreprocessingPhase = snapshot.phase.key === 'preprocessing';
  const mutationDisabled = isMutationDisabled(snapshot.mutationEnabled);

  useEffect(() => {
    setWorkerPage((current) => {
      if (workerPageCount === 0) {
        return 0;
      }

      return Math.min(current, workerPageCount - 1);
    });
  }, [workerPageCount]);

  const visibleWorkerRows = useMemo(() => {
    const start = workerPage * WORKER_OUTPUT_PAGE_SIZE;
    return workerOutputRows.slice(start, start + WORKER_OUTPUT_PAGE_SIZE);
  }, [workerOutputRows, workerPage]);

  return (
    <>
      <div className="run-page__hero">
        <div>
          <p className="eyebrow">运行</p>
          <h2>并行运行状态</h2>
          <p className="run-page__lead">
            <code>{snapshot.runId}</code> 的并行模式快照，页面会先根据最新的批次感知后端状态恢复，
            {snapshot.isHistorical
              ? '当前展示的是历史快照，不会再继续实时更新。'
              : '然后再继续实时更新。'}
          </p>
        </div>

        <div className="run-status-badges">
          <span className="run-badge">状态：{translateStatus(snapshot.status)}</span>
          <span className="run-badge">阶段：{translatePhaseLabel(snapshot.phase)}</span>
          <span className="run-badge">当前批次：{parallel.currentBatch}</span>
          <span className="run-badge">实时连接：{CONNECTION_LABELS[connectionState]}</span>
        </div>
      </div>

      <section className="run-card" aria-labelledby="run-parallel-metrics-panel">
        <p className="eyebrow">指标</p>
        <h3 id="run-parallel-metrics-panel">核心指标</h3>
        <div className="metric-grid metric-grid--hero">
          <article>
            <span>变异分数</span>
            <strong>
              {mutationDisabled
                ? '未启用（测试生成消融模式）'
                : formatPercent(snapshot.metrics.globalMutationScore)}
            </strong>
          </article>
          <article>
            <span>行覆盖率</span>
            <strong>{formatPercent(snapshot.metrics.lineCoverage)}</strong>
          </article>
          <article>
            <span>分支覆盖率</span>
            <strong>{formatPercent(snapshot.metrics.branchCoverage)}</strong>
          </article>
        </div>
        <dl className="detail-grid detail-grid--compact">
          <div>
            <dt>变异分析</dt>
            <dd>{getMutationStatusText(snapshot.mutationEnabled)}</dd>
          </div>
          <div>
            <dt>当前批次</dt>
            <dd>{parallel.currentBatch}</dd>
          </div>
          <div>
            <dt>运行中目标</dt>
            <dd>{parallel.activeTargets.length}</dd>
          </div>
          <div>
            <dt>最新工作线程更新</dt>
            <dd>{parallel.workerCards.length}</dd>
          </div>
          <div>
            <dt>已完成批次组</dt>
            <dd>{parallel.batchResults.length}</dd>
          </div>
          <div>
            <dt>测试总数</dt>
            <dd>{formatMetricValue(snapshot.metrics.totalTests)}</dd>
          </div>
          {mutationDisabled ? null : (
            <>
              <div>
                <dt>变异体总数</dt>
                <dd>{formatMetricValue(snapshot.metrics.globalTotalMutants)}</dd>
              </div>
              <div>
                <dt>已杀死变异体</dt>
                <dd>{formatMetricValue(snapshot.metrics.globalKilledMutants)}</dd>
              </div>
            </>
          )}
          <div>
            <dt>迭代次数</dt>
            <dd>{snapshot.iteration}</dd>
          </div>
        </dl>
        <div className="decision-reasoning">
          <strong>决策说明</strong>
          <p>{snapshot.decisionReasoning ?? '暂未发布决策说明。'}</p>
        </div>
      </section>

      {!isPreprocessingPhase && parallelStatsSummary.length > 0 ? (
        <section className="run-card" aria-labelledby="run-parallel-summary-panel">
          <p className="eyebrow">摘要</p>
          <h3 id="run-parallel-summary-panel">批次摘要</h3>
          <ul className="summary-list summary-list--compact summary-list--two-column">
            {parallelStatsSummary.map((entry) => (
              <li key={entry.label}>
                <strong>{entry.label}</strong>
                <span>{entry.value}</span>
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {!isPreprocessingPhase ? (
        <section className="run-card" aria-labelledby="run-worker-panel">
          <div className="run-card__header run-card__header--compact">
            <div>
              <p className="eyebrow">工作线程</p>
              <h3 id="run-worker-panel">工作线程输出</h3>
            </div>
            <span className="run-badge">实时连接：{CONNECTION_LABELS[connectionState]}</span>
          </div>
          {workerOutputRows.length > 0 ? (
            <>
              <table className="worker-output-table">
                <thead>
                  <tr>
                    <th scope="col">目标</th>
                    <th scope="col">状态</th>
                    <th scope="col">测试</th>
                    <th scope="col">变异体</th>
                    <th scope="col">已杀死</th>
                    <th scope="col">分数</th>
                    <th scope="col">覆盖率</th>
                  </tr>
                </thead>
                <tbody>
                  {visibleWorkerRows.map((worker) => (
                    <tr key={worker.key} className="worker-output-row">
                      <td className="worker-output-row__target">
                        <strong title={worker.targetId || worker.key}>
                          {worker.targetId || '未命名目标'}
                        </strong>
                        <span>
                          {worker.className}.{worker.methodName}
                        </span>
                        {worker.error ? (
                          <p className="worker-output-row__error">{worker.error}</p>
                        ) : null}
                      </td>
                      <td>
                        <span
                          className={
                            worker.success
                              ? 'worker-pill worker-pill--success'
                              : 'worker-pill worker-pill--error'
                          }
                        >
                          {formatWorkerStatusLabel(worker.success)}
                        </span>
                      </td>
                      <td>{worker.testsGenerated}</td>
                      <td>
                        {mutationDisabled ? '未启用' : formatMetricValue(worker.mutantsGenerated)}
                      </td>
                      <td>
                        {mutationDisabled ? '未启用' : formatMetricValue(worker.mutantsKilled)}
                      </td>
                      <td>
                        {mutationDisabled
                          ? '未启用（测试生成消融模式）'
                          : formatPercent(worker.localMutationScore)}
                      </td>
                      <td>
                        {formatPercent(
                          worker.methodCoverage ?? workerCoverageLookup.get(worker.targetId),
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {workerPageCount > 1 ? (
                <div className="run-log-viewer__pagination">
                  <button
                    type="button"
                    className="run-log-viewer__pager-button"
                    onClick={() => setWorkerPage((current) => Math.max(current - 1, 0))}
                    disabled={workerPage === 0}
                  >
                    上一页
                  </button>
                  <span className="muted-copy">
                    第 {workerPage + 1} / {workerPageCount} 页
                  </span>
                  <button
                    type="button"
                    className="run-log-viewer__pager-button"
                    onClick={() =>
                      setWorkerPage((current) => Math.min(current + 1, workerPageCount - 1))
                    }
                    disabled={workerPage >= workerPageCount - 1}
                  >
                    下一页
                  </button>
                </div>
              ) : null}
            </>
          ) : (
            <p className="muted-copy">正在等待首个工作线程批次完成。</p>
          )}
        </section>
      ) : (
        <section className="run-card" aria-labelledby="run-preprocessing-panel">
          <p className="eyebrow">准备</p>
          <h3 id="run-preprocessing-panel">并行预处理</h3>
          <p className="muted-copy">预处理完成后会显示工作线程输出。下方仍可查看实时日志。</p>
        </section>
      )}

      {snapshot.isHistorical ? (
        <HistoricalLogNotice runId={runId} />
      ) : (
        <LogViewer runId={runId} runStatus={snapshot.status} />
      )}

      <Link to={`/runs/${runId}/results`}>前往结果页</Link>
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
  const snapshotStatus = snapshot?.status ?? null;
  const snapshotRef = useRef<RunSnapshot | null>(null);
  const snapshotSignatureRef = useRef<string | null>(null);
  const pageErrorRef = useRef<string | null>(null);

  useEffect(() => {
    snapshotRef.current = snapshot;
    snapshotSignatureRef.current = snapshot ? JSON.stringify(snapshot) : null;
  }, [snapshot]);

  useEffect(() => {
    pageErrorRef.current = pageError;
  }, [pageError]);

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

        snapshotSignatureRef.current = JSON.stringify(initialSnapshot);
        snapshotRef.current = initialSnapshot;
        setSnapshot(initialSnapshot);
        setIsLoading(false);

        if (isTerminalRunStatus(initialSnapshot.status)) {
          setConnectionState('ended');
          return;
        }

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

              if (
                isTerminalRunStatus(event.status) ||
                event.type === 'run.completed' ||
                event.type === 'run.failed'
              ) {
                setConnectionState('ended');
                return;
              }

              setConnectionState('live');
            },
            onError: () => {
              if (active && !isTerminalRunStatus(snapshotRef.current?.status)) {
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

        setPageError(error instanceof Error ? error.message : '无法加载运行快照。');
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

  useEffect(() => {
    if (
      snapshot === null ||
      isTerminalRunStatus(snapshotStatus) ||
      (connectionState !== 'unavailable' && connectionState !== 'error')
    ) {
      return;
    }

    let active = true;

    async function refreshSnapshot() {
      try {
        const nextSnapshot = await fetchRunSnapshot(runId);
        if (!active) {
          return;
        }

        const nextSnapshotSignature = JSON.stringify(nextSnapshot);

        if (snapshotSignatureRef.current !== nextSnapshotSignature) {
          snapshotSignatureRef.current = nextSnapshotSignature;
          snapshotRef.current = nextSnapshot;
          setSnapshot(nextSnapshot);
        }

        if (pageErrorRef.current !== null) {
          pageErrorRef.current = null;
          setPageError(null);
        }
      } catch (error) {
        if (!active) {
          return;
        }

        setPageError(error instanceof Error ? error.message : '无法刷新运行快照。');
      }
    }

    const intervalId = window.setInterval(() => {
      void refreshSnapshot();
    }, SNAPSHOT_POLL_MS);

    return () => {
      active = false;
      window.clearInterval(intervalId);
    };
  }, [connectionState, runId, snapshot, snapshotStatus]);

  const improvementSummary = useMemo(
    () => (snapshot ? buildImprovementSummary(snapshot) : []),
    [snapshot],
  );

  if (isLoading) {
    return (
      <section className="panel run-page">
        <p className="eyebrow">运行</p>
        <h2>运行状态</h2>
        <p>
          正在加载 <code>{runId}</code> 的快照...
        </p>
      </section>
    );
  }

  if (snapshot === null) {
    return (
      <section className="panel run-page">
        <p className="eyebrow">运行</p>
        <h2>运行状态</h2>
        <p role="alert">{pageError ?? '运行快照当前不可用。'}</p>
      </section>
    );
  }

  return (
    <section className="panel run-page">
      {pageError ? <p role="alert">{pageError}</p> : null}
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
