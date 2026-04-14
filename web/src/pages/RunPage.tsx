import { useEffect, useMemo, useRef, useState } from 'react';
import { Link, useParams } from 'react-router-dom';

import { Alert, AlertDescription } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Separator } from '@/components/ui/separator';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
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
    return '—';
  }

  return `${(value * 100).toFixed(1)}%`;
}

function formatMetricValue(value: number | null | undefined): string {
  if (typeof value !== 'number' || Number.isNaN(value)) {
    return '—';
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
    return '—';
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
  return isMutationDisabled(mutationEnabled) ? '未启用' : '已启用';
}

function InfoRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between py-1.5">
      <span className="text-xs text-muted-foreground">{label}</span>
      <span className="text-xs font-medium">{value}</span>
    </div>
  );
}

function MetricCard({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md bg-muted/50 p-2.5">
      <p className="text-xs text-muted-foreground">{label}</p>
      <p className="text-lg font-bold mt-0.5">{value}</p>
    </div>
  );
}

function HistoricalLogNotice(props: { runId: string }) {
  const { runId } = props;

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm">历史日志</CardTitle>
      </CardHeader>
      <CardContent>
        <p className="text-xs text-muted-foreground mb-2">
          这次运行是从已落盘记录中恢复出来的，实时日志缓冲区不会再重建。仍可直接下载
          <code> run.log </code>
          查看完整日志。
        </p>
        <Button variant="outline" size="sm" className="h-7 text-xs" asChild>
          <Link to={`/api/runs/${runId}/artifacts/run-log`}>下载 run.log</Link>
        </Button>
      </CardContent>
    </Card>
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
    <div className="space-y-4">
      {/* 标题 */}
      <div>
        <p className="text-xs font-mono text-muted-foreground tracking-widest uppercase mb-1">
          运行
        </p>
        <h1 className="text-xl font-semibold">运行状态</h1>
        <p className="text-sm text-muted-foreground mt-1">
          <code className="text-xs bg-muted px-1 rounded">{snapshot.runId}</code> 的标准模式快照。
          {snapshot.isHistorical ? '当前展示历史快照，不会再恢复实时更新。' : ''}
        </p>
        <div className="flex flex-wrap gap-1.5 mt-2">
          <Badge variant="outline" className="text-xs h-5 px-1.5">
            状态：{translateStatus(snapshot.status)}
          </Badge>
          <Badge variant="outline" className="text-xs h-5 px-1.5">
            阶段：{translatePhaseLabel(snapshot.phase)}
          </Badge>
          <Badge variant="outline" className="text-xs h-5 px-1.5">
            实时连接：{CONNECTION_LABELS[connectionState]}
          </Badge>
        </div>
      </div>

      {/* 核心指标 */}
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base">核心指标</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="grid grid-cols-3 gap-3">
            <MetricCard
              label="变异分数"
              value={mutationDisabled ? '未启用' : formatPercent(snapshot.metrics.mutationScore)}
            />
            <MetricCard label="行覆盖率" value={formatPercent(snapshot.metrics.lineCoverage)} />
            <MetricCard label="分支覆盖率" value={formatPercent(snapshot.metrics.branchCoverage)} />
          </div>
          <Separator />
          <div className="grid grid-cols-2 gap-x-8 divide-y divide-border sm:divide-y-0 sm:grid-cols-2">
            <div className="divide-y divide-border">
              <InfoRow label="变异分析" value={getMutationStatusText(snapshot.mutationEnabled)} />
              <InfoRow label="当前目标" value={formatTarget(snapshot.currentTarget)} />
              <InfoRow label="上一个目标" value={formatTarget(snapshot.previousTarget)} />
              <InfoRow label="测试总数" value={formatMetricValue(snapshot.metrics.totalTests)} />
            </div>
            <div className="divide-y divide-border">
              <InfoRow
                label="当前方法覆盖率"
                value={formatPercent(snapshot.metrics.currentMethodCoverage)}
              />
              {mutationDisabled ? null : (
                <>
                  <InfoRow
                    label="已杀死变异体"
                    value={formatMetricValue(snapshot.metrics.killedMutants)}
                  />
                  <InfoRow
                    label="存活变异体"
                    value={formatMetricValue(snapshot.metrics.survivedMutants)}
                  />
                </>
              )}
              <InfoRow label="LLM 调用" value={`${snapshot.llmCalls} / ${snapshot.budget}`} />
              <InfoRow label="迭代次数" value={String(snapshot.iteration)} />
            </div>
          </div>
        </CardContent>
      </Card>

      {/* 决策面板 */}
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm">决策面板</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-xs text-muted-foreground">
            {snapshot.decisionReasoning ?? '暂未发布决策说明。'}
          </p>
        </CardContent>
      </Card>

      {/* 改进 + 操作历史 并排 */}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">最近改进</CardTitle>
          </CardHeader>
          <CardContent className="pt-0">
            <div className="divide-y divide-border">
              {improvementSummary.map((entry) => (
                <InfoRow key={entry.label} label={entry.label} value={entry.value} />
              ))}
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">操作历史摘要</CardTitle>
          </CardHeader>
          <CardContent className="pt-0">
            {actionHistory.length > 0 ? (
              <div className="divide-y divide-border">
                {actionHistory.map((entry) => (
                  <InfoRow key={entry.id} label={entry.title} value={entry.detail} />
                ))}
              </div>
            ) : (
              <p className="text-xs text-muted-foreground">正在等待实时运行事件。</p>
            )}
          </CardContent>
        </Card>
      </div>

      {snapshot.isHistorical ? <HistoricalLogNotice runId={runId} /> : null}

      <Separator />

      <Button variant="ghost" size="sm" className="text-xs" asChild>
        <Link to={`/runs/${runId}/results`}>前往结果页</Link>
      </Button>
    </div>
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
    <div className="space-y-4">
      {/* 标题 */}
      <div>
        <p className="text-xs font-mono text-muted-foreground tracking-widest uppercase mb-1">
          运行
        </p>
        <h1 className="text-xl font-semibold">并行运行状态</h1>
        <p className="text-sm text-muted-foreground mt-1">
          <code className="text-xs bg-muted px-1 rounded">{snapshot.runId}</code> 的并行模式快照。
          {snapshot.isHistorical ? '当前展示历史快照，不会再继续实时更新。' : ''}
        </p>
        <div className="flex flex-wrap gap-1.5 mt-2">
          <Badge variant="outline" className="text-xs h-5 px-1.5">
            状态：{translateStatus(snapshot.status)}
          </Badge>
          <Badge variant="outline" className="text-xs h-5 px-1.5">
            阶段：{translatePhaseLabel(snapshot.phase)}
          </Badge>
          <Badge variant="outline" className="text-xs h-5 px-1.5">
            当前批次：{parallel.currentBatch}
          </Badge>
          <Badge variant="outline" className="text-xs h-5 px-1.5">
            实时连接：{CONNECTION_LABELS[connectionState]}
          </Badge>
        </div>
      </div>

      {/* 核心指标 */}
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base">核心指标</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="grid grid-cols-3 gap-3">
            <MetricCard
              label="变异分数"
              value={
                mutationDisabled ? '未启用' : formatPercent(snapshot.metrics.globalMutationScore)
              }
            />
            <MetricCard label="行覆盖率" value={formatPercent(snapshot.metrics.lineCoverage)} />
            <MetricCard label="分支覆盖率" value={formatPercent(snapshot.metrics.branchCoverage)} />
          </div>
          <Separator />
          <div className="grid grid-cols-2 gap-x-8">
            <div className="divide-y divide-border">
              <InfoRow label="变异分析" value={getMutationStatusText(snapshot.mutationEnabled)} />
              <InfoRow label="当前批次" value={String(parallel.currentBatch)} />
              <InfoRow label="运行中目标" value={String(parallel.activeTargets.length)} />
              <InfoRow label="最新工作线程" value={String(parallel.workerCards.length)} />
            </div>
            <div className="divide-y divide-border">
              <InfoRow label="已完成批次组" value={String(parallel.batchResults.length)} />
              <InfoRow label="测试总数" value={formatMetricValue(snapshot.metrics.totalTests)} />
              {mutationDisabled ? null : (
                <>
                  <InfoRow
                    label="变异体总数"
                    value={formatMetricValue(snapshot.metrics.globalTotalMutants)}
                  />
                  <InfoRow
                    label="已杀死变异体"
                    value={formatMetricValue(snapshot.metrics.globalKilledMutants)}
                  />
                </>
              )}
              <InfoRow label="迭代次数" value={String(snapshot.iteration)} />
            </div>
          </div>
          {snapshot.decisionReasoning ? (
            <>
              <Separator />
              <p className="text-xs text-muted-foreground">{snapshot.decisionReasoning}</p>
            </>
          ) : null}
        </CardContent>
      </Card>

      {/* 批次摘要 */}
      {!isPreprocessingPhase && parallelStatsSummary.length > 0 ? (
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">批次摘要</CardTitle>
          </CardHeader>
          <CardContent className="pt-0">
            <div className="grid grid-cols-2 gap-x-8 divide-y divide-border sm:divide-y-0">
              <div className="divide-y divide-border">
                {parallelStatsSummary
                  .slice(0, Math.ceil(parallelStatsSummary.length / 2))
                  .map((entry) => (
                    <InfoRow key={entry.label} label={entry.label} value={entry.value} />
                  ))}
              </div>
              <div className="divide-y divide-border">
                {parallelStatsSummary
                  .slice(Math.ceil(parallelStatsSummary.length / 2))
                  .map((entry) => (
                    <InfoRow key={entry.label} label={entry.label} value={entry.value} />
                  ))}
              </div>
            </div>
          </CardContent>
        </Card>
      ) : null}

      {/* 工作线程输出 */}
      {!isPreprocessingPhase ? (
        <Card>
          <CardHeader className="pb-3">
            <div className="flex items-center justify-between">
              <CardTitle className="text-base">工作线程输出</CardTitle>
              <Badge variant="outline" className="text-xs h-5 px-1.5">
                {CONNECTION_LABELS[connectionState]}
              </Badge>
            </div>
          </CardHeader>
          <CardContent className="pt-0">
            {workerOutputRows.length > 0 ? (
              <>
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead className="text-xs">目标</TableHead>
                      <TableHead className="text-xs">状态</TableHead>
                      <TableHead className="text-xs">测试</TableHead>
                      <TableHead className="text-xs">变异体</TableHead>
                      <TableHead className="text-xs">已杀死</TableHead>
                      <TableHead className="text-xs">分数</TableHead>
                      <TableHead className="text-xs">覆盖率</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {visibleWorkerRows.map((worker) => (
                      <TableRow key={worker.key}>
                        <TableCell className="text-xs">
                          <div
                            className="font-medium max-w-[200px] truncate"
                            title={worker.targetId || worker.key}
                          >
                            {worker.targetId || '未命名目标'}
                          </div>
                          <div className="text-muted-foreground text-xs truncate max-w-[200px]">
                            {worker.className}.{worker.methodName}
                          </div>
                          {worker.error ? (
                            <div className="text-destructive text-xs mt-0.5 max-w-[200px] truncate">
                              {worker.error}
                            </div>
                          ) : null}
                        </TableCell>
                        <TableCell>
                          <Badge
                            variant={worker.success ? 'default' : 'destructive'}
                            className="text-xs h-5 px-1.5"
                          >
                            {formatWorkerStatusLabel(worker.success)}
                          </Badge>
                        </TableCell>
                        <TableCell className="text-xs">{worker.testsGenerated}</TableCell>
                        <TableCell className="text-xs">
                          {mutationDisabled ? '—' : formatMetricValue(worker.mutantsGenerated)}
                        </TableCell>
                        <TableCell className="text-xs">
                          {mutationDisabled ? '—' : formatMetricValue(worker.mutantsKilled)}
                        </TableCell>
                        <TableCell className="text-xs">
                          {mutationDisabled ? '—' : formatPercent(worker.localMutationScore)}
                        </TableCell>
                        <TableCell className="text-xs">
                          {formatPercent(
                            worker.methodCoverage ?? workerCoverageLookup.get(worker.targetId),
                          )}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
                {workerPageCount > 1 ? (
                  <div className="flex items-center justify-between mt-3">
                    <Button
                      variant="outline"
                      size="sm"
                      className="h-7 text-xs"
                      onClick={() => setWorkerPage((current) => Math.max(current - 1, 0))}
                      disabled={workerPage === 0}
                    >
                      上一页
                    </Button>
                    <span className="text-xs text-muted-foreground">
                      第 {workerPage + 1} / {workerPageCount} 页
                    </span>
                    <Button
                      variant="outline"
                      size="sm"
                      className="h-7 text-xs"
                      onClick={() =>
                        setWorkerPage((current) => Math.min(current + 1, workerPageCount - 1))
                      }
                      disabled={workerPage >= workerPageCount - 1}
                    >
                      下一页
                    </Button>
                  </div>
                ) : null}
              </>
            ) : (
              <p className="text-xs text-muted-foreground">正在等待首个工作线程批次完成。</p>
            )}
          </CardContent>
        </Card>
      ) : (
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">并行预处理</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-xs text-muted-foreground">
              预处理完成后会显示工作线程输出。下方仍可查看实时日志。
            </p>
          </CardContent>
        </Card>
      )}

      {snapshot.isHistorical ? (
        <HistoricalLogNotice runId={runId} />
      ) : (
        <LogViewer runId={runId} runStatus={snapshot.status} />
      )}

      <Separator />

      <Button variant="ghost" size="sm" className="text-xs" asChild>
        <Link to={`/runs/${runId}/results`}>前往结果页</Link>
      </Button>
    </div>
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
      <Card>
        <CardHeader>
          <CardTitle>运行状态</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground">
            正在加载 <code>{runId}</code> 的快照...
          </p>
        </CardContent>
      </Card>
    );
  }

  if (snapshot === null) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>运行状态</CardTitle>
        </CardHeader>
        <CardContent>
          <p role="alert" className="text-sm text-destructive">
            {pageError ?? '运行快照当前不可用。'}
          </p>
        </CardContent>
      </Card>
    );
  }

  return (
    <div>
      {pageError ? (
        <Alert variant="destructive" className="mb-4" role="alert">
          <AlertDescription className="text-xs">{pageError}</AlertDescription>
        </Alert>
      ) : null}
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
    </div>
  );
}
