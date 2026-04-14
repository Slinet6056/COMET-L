import { useEffect, useMemo, useState } from 'react';
import { Link, useParams } from 'react-router-dom';

import { Alert, AlertDescription } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Separator } from '@/components/ui/separator';
import { fetchRunResults, type RunResultsArtifact, type RunResultsResponse } from '../lib/api';

const STATUS_LABELS: Record<string, string> = {
  created: '已创建',
  queued: '排队中',
  preprocessing: '预处理中',
  running: '运行中',
  completed: '已完成',
  failed: '失败',
  standard: '标准单目标演化',
  parallel: '并行批次演化',
};

function formatPercent(value: number | null | undefined): string {
  if (typeof value !== 'number' || Number.isNaN(value)) {
    return '—';
  }

  return `${(value * 100).toFixed(1)}%`;
}

function formatCount(value: number | null | undefined): string {
  if (typeof value !== 'number' || Number.isNaN(value)) {
    return '—';
  }

  return value.toLocaleString();
}

function formatDate(value: string | null | undefined): string {
  if (!value) {
    return '不可用';
  }

  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }

  return date.toLocaleString();
}

function formatBytes(value: number | null | undefined): string {
  if (typeof value !== 'number' || Number.isNaN(value)) {
    return '不可用';
  }

  if (value < 1024) {
    return `${value} B`;
  }

  const units = ['KB', 'MB', 'GB'];
  let size = value / 1024;
  let unitIndex = 0;
  while (size >= 1024 && unitIndex < units.length - 1) {
    size /= 1024;
    unitIndex += 1;
  }

  return `${size.toFixed(1)} ${units[unitIndex]}`;
}

function translateStatus(value: string | null | undefined): string {
  if (!value) {
    return '未知';
  }

  return STATUS_LABELS[value] ?? value;
}

function translatePhaseLabel(label: string | null | undefined): string {
  if (!label) {
    return '未知';
  }

  return STATUS_LABELS[label.toLowerCase()] ?? label;
}

function buildTerminalMessage(results: RunResultsResponse): string {
  if (results.status === 'failed') {
    return '本次运行以失败结束。如果后端已生成 final-state 数据和运行日志，仍可在此获取。';
  }

  if (results.status === 'completed') {
    return '本次运行已完成。下方摘要展示最终快照以及按本次运行聚合的数据库统计。';
  }

  return '本次运行尚未进入终态。页面仍会展示当前可获得的最新结果数据。';
}

function isMutationDisabled(mutationEnabled: boolean | null | undefined): boolean {
  return mutationEnabled === false;
}

function getMutationScoreLabel(results: RunResultsResponse): string {
  return isMutationDisabled(results.mutationEnabled) ? '变异分析状态' : '变异分数';
}

function getMutationScoreDisplay(results: RunResultsResponse): string {
  return isMutationDisabled(results.mutationEnabled)
    ? '未启用'
    : formatPercent(getDisplayMutationScore(results));
}

function buildModeHighlights(results: RunResultsResponse): Array<{ label: string; value: string }> {
  const metrics = results.summary.metrics;
  const mutationDisabled = isMutationDisabled(results.mutationEnabled);

  if (results.mode === 'parallel') {
    return [
      { label: '执行模式', value: '并行批次演化' },
      {
        label: mutationDisabled ? '变异分析状态' : '全局变异分数',
        value: mutationDisabled ? '未启用' : formatPercent(metrics.globalMutationScore),
      },
      {
        label: '全局已杀死变异体',
        value: mutationDisabled ? '未启用' : formatCount(metrics.globalKilledMutants),
      },
      {
        label: '全局存活变异体',
        value: mutationDisabled ? '未启用' : formatCount(metrics.globalSurvivedMutants),
      },
    ];
  }

  return [
    { label: '执行模式', value: '标准单目标演化' },
    { label: '当前方法覆盖率', value: formatPercent(metrics.currentMethodCoverage) },
    {
      label: '已杀死变异体',
      value: mutationDisabled ? '未启用' : formatCount(metrics.killedMutants),
    },
    {
      label: '存活变异体',
      value: mutationDisabled ? '未启用' : formatCount(metrics.survivedMutants),
    },
  ];
}

function getDisplayTotalMutants(results: RunResultsResponse): number | null | undefined {
  if (isMutationDisabled(results.mutationEnabled)) {
    return null;
  }

  if (typeof results.summary.mutants.total === 'number') {
    return results.summary.mutants.total;
  }

  if (results.mode === 'parallel') {
    return results.summary.metrics.globalTotalMutants ?? results.summary.metrics.totalMutants;
  }

  return results.summary.metrics.totalMutants;
}

function getDisplayMutationScore(results: RunResultsResponse): number | null | undefined {
  if (isMutationDisabled(results.mutationEnabled)) {
    return null;
  }

  const preferredScore =
    results.mode === 'parallel'
      ? results.summary.metrics.globalMutationScore
      : results.summary.metrics.mutationScore;

  if (
    (preferredScore === null || preferredScore === undefined || preferredScore === 0) &&
    results.summary.mutants.total > 0
  ) {
    return results.summary.mutants.killed / results.summary.mutants.total;
  }

  return preferredScore;
}

