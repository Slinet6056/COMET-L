import { useEffect, useMemo, useState } from 'react';
import { Link, useParams } from 'react-router-dom';

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
    return '暂无';
  }

  return `${(value * 100).toFixed(1)}%`;
}

function formatCount(value: number | null | undefined): string {
  if (typeof value !== 'number' || Number.isNaN(value)) {
    return '暂无';
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
        value: mutationDisabled
          ? '未启用'
          : formatPercent(metrics.globalMutationScore),
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

function ArtifactCard(props: { title: string; artifact?: RunResultsArtifact }) {
  const { title, artifact } = props;

  if (!artifact) {
    return (
      <article className="artifact-card">
        <h4>{title}</h4>
        <p className="muted-copy">工件元数据当前不可用。</p>
      </article>
    );
  }

  return (
    <article className="artifact-card">
      <div className="artifact-card__header">
        <div>
          <h4>{title}</h4>
          <p className="muted-copy">{artifact.filename}</p>
        </div>
        <span
          className={
            artifact.exists ? 'worker-pill worker-pill--success' : 'worker-pill worker-pill--error'
          }
        >
          {artifact.exists ? '可用' : '缺失'}
        </span>
      </div>

      <dl className="detail-grid artifact-card__details">
        <div>
          <dt>更新时间</dt>
          <dd>{formatDate(artifact.updatedAt)}</dd>
        </div>
        <div>
          <dt>大小</dt>
          <dd>{formatBytes(artifact.sizeBytes)}</dd>
        </div>
      </dl>

      {artifact.exists ? (
        <a className="artifact-link" href={artifact.downloadUrl}>
          下载 {artifact.filename}
        </a>
      ) : (
        <p className="muted-copy">本次运行未生成该工件。</p>
      )}
    </article>
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
      <section className="panel run-page">
        <p className="eyebrow">结果</p>
        <h2>运行结果</h2>
        <p>
          正在加载 <code>{runId}</code> 的最终摘要...
        </p>
      </section>
    );
  }

  if (pageError || results === null) {
    return (
      <section className="panel run-page">
        <p className="eyebrow">结果</p>
        <h2>运行结果</h2>
        <p role="alert">{pageError ?? '运行结果当前不可用。'}</p>
        <Link to={`/runs/${runId}`}>返回运行详情</Link>
      </section>
    );
  }

  return (
    <section className="panel run-page results-page">
      <div className="run-page__hero">
        <div>
          <p className="eyebrow">结果</p>
          <h2>运行结果</h2>
          <p className="run-page__lead">
            <code>{results.runId}</code> 的终态摘要。此页面汇总最终运行快照、数据库聚合结果，
            以及可下载的工件。
          </p>
        </div>

        <div className="run-status-badges">
          <span className="run-badge">状态：{translateStatus(results.status)}</span>
          <span className="run-badge">阶段：{translatePhaseLabel(results.phase.label)}</span>
          <span className="run-badge">模式：{translateStatus(results.mode)}</span>
          <span className="run-badge">迭代次数：{results.iteration}</span>
        </div>
      </div>

      <section
        className={`feedback-banner ${results.status === 'failed' ? 'feedback-banner--error' : 'feedback-banner--success'}`}
        aria-live="polite"
      >
        {buildTerminalMessage(results)}
      </section>

      <section className="run-card" aria-labelledby="results-final-stats">
        <p className="eyebrow">最终统计</p>
        <h3 id="results-final-stats">最终统计</h3>
        <div className="metric-grid metric-grid--hero">
          <article>
            <span>{getMutationScoreLabel(results)}</span>
            <strong>{getMutationScoreDisplay(results)}</strong>
          </article>
          <article>
            <span>行覆盖率</span>
            <strong>{formatPercent(results.summary.metrics.lineCoverage)}</strong>
          </article>
          <article>
            <span>分支覆盖率</span>
            <strong>{formatPercent(results.summary.metrics.branchCoverage)}</strong>
          </article>
          <article>
            <span>{isMutationDisabled(results.mutationEnabled) ? '变异体状态' : '变异体总数'}</span>
            <strong>
              {isMutationDisabled(results.mutationEnabled)
                ? '未启用'
                : formatCount(displayTotalMutants)}
            </strong>
          </article>
          <article>
            <span>测试总数</span>
            <strong>{formatCount(results.summary.metrics.totalTests)}</strong>
          </article>
          <article>
            <span>LLM 调用次数</span>
            <strong>
              {formatCount(results.llmCalls)} / {formatCount(results.budget)}
            </strong>
          </article>
        </div>
      </section>

      <section className="run-card" aria-labelledby="results-mode-summary">
        <p className="eyebrow">模式</p>
        <h3 id="results-mode-summary">模式摘要</h3>
        <ul className="summary-list summary-list--compact summary-list--two-column">
          {modeHighlights.map((entry) => (
            <li key={entry.label}>
              <strong>{entry.label}</strong>
              <span>{entry.value}</span>
            </li>
          ))}
        </ul>
      </section>

      <section className="run-card" aria-labelledby="results-test-summary">
        <p className="eyebrow">数据库</p>
        <h3 id="results-test-summary">测试与变异体摘要</h3>
        <ul className="summary-list summary-list--compact summary-list--two-column">
          <li>
            <strong>编译通过用例</strong>
            <span>
              {formatCount(results.summary.tests.compiledCases)} /{' '}
              {formatCount(results.summary.tests.totalCases)}
            </span>
          </li>
          <li>
            <strong>生成的方法数</strong>
            <span>{formatCount(results.summary.tests.totalMethods)}</span>
          </li>
          <li>
            <strong>命中的目标方法</strong>
            <span>{formatCount(results.summary.tests.targetMethods)}</span>
          </li>
          <li>
            <strong>已评估变异体</strong>
            <span>{formatCount(results.summary.mutants.evaluated)}</span>
          </li>
          <li>
            <strong>待评估变异体</strong>
            <span>{formatCount(results.summary.mutants.pending)}</span>
          </li>
          <li>
            <strong>无效变异体</strong>
            <span>{formatCount(results.summary.mutants.invalid)}</span>
          </li>
        </ul>
      </section>

      <section className="run-card" aria-labelledby="results-coverage-summary">
        <p className="eyebrow">覆盖率</p>
        <h3 id="results-coverage-summary">覆盖率摘要</h3>
        <ul className="summary-list summary-list--compact summary-list--two-column">
          <li>
            <strong>数据库最新迭代</strong>
            <span>{formatCount(results.summary.coverage.latestIteration)}</span>
          </li>
          <li>
            <strong>已跟踪方法数</strong>
            <span>{formatCount(results.summary.coverage.methodsTracked)}</span>
          </li>
          <li>
            <strong>平均行覆盖率</strong>
            <span>{formatPercent(results.summary.coverage.averageLineCoverage)}</span>
          </li>
          <li>
            <strong>平均分支覆盖率</strong>
            <span>{formatPercent(results.summary.coverage.averageBranchCoverage)}</span>
          </li>
          <li>
            <strong>最终状态来源</strong>
            <span>{results.summary.sources.finalState ? '可用' : '缺失'}</span>
          </li>
          <li>
            <strong>运行日志来源</strong>
            <span>{results.summary.sources.runLog ? '可用' : '缺失'}</span>
          </li>
        </ul>
      </section>

      <section className="run-card" aria-labelledby="results-artifacts">
        <p className="eyebrow">工件</p>
        <h3 id="results-artifacts">工件下载</h3>
        <div className="artifact-grid">
          <ArtifactCard title="最终状态 JSON" artifact={results.artifacts.finalState} />
          <ArtifactCard title="运行日志" artifact={results.artifacts.runLog} />
        </div>
      </section>

      <Link to={`/runs/${runId}`}>返回运行详情</Link>
    </section>
  );
}
