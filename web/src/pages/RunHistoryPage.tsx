import { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';

import { fetchRunHistory, type RunHistoryEntry } from '../lib/api';

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

function translateLabel(value: string | null | undefined): string {
  if (!value) {
    return '未知';
  }

  return STATUS_LABELS[value] ?? value;
}

function formatPercent(value: number | null | undefined): string {
  if (typeof value !== 'number' || Number.isNaN(value)) {
    return '暂无';
  }

  return `${(value * 100).toFixed(1)}%`;
}

function formatNumber(value: number | null | undefined): string {
  if (typeof value !== 'number' || Number.isNaN(value)) {
    return '暂无';
  }

  return value.toLocaleString();
}

function formatDateTime(value: string | null | undefined): string {
  if (!value) {
    return '暂无';
  }

  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }

  return date.toLocaleString();
}

function buildEndedAt(entry: RunHistoryEntry): string | null {
  return entry.completedAt ?? entry.failedAt ?? null;
}

function buildDuration(entry: RunHistoryEntry): string {
  if (!entry.startedAt) {
    return '未开始';
  }

  const startedAt = new Date(entry.startedAt);
  const endedAt = buildEndedAt(entry);
  const endTime = endedAt ? new Date(endedAt) : new Date();
  if (Number.isNaN(startedAt.getTime()) || Number.isNaN(endTime.getTime())) {
    return '暂无';
  }

  const totalSeconds = Math.max(Math.round((endTime.getTime() - startedAt.getTime()) / 1000), 0);
  if (totalSeconds < 60) {
    return `${totalSeconds} 秒`;
  }

  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  if (minutes < 60) {
    return `${minutes} 分 ${seconds} 秒`;
  }

  const hours = Math.floor(minutes / 60);
  const remainingMinutes = minutes % 60;
  return `${hours} 小时 ${remainingMinutes} 分`;
}

function HistoryCard(props: { entry: RunHistoryEntry }) {
  const { entry } = props;
  const endedAt = buildEndedAt(entry);

  return (
    <article className="run-card history-card">
      <div className="history-card__header">
        <div>
          <p className="eyebrow">运行记录</p>
          <h3>{entry.runId}</h3>
          <p className="history-card__path">{entry.projectPath}</p>
        </div>
        <div className="run-status-badges history-card__badges">
          <span className="run-badge">状态：{translateLabel(entry.status)}</span>
          <span className="run-badge">模式：{translateLabel(entry.mode)}</span>
          <span className="run-badge">
            阶段：{translateLabel(entry.phase.key ?? entry.phase.label)}
          </span>
        </div>
      </div>

      <div className="metric-grid history-card__metrics">
        <article>
          <span>变异分数</span>
          <strong>
            {formatPercent(
              entry.mode === 'parallel'
                ? entry.metrics.globalMutationScore
                : entry.metrics.mutationScore,
            )}
          </strong>
        </article>
        <article>
          <span>行覆盖率</span>
          <strong>{formatPercent(entry.metrics.lineCoverage)}</strong>
        </article>
        <article>
          <span>测试总数</span>
          <strong>{formatNumber(entry.metrics.totalTests)}</strong>
        </article>
        <article>
          <span>LLM 调用</span>
          <strong>
            {formatNumber(entry.llmCalls)} / {formatNumber(entry.budget)}
          </strong>
        </article>
      </div>

      <dl className="detail-grid detail-grid--compact history-card__details">
        <div>
          <dt>创建时间</dt>
          <dd>{formatDateTime(entry.createdAt)}</dd>
        </div>
        <div>
          <dt>开始时间</dt>
          <dd>{formatDateTime(entry.startedAt)}</dd>
        </div>
        <div>
          <dt>结束时间</dt>
          <dd>{formatDateTime(endedAt)}</dd>
        </div>
        <div>
          <dt>持续时间</dt>
          <dd>{buildDuration(entry)}</dd>
        </div>
        <div>
          <dt>迭代次数</dt>
          <dd>{formatNumber(entry.iteration)}</dd>
        </div>
        <div>
          <dt>结果工件</dt>
          <dd>
            {entry.artifacts.finalState?.exists || entry.artifacts.log?.exists ? '可查看' : '暂无'}
          </dd>
        </div>
      </dl>

      {entry.error ? <p className="history-card__error">{entry.error}</p> : null}

      <div className="history-card__actions">
        <Link className="secondary-button history-card__link" to={`/runs/${entry.runId}`}>
          查看运行详情
        </Link>
        <Link className="primary-button history-card__link" to={`/runs/${entry.runId}/results`}>
          查看结果页
        </Link>
      </div>
    </article>
  );
}

export function RunHistoryPage() {
  const [entries, setEntries] = useState<RunHistoryEntry[]>([]);
  const [pageError, setPageError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    let active = true;

    async function loadHistory() {
      setIsLoading(true);
      setPageError(null);

      try {
        const response = await fetchRunHistory();
        if (!active) {
          return;
        }

        setEntries(response.items);
        setIsLoading(false);
      } catch (error) {
        if (!active) {
          return;
        }

        setPageError(error instanceof Error ? error.message : '无法加载运行记录。');
        setIsLoading(false);
      }
    }

    void loadHistory();

    return () => {
      active = false;
    };
  }, []);

  const completedCount = useMemo(
    () => entries.filter((entry) => entry.status === 'completed').length,
    [entries],
  );

  if (isLoading) {
    return (
      <section className="panel run-page run-history-page">
        <p className="eyebrow">运行记录</p>
        <h2>历史运行</h2>
        <p>正在加载历史运行列表...</p>
      </section>
    );
  }

  if (pageError) {
    return (
      <section className="panel run-page run-history-page">
        <p className="eyebrow">运行记录</p>
        <h2>历史运行</h2>
        <p role="alert">{pageError}</p>
      </section>
    );
  }

  return (
    <section className="panel run-page run-history-page">
      <div className="run-page__hero run-history-page__hero">
        <div>
          <p className="eyebrow">运行记录</p>
          <h2>历史运行</h2>
          <p className="run-page__lead">
            这里会保留每次运行的基础信息、阶段状态和结果入口。即使 Web
            服务重启，已经落盘的运行记录仍然可以继续查看。
          </p>
        </div>

        <div className="metric-grid metric-grid--hero run-history-page__summary">
          <article>
            <span>累计运行数</span>
            <strong>{formatNumber(entries.length)}</strong>
          </article>
          <article>
            <span>已完成</span>
            <strong>{formatNumber(completedCount)}</strong>
          </article>
        </div>
      </div>

      {entries.length === 0 ? (
        <section className="run-card history-empty-state">
          <p className="eyebrow">空状态</p>
          <h3>还没有可查看的运行记录</h3>
          <p className="muted-copy">先从首页启动一次运行，完成后这里会自动显示历史记录。</p>
          <Link to="/" className="primary-button history-card__link">
            返回首页启动运行
          </Link>
        </section>
      ) : (
        <div className="history-card-grid">
          {entries.map((entry) => (
            <HistoryCard key={entry.runId} entry={entry} />
          ))}
        </div>
      )}
    </section>
  );
}