function ArtifactCard(props: { title: string; artifact?: RunResultsArtifact; testId?: string }) {
  const { title, artifact, testId } = props;

  if (!artifact) {
    return (
      <div className="rounded-lg border border-border p-3">
        <p className="text-sm font-medium">{title}</p>
        <p className="text-xs text-muted-foreground mt-1">工件元数据当前不可用。</p>
      </div>
    );
  }

  return (
    <div className="rounded-lg border border-border p-3 space-y-2">
      <div className="flex items-center justify-between gap-2">
        <div className="min-w-0">
          <p className="text-sm font-medium">{title}</p>
          <p className="text-xs text-muted-foreground truncate">{artifact.filename}</p>
        </div>
        <Badge
          variant={artifact.exists ? 'default' : 'destructive'}
          className="text-xs flex-shrink-0 h-5 px-1.5"
        >
          {artifact.exists ? '可用' : '缺失'}
        </Badge>
      </div>

      <div className="grid grid-cols-2 gap-2 text-xs">
        <div>
          <span className="text-muted-foreground">更新时间</span>
          <p>{formatDate(artifact.updatedAt)}</p>
        </div>
        <div>
          <span className="text-muted-foreground">大小</span>
          <p>{formatBytes(artifact.sizeBytes)}</p>
        </div>
      </div>

      {artifact.exists ? (
        <Button variant="outline" size="sm" className="w-full h-7 text-xs" asChild>
          <a href={artifact.downloadUrl} data-testid={testId}>
            下载 {artifact.filename}
          </a>
        </Button>
      ) : (
        <p className="text-xs text-muted-foreground">本次运行未生成该工件。</p>
      )}
    </div>
  );
}

function InfoRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between py-1.5">
      <span className="text-xs text-muted-foreground">{label}</span>
      <span className="text-xs font-medium">{value}</span>
    </div>
  );
}

