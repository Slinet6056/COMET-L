import { Link, Route, Routes } from 'react-router-dom';

import { Button } from '@/components/ui/button';
import { Separator } from '@/components/ui/separator';
import { HomePage } from './pages/HomePage';
import { RunHistoryPage } from './pages/RunHistoryPage';
import { RunPage } from './pages/RunPage';
import { RunResultsPage } from './pages/RunResultsPage';

export function App() {
  return (
    <div className="min-h-screen bg-background">
      <header className="sticky top-0 z-10 bg-background/95 backdrop-blur border-b border-border">
        <div className="max-w-5xl mx-auto px-4 h-12 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <span className="text-xs font-mono text-muted-foreground tracking-widest uppercase">
              COMET-L
            </span>
            <Separator orientation="vertical" className="h-4" />
            <span className="text-sm font-medium">Web 控制台</span>
          </div>
          <nav className="flex items-center gap-1" aria-label="主导航">
            <Button variant="ghost" size="sm" asChild>
              <Link to="/">首页</Link>
            </Button>
            <Button variant="ghost" size="sm" asChild>
              <Link to="/runs/history">运行记录</Link>
            </Button>
          </nav>
        </div>
      </header>

      <main className="max-w-5xl mx-auto px-4 py-6">
        <Routes>
          <Route path="/" element={<HomePage />} />
          <Route path="/runs/history" element={<RunHistoryPage />} />
          <Route path="/runs/:runId" element={<RunPage />} />
          <Route path="/runs/:runId/results" element={<RunResultsPage />} />
        </Routes>
      </main>
    </div>
  );
}
