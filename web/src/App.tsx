import { Link, Route, Routes } from 'react-router-dom';

import { HomePage } from './pages/HomePage';
import { RunHistoryPage } from './pages/RunHistoryPage';
import { RunPage } from './pages/RunPage';
import { RunResultsPage } from './pages/RunResultsPage';

export function App() {
  return (
    <div className="app-shell">
      <header className="app-header">
        <div>
          <p className="eyebrow">COMET-L</p>
          <h1>Web 控制台</h1>
        </div>
        <nav className="app-nav" aria-label="主导航">
          <Link to="/">首页</Link>
          <Link to="/runs/history">运行记录</Link>
        </nav>
      </header>

      <main className="app-main">
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