export function RunResultsPage() {
  const { runId = '' } = useParams();
  const [results, setResults] = useState<RunResultsResponse | null>(null);
  const [pageError, setPageError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    let active = true;

    async function loadResults() {
      setIsLoading(true);
      setPageError(null);

      try {
        const payload = await fetchRunResults(runId);
        if (!active) {
          return;
        }

        setResults(payload);
        setIsLoading(false);
      } catch (error) {
        if (!active) {
          return;
        }

        setPageError(error instanceof Error ? error.message : '无法加载运行结果。');
        setIsLoading(false);
      }
    }

    void loadResults();

    return () => {
      active = false;
    };
  }, [runId]);

  const modeHighlights = useMemo(() => (results ? buildModeHighlights(results) : []), [results]);
  const displayTotalMutants = useMemo(
    () => (results ? getDisplayTotalMutants(results) : null),
    [results],
  );

  if (isLoading) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>运行结果</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground">
            正在加载 <code>{runId}</code> 的最终摘要...
          </p>
        </CardContent>
      </Card>
    );
  }

  if (pageError || results === null) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>运行结果</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <p role="alert" className="text-sm text-destructive">
            {pageError ?? '运行结果当前不可用。'}
          </p>
          <Button variant="ghost" size="sm" asChild>
            <Link to={`/runs/${runId}`}>返回运行详情</Link>
          </Button>
        </CardContent>
      </Card>
    );
  }

  return (
    <div className="space-y-4">
      {/* 标题区 */}
      <div>
        <p className="text-xs font-mono text-muted-foreground tracking-widest uppercase mb-1">
          结果
        </p>
        <h1 className="text-xl font-semibold">运行结果</h1>
        <p className="text-sm text-muted-foreground mt-1">
          <code className="text-xs bg-muted px-1 rounded">{results.runId}</code> 的终态摘要。
        </p>
        <div className="flex flex-wrap gap-1.5 mt-2">
          <Badge variant="outline" className="text-xs h-5 px-1.5">
            状态：{translateStatus(results.status)}
          </Badge>
          <Badge variant="outline" className="text-xs h-5 px-1.5">
            阶段：{translatePhaseLabel(results.phase.label)}
          </Badge>
          <Badge variant="outline" className="text-xs h-5 px-1.5">
            模式：{translateStatus(results.mode)}
          </Badge>
          <Badge variant="outline" className="text-xs h-5 px-1.5">
            迭代：{results.iteration}
          </Badge>
          {results.selectedJavaVersion && (
            <Badge
              variant="outline"
              className="text-xs h-5 px-1.5"
              data-testid="java-version-badge"
            >
              Java 版本：{results.selectedJavaVersion}
            </Badge>
          )}
        </div>
      </div>

      <Alert variant={results.status === 'failed' ? 'destructive' : 'default'} aria-live="polite">
        <AlertDescription className="text-xs">{buildTerminalMessage(results)}</AlertDescription>
      </Alert>

      {/* 最终统计 */}
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base">最终统计</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
            {[
              { label: getMutationScoreLabel(results), value: getMutationScoreDisplay(results) },
              { label: '行覆盖率', value: formatPercent(results.summary.metrics.lineCoverage) },
              { label: '分支覆盖率', value: formatPercent(results.summary.metrics.branchCoverage) },
              {
                label: isMutationDisabled(results.mutationEnabled) ? '变异体状态' : '变异体总数',
                value: isMutationDisabled(results.mutationEnabled)
                  ? '未启用'
                  : formatCount(displayTotalMutants),
              },
              { label: '测试总数', value: formatCount(results.summary.metrics.totalTests) },
              {
                label: 'LLM 调用',
                value: `${formatCount(results.llmCalls)} / ${formatCount(results.budget)}`,
              },
            ].map(({ label, value }) => (
              <article key={label} className="rounded-md bg-muted/50 p-2.5">
                <p className="text-xs text-muted-foreground">{label}</p>
                <p className="text-lg font-bold mt-0.5">{value}</p>
              </article>
            ))}
          </div>
        </CardContent>
      </Card>

      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
        {/* 模式摘要 */}
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">模式摘要</CardTitle>
          </CardHeader>
          <CardContent className="pt-0">
            <div className="divide-y divide-border">
              {modeHighlights.map((entry) => (
                <InfoRow key={entry.label} label={entry.label} value={entry.value} />
              ))}
            </div>
          </CardContent>
        </Card>

        {/* 测试与变异体 */}
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">测试与变异体</CardTitle>
          </CardHeader>
          <CardContent className="pt-0">
            <div className="divide-y divide-border">
              <InfoRow
                label="编译通过用例"
                value={`${formatCount(results.summary.tests.compiledCases)} / ${formatCount(results.summary.tests.totalCases)}`}
              />
              <InfoRow label="生成方法数" value={formatCount(results.summary.tests.totalMethods)} />
              <InfoRow
                label="命中目标方法"
                value={formatCount(results.summary.tests.targetMethods)}
              />
              <InfoRow
                label="已评估变异体"
                value={formatCount(results.summary.mutants.evaluated)}
              />
              <InfoRow label="待评估变异体" value={formatCount(results.summary.mutants.pending)} />
              <InfoRow label="无效变异体" value={formatCount(results.summary.mutants.invalid)} />
            </div>
          </CardContent>
        </Card>

        {/* 覆盖率 */}
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">覆盖率</CardTitle>
          </CardHeader>
          <CardContent className="pt-0">
            <div className="divide-y divide-border">
              <InfoRow
                label="最新迭代"
                value={formatCount(results.summary.coverage.latestIteration)}
              />
              <InfoRow
                label="已跟踪方法"
                value={formatCount(results.summary.coverage.methodsTracked)}
              />
              <InfoRow
                label="平均行覆盖率"
                value={formatPercent(results.summary.coverage.averageLineCoverage)}
              />
              <InfoRow
                label="平均分支覆盖率"
                value={formatPercent(results.summary.coverage.averageBranchCoverage)}
              />
              <InfoRow
                label="最终状态"
                value={results.summary.sources.finalState ? '可用' : '缺失'}
              />
              <InfoRow label="运行日志" value={results.summary.sources.runLog ? '可用' : '缺失'} />
            </div>
          </CardContent>
        </Card>
      </div>

      {/* 工件 */}
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base">工件下载</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
            <ArtifactCard title="最终状态 JSON" artifact={results.artifacts.finalState} />
            <ArtifactCard title="运行日志" artifact={results.artifacts.runLog} />
            {results.reportArtifact && (
              <ArtifactCard
                title="Markdown 报告"
                artifact={results.reportArtifact}
                testId="report-download-link"
              />
            )}
          </div>
        </CardContent>
      </Card>

      {/* Pull Request */}
      {results.pullRequestUrl ? (
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">Pull Request 链接</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-xs text-muted-foreground mb-2">
              测试文件已提交至 GitHub 仓库并创建 Pull Request。
            </p>
            <Button variant="outline" size="sm" className="h-7 text-xs" asChild>
              <a
                href={results.pullRequestUrl}
                target="_blank"
                rel="noopener noreferrer"
                data-testid="pr-link"
              >
                查看 Pull Request
              </a>
            </Button>
          </CardContent>
        </Card>
      ) : results.reportArtifact?.exists ? (
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">Pull Request 创建失败</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-xs text-muted-foreground">
              {results.pullRequestError?.trim() ||
                '报告已生成，但 Pull Request 创建失败。请检查 GitHub 授权状态或仓库权限。'}
            </p>
          </CardContent>
        </Card>
      ) : null}

      <Separator />

      <Button variant="ghost" size="sm" className="text-xs" asChild>
        <Link to={`/runs/${runId}`}>返回运行详情</Link>
      </Button>
    </div>
  );
}
