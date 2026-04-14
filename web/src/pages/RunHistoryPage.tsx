import { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { fetchRunHistory, type RunHistoryEntry } from '../lib/api';

const STATUS_LABELS: Record<string, string> = {
  created: '已创建',
  queued: '排队中',
  preprocessing: '预处理中',
  running: '运行中',
  completed: '已完成',
  failed: '失败',
  standard: '标准',
  parallel: '并行',
};

function translateLabel(value: string | null | undefined): string {
  if (!value) {
    return '未知';
  }

  return STATUS_LABELS[value] ?? value;
}

function formatPercent(value: number | null | undefined): string {
  if (typeof value !== 'number' || Number.isNaN(value)) {
    return '—';
  }

  return `${(value * 100).toFixed(1)}%`;
}

function formatNumber(value: number | null | undefined): string {
  if (typeof value !== 'number' || Number.isNaN(value)) {
    return '—';
  }

  return value.toLocaleString();
}

function formatDateTime(value: string | null | undefined): string {
  if (!value) {
    return '—';
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
    return '—';
  }

  const totalSeconds = Math.max(Math.round((endTime.getTime() - startedAt.getTime()) / 1000), 0);
  if (totalSeconds < 60) {
    return `${totalSeconds}s`;
  }

  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  if (minutes < 60) {
    return `${minutes}m${seconds}s`;
  }

  const hours = Math.floor(minutes / 60);
  const remainingMinutes = minutes % 60;
  return `${hours}h${remainingMinutes}m`;
}

function statusVariant(
  status: string | null | undefined,
): 'default' | 'secondary' | 'destructive' | 'outline' {
  if (status === 'completed') return 'default';
  if (status === 'failed') return 'destructive';
  if (status === 'running') return 'secondary';
  return 'outline';
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
      <Card>
        <CardHeader>
          <CardTitle>历史运行</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground">正在加载历史运行列表...</p>
        </CardContent>
      </Card>
    );
  }

  if (pageError) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>历史运行</CardTitle>
        </CardHeader>
        <CardContent>
          <p role="alert" className="text-sm text-destructive">
            {pageError}
          </p>
        </CardContent>
      </Card>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex items-start justify-between">
        <div>
          <p className="text-xs font-mono text-muted-foreground tracking-widest uppercase mb-1">
            运行记录
          </p>
          <h1 className="text-xl font-semibold">历史运行</h1>
          <p className="text-sm text-muted-foreground mt-1">
            保留每次运行的基础信息、阶段状态和结果入口。Web 服务重启后仍可查看落盘的记录。
          </p>
        </div>
        <div className="flex gap-3 text-right">
          <div>
            <p className="text-xs text-muted-foreground">累计</p>
            <p className="text-2xl font-bold">{formatNumber(entries.length)}</p>
          </div>
          <div>
            <p className="text-xs text-muted-foreground">已完成</p>
            <p className="text-2xl font-bold">{formatNumber(completedCount)}</p>
          </div>
        </div>
      </div>

      {entries.length === 0 ? (
        <Card>
          <CardContent className="py-12 text-center">
            <p className="text-sm font-medium mb-1">还没有可查看的运行记录</p>
            <p className="text-xs text-muted-foreground mb-4">
              先从首页启动一次运行，完成后这里会自动显示历史记录。
            </p>
            <Button asChild size="sm">
              <Link to="/">返回首页启动运行</Link>
            </Button>
          </CardContent>
        </Card>
      ) : (
        <Card>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="text-xs">运行 ID</TableHead>
                <TableHead className="text-xs">项目路径</TableHead>
                <TableHead className="text-xs">状态</TableHead>
                <TableHead className="text-xs">模式</TableHead>
                <TableHead className="text-xs">变异分数</TableHead>
                <TableHead className="text-xs">行覆盖率</TableHead>
                <TableHead className="text-xs">测试数</TableHead>
                <TableHead className="text-xs">时长</TableHead>
                <TableHead className="text-xs">创建时间</TableHead>
                <TableHead className="text-xs w-32"></TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {entries.map((entry) => (
                <TableRow key={entry.runId}>
                  <TableCell className="text-xs font-mono">{entry.runId}</TableCell>
                  <TableCell className="text-xs max-w-[180px] truncate text-muted-foreground">
                    {entry.projectPath}
                  </TableCell>
                  <TableCell>
                    <Badge variant={statusVariant(entry.status)} className="text-xs h-5 px-1.5">
                      {translateLabel(entry.status)}
                    </Badge>
                  </TableCell>
                  <TableCell>
                    <Badge variant="outline" className="text-xs h-5 px-1.5">
                      {translateLabel(entry.mode)}
                    </Badge>
                  </TableCell>
                  <TableCell className="text-xs">
                    {formatPercent(
                      entry.mode === 'parallel'
                        ? entry.metrics.globalMutationScore
                        : entry.metrics.mutationScore,
                    )}
                  </TableCell>
                  <TableCell className="text-xs">
                    {formatPercent(entry.metrics.lineCoverage)}
                  </TableCell>
                  <TableCell className="text-xs">
                    {formatNumber(entry.metrics.totalTests)}
                  </TableCell>
                  <TableCell className="text-xs">{buildDuration(entry)}</TableCell>
                  <TableCell className="text-xs text-muted-foreground">
                    {formatDateTime(entry.createdAt)}
                  </TableCell>
                  <TableCell>
                    <div className="flex gap-1">
                      <Button variant="ghost" size="sm" className="h-6 text-xs px-2" asChild>
                        <Link
                          className="secondary-button history-card__link"
                          to={`/runs/${entry.runId}`}
                        >
                          详情
                        </Link>
                      </Button>
                      <Button size="sm" className="h-6 text-xs px-2" asChild>
                        <Link
                          className="primary-button history-card__link"
                          to={`/runs/${entry.runId}/results`}
                        >
                          结果
                        </Link>
                      </Button>
                    </div>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </Card>
      )}
    </div>
  );
}
