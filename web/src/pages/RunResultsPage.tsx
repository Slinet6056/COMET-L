import { useEffect, useMemo, useState } from 'react';
import { Link, useParams } from 'react-router-dom';

import { fetchRunResults, type RunResultsArtifact, type RunResultsResponse } from '../lib/api';

function formatPercent(value: number | null | undefined): string {
  if (typeof value !== 'number' || Number.isNaN(value)) {
    return 'N/A';
  }

  return `${(value * 100).toFixed(1)}%`;
}

function formatCount(value: number | null | undefined): string {
  if (typeof value !== 'number' || Number.isNaN(value)) {
    return 'N/A';
  }

  return value.toLocaleString();
}

function formatDate(value: string | null | undefined): string {
  if (!value) {
    return 'Unavailable';
  }

  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }

  return date.toLocaleString();
}

function formatBytes(value: number | null | undefined): string {
  if (typeof value !== 'number' || Number.isNaN(value)) {
    return 'Unavailable';
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

function buildTerminalMessage(results: RunResultsResponse): string {
  if (results.status === 'failed') {
    return 'This run ended in failure. Final-state data and the run log remain available when the backend produced them.';
  }

  if (results.status === 'completed') {
    return 'This run reached a terminal completed state. The summary below reflects the final snapshot and any run-scoped database aggregates.';
  }

  return 'This run has not reached a terminal state yet. The page still shows the latest available result payload.';
}

function buildModeHighlights(results: RunResultsResponse): Array<{ label: string; value: string }> {
  const metrics = results.summary.metrics;

  if (results.mode === 'parallel') {
    return [
      { label: 'Execution mode', value: 'Parallel batch evolution' },
      { label: 'Global mutation score', value: formatPercent(metrics.globalMutationScore) },
      { label: 'Global killed mutants', value: formatCount(metrics.globalKilledMutants) },
      { label: 'Global survived mutants', value: formatCount(metrics.globalSurvivedMutants) },
    ];
  }

  return [
    { label: 'Execution mode', value: 'Standard single-target evolution' },
    { label: 'Current method coverage', value: formatPercent(metrics.currentMethodCoverage) },
    { label: 'Killed mutants', value: formatCount(metrics.killedMutants) },
    { label: 'Survived mutants', value: formatCount(metrics.survivedMutants) },
  ];
}

function ArtifactCard(props: { title: string; artifact?: RunResultsArtifact }) {
  const { title, artifact } = props;

  if (!artifact) {
    return (
      <article className="artifact-card">
        <h4>{title}</h4>
        <p className="muted-copy">Artifact metadata is unavailable.</p>
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
        <span className={artifact.exists ? 'worker-pill worker-pill--success' : 'worker-pill worker-pill--error'}>
          {artifact.exists ? 'Available' : 'Missing'}
        </span>
      </div>

      <dl className="detail-grid artifact-card__details">
        <div>
          <dt>Updated</dt>
          <dd>{formatDate(artifact.updatedAt)}</dd>
        </div>
        <div>
          <dt>Size</dt>
          <dd>{formatBytes(artifact.sizeBytes)}</dd>
        </div>
      </dl>

      {artifact.exists ? (
        <a className="artifact-link" href={artifact.downloadUrl}>
          Download {artifact.filename}
        </a>
      ) : (
        <p className="muted-copy">This artifact was not generated for the run.</p>
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

        setPageError(error instanceof Error ? error.message : 'Unable to load run results.');
        setIsLoading(false);
      }
    }

    void loadResults();

    return () => {
      active = false;
    };
  }, [runId]);

  const modeHighlights = useMemo(() => (results ? buildModeHighlights(results) : []), [results]);

  if (isLoading) {
    return (
      <section className="panel run-page">
        <p className="eyebrow">Results</p>
        <h2>Run Results</h2>
        <p>Loading final summary for <code>{runId}</code>...</p>
      </section>
    );
  }

  if (pageError || results === null) {
    return (
      <section className="panel run-page">
        <p className="eyebrow">Results</p>
        <h2>Run Results</h2>
        <p role="alert">{pageError ?? 'Run results are unavailable.'}</p>
        <Link to={`/runs/${runId}`}>Back to run details</Link>
      </section>
    );
  }

  return (
    <section className="panel run-page results-page">
      <div className="run-page__hero">
        <div>
          <p className="eyebrow">Results</p>
          <h2>Run Results</h2>
          <p className="run-page__lead">
            Terminal summary for <code>{results.runId}</code>. This page combines the final
            run snapshot, database-backed aggregates, and downloadable artifacts.
          </p>
        </div>

        <div className="run-status-badges">
          <span className="run-badge">Status: {results.status}</span>
          <span className="run-badge">Phase: {results.phase.label}</span>
          <span className="run-badge">Mode: {results.mode}</span>
          <span className="run-badge">Iteration: {results.iteration}</span>
        </div>
      </div>

      <section
        className={`feedback-banner ${results.status === 'failed' ? 'feedback-banner--error' : 'feedback-banner--success'}`}
        aria-live="polite"
      >
        {buildTerminalMessage(results)}
      </section>

      <div className="run-layout">
        <div className="run-layout__main">
          <section className="run-card" aria-labelledby="results-final-stats">
            <p className="eyebrow">Final Stats</p>
            <h3 id="results-final-stats">Final Statistics</h3>
            <div className="metric-grid">
              <article>
                <span>Mutation score</span>
                <strong>{formatPercent(results.summary.metrics.mutationScore)}</strong>
              </article>
              <article>
                <span>Line coverage</span>
                <strong>{formatPercent(results.summary.metrics.lineCoverage)}</strong>
              </article>
              <article>
                <span>Branch coverage</span>
                <strong>{formatPercent(results.summary.metrics.branchCoverage)}</strong>
              </article>
              <article>
                <span>Total tests</span>
                <strong>{formatCount(results.summary.metrics.totalTests)}</strong>
              </article>
              <article>
                <span>Total mutants</span>
                <strong>{formatCount(results.summary.metrics.totalMutants)}</strong>
              </article>
              <article>
                <span>LLM calls</span>
                <strong>
                  {formatCount(results.llmCalls)} / {formatCount(results.budget)}
                </strong>
              </article>
            </div>
          </section>

          <section className="run-card" aria-labelledby="results-artifacts">
            <p className="eyebrow">Artifacts</p>
            <h3 id="results-artifacts">Artifact Downloads</h3>
            <div className="artifact-grid">
              <ArtifactCard title="Final State JSON" artifact={results.artifacts.finalState} />
              <ArtifactCard title="Run Log" artifact={results.artifacts.runLog} />
            </div>
          </section>
        </div>

        <aside className="run-layout__sidebar">
          <section className="run-card" aria-labelledby="results-mode-summary">
            <p className="eyebrow">Mode</p>
            <h3 id="results-mode-summary">Mode Summary</h3>
            <ul className="summary-list summary-list--compact">
              {modeHighlights.map((entry) => (
                <li key={entry.label}>
                  <strong>{entry.label}</strong>
                  <span>{entry.value}</span>
                </li>
              ))}
            </ul>
          </section>

          <section className="run-card" aria-labelledby="results-test-summary">
            <p className="eyebrow">Database</p>
            <h3 id="results-test-summary">Test and Mutant Summary</h3>
            <ul className="summary-list summary-list--compact">
              <li>
                <strong>Compiled cases</strong>
                <span>
                  {formatCount(results.summary.tests.compiledCases)} / {formatCount(results.summary.tests.totalCases)}
                </span>
              </li>
              <li>
                <strong>Generated methods</strong>
                <span>{formatCount(results.summary.tests.totalMethods)}</span>
              </li>
              <li>
                <strong>Target methods hit</strong>
                <span>{formatCount(results.summary.tests.targetMethods)}</span>
              </li>
              <li>
                <strong>Evaluated mutants</strong>
                <span>{formatCount(results.summary.mutants.evaluated)}</span>
              </li>
              <li>
                <strong>Pending mutants</strong>
                <span>{formatCount(results.summary.mutants.pending)}</span>
              </li>
              <li>
                <strong>Invalid mutants</strong>
                <span>{formatCount(results.summary.mutants.invalid)}</span>
              </li>
            </ul>
          </section>

          <section className="run-card" aria-labelledby="results-coverage-summary">
            <p className="eyebrow">Coverage</p>
            <h3 id="results-coverage-summary">Coverage Summary</h3>
            <ul className="summary-list summary-list--compact">
              <li>
                <strong>Latest DB iteration</strong>
                <span>{formatCount(results.summary.coverage.latestIteration)}</span>
              </li>
              <li>
                <strong>Tracked methods</strong>
                <span>{formatCount(results.summary.coverage.methodsTracked)}</span>
              </li>
              <li>
                <strong>Average line coverage</strong>
                <span>{formatPercent(results.summary.coverage.averageLineCoverage)}</span>
              </li>
              <li>
                <strong>Average branch coverage</strong>
                <span>{formatPercent(results.summary.coverage.averageBranchCoverage)}</span>
              </li>
              <li>
                <strong>Final state source</strong>
                <span>{results.summary.sources.finalState ? 'Available' : 'Missing'}</span>
              </li>
              <li>
                <strong>Run log source</strong>
                <span>{results.summary.sources.runLog ? 'Available' : 'Missing'}</span>
              </li>
            </ul>
          </section>

          <Link to={`/runs/${runId}`}>Back to run details</Link>
        </aside>
      </div>
    </section>
  );
}
