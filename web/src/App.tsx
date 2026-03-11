import { Link, Route, Routes } from 'react-router-dom';

import { HomePage } from './pages/HomePage';
import { RunPage } from './pages/RunPage';
import { RunResultsPage } from './pages/RunResultsPage';

export function App() {
  return (
    <div className="app-shell">
      <header className="app-header">
        <div>
          <p className="eyebrow">COMET-L</p>
          <h1>Web Console Scaffold</h1>
        </div>
        <nav className="app-nav" aria-label="Primary">
          <Link to="/">Home</Link>
        </nav>
      </header>

      <main className="app-main">
        <Routes>
          <Route path="/" element={<HomePage />} />
          <Route path="/runs/:runId" element={<RunPage />} />
          <Route path="/runs/:runId/results" element={<RunResultsPage />} />
        </Routes>
      </main>
    </div>
  );
}
