export type ApiFieldError = {
  path: Array<string | number>;
  code: string;
  message: string;
};

export type ApiErrorPayload = {
  error: {
    code: string;
    message: string;
    fieldErrors: ApiFieldError[];
  };
};

export type ConfigPayload = {
  config: Record<string, unknown>;
};

export type RunCreateResponse = {
  runId: string;
  status: string;
  mode: string;
};

export type RunPhase = {
  key: string;
  label: string;
  createdAt?: string | null;
  startedAt?: string | null;
  completedAt?: string | null;
  failedAt?: string | null;
};

export type RunMetrics = {
  mutationScore: number;
  globalMutationScore: number;
  lineCoverage: number;
  branchCoverage: number;
  totalTests: number;
  totalMutants: number;
  globalTotalMutants: number;
  killedMutants: number;
  globalKilledMutants: number;
  survivedMutants: number;
  globalSurvivedMutants: number;
  currentMethodCoverage?: number | null;
};

export type RunArtifact = {
  exists: boolean;
  downloadUrl?: string;
};

export type RunWorkerCard = {
  targetId: string;
  className: string;
  methodName: string;
  success: boolean;
  error?: string | null;
  testsGenerated: number;
  mutantsGenerated: number;
  mutantsEvaluated: number;
  mutantsKilled: number;
  localMutationScore: number;
  processingTime: number;
};

export type RunActiveTarget = Record<string, unknown> & {
  targetId?: string;
  target_id?: string;
  className?: string;
  class_name?: string;
  methodName?: string;
  method_name?: string;
  started_at?: string;
};

export type RunLogStreamsSummary = {
  taskIds: string[];
  counts: Record<string, number>;
  maxEntriesPerStream: number;
  items: RunLogStream[];
  byTaskId: Record<string, RunLogStream>;
};

export type RunLogStream = {
  taskId: string;
  order: number;
  status: string;
  startedAt?: string | null;
  completedAt?: string | null;
  failedAt?: string | null;
  endedAt?: string | null;
  durationSeconds?: number | null;
  firstEntryAt?: string | null;
  lastEntryAt?: string | null;
  bufferedEntryCount: number;
  totalEntryCount: number;
};

export type RunLogEntry = {
  sequence: number;
  timestamp: string;
  taskId: string;
  logger: string;
  level: string;
  message: string;
};

export type RunLogsSummaryResponse = {
  runId: string;
  streams: RunLogStreamsSummary;
};

export type RunLogsStreamResponse = {
  runId: string;
  taskId: string;
  availableTaskIds: string[];
  maxEntriesPerStream: number;
  stream?: RunLogStream | null;
  entries: RunLogEntry[];
};

export type RunResultsArtifact = {
  exists: boolean;
  filename: string;
  contentType: string;
  sizeBytes?: number | null;
  updatedAt?: string | null;
  downloadUrl: string;
};

export type RunResultsSources = {
  finalState: boolean;
  database: boolean;
  runLog: boolean;
};

export type RunResultsTestsSummary = {
  totalCases: number;
  compiledCases: number;
  totalMethods: number;
  targetMethods: number;
};

export type RunResultsMutantsSummary = {
  total: number;
  evaluated: number;
  killed: number;
  survived: number;
  pending: number;
  valid: number;
  invalid: number;
  outdated: number;
};

export type RunResultsCoverageSummary = {
  latestIteration?: number | null;
  methodsTracked: number;
  averageLineCoverage?: number | null;
  averageBranchCoverage?: number | null;
};

export type RunResultsSummary = {
  metrics: RunMetrics;
  tests: RunResultsTestsSummary;
  mutants: RunResultsMutantsSummary;
  coverage: RunResultsCoverageSummary;
  sources: RunResultsSources;
};

export type RunResultsResponse = {
  runId: string;
  status: string;
  mode: string;
  iteration: number;
  llmCalls: number;
  budget: number;
  phase: RunPhase;
  summary: RunResultsSummary;
  artifacts: Record<string, RunResultsArtifact>;
};

export type RunSnapshot = {
  runId: string;
  status: string;
  mode: string;
  iteration: number;
  llmCalls: number;
  budget: number;
  decisionReasoning?: string | null;
  currentTarget?: Record<string, unknown> | null;
  previousTarget?: Record<string, unknown> | null;
  recentImprovements: Array<Record<string, unknown>>;
  improvementSummary: Record<string, unknown>;
  metrics: RunMetrics;
  phase: RunPhase;
  artifacts: Record<string, RunArtifact>;
  parallel?: {
    currentBatch: number;
    parallelStats: Record<string, unknown>;
    activeTargets: RunActiveTarget[];
    workerCards: RunWorkerCard[];
    batchResults: Array<Array<Record<string, unknown>>>;
  };
  currentBatch?: number;
  parallelStats?: Record<string, unknown>;
  activeTargets?: RunActiveTarget[];
  workerCards?: RunWorkerCard[];
  batchResults?: Array<Array<Record<string, unknown>>>;
  logStreams?: RunLogStreamsSummary;
};

export type RunEvent = {
  sequence?: number;
  timestamp?: string;
  type: string;
  runId?: string;
  status?: string;
  mode?: string;
  snapshot?: RunSnapshot;
  phase?: Partial<RunPhase>;
  iteration?: number;
  llmCalls?: number;
  budget?: number;
  decisionReasoning?: string | null;
  currentTarget?: Record<string, unknown> | null;
  previousTarget?: Record<string, unknown> | null;
  recentImprovements?: Array<Record<string, unknown>>;
  improvementSummary?: Record<string, unknown>;
  metrics?: Partial<RunMetrics>;
  currentBatch?: number;
  parallelStats?: Record<string, unknown>;
  activeTargets?: RunActiveTarget[];
  workerCards?: RunWorkerCard[];
  batchResults?: Array<Array<Record<string, unknown>>>;
  error?: string;
};

export class ApiError extends Error {
  status: number;
  code: string;
  fieldErrors: ApiFieldError[];

  constructor(status: number, payload: ApiErrorPayload) {
    super(payload.error.message);
    this.name = 'ApiError';
    this.status = status;
    this.code = payload.error.code;
    this.fieldErrors = payload.error.fieldErrors;
  }
}

async function parseJsonResponse<T>(response: Response): Promise<T> {
  const payload = (await response.json()) as T | ApiErrorPayload;

  if (!response.ok) {
    throw new ApiError(response.status, payload as ApiErrorPayload);
  }

  return payload as T;
}

export async function fetchConfigDefaults(): Promise<ConfigPayload> {
  const response = await fetch('/api/config/defaults');
  return parseJsonResponse<ConfigPayload>(response);
}

export async function parseConfigFile(file: File): Promise<ConfigPayload> {
  const formData = new FormData();
  formData.set('file', file);

  const response = await fetch('/api/config/parse', {
    method: 'POST',
    body: formData,
  });

  return parseJsonResponse<ConfigPayload>(response);
}

export async function createRun(options: {
  projectPath: string;
  bugReportsDir?: string | null;
  config: Record<string, unknown>;
}): Promise<RunCreateResponse> {
  const formData = new FormData();
  formData.set('projectPath', options.projectPath);
  if (options.bugReportsDir && options.bugReportsDir.trim().length > 0) {
    formData.set('bugReportsDir', options.bugReportsDir.trim());
  }
  formData.set(
    'configFile',
    new File([JSON.stringify(options.config, null, 2)], 'web-config.yaml', {
      type: 'application/x-yaml',
    }),
  );

  const response = await fetch('/api/runs', {
    method: 'POST',
    body: formData,
  });

  return parseJsonResponse<RunCreateResponse>(response);
}

export async function fetchRunSnapshot(runId: string): Promise<RunSnapshot> {
  const response = await fetch(`/api/runs/${runId}`);
  return parseJsonResponse<RunSnapshot>(response);
}

export async function fetchRunResults(runId: string): Promise<RunResultsResponse> {
  const response = await fetch(`/api/runs/${runId}/results`);
  return parseJsonResponse<RunResultsResponse>(response);
}

export async function fetchRunLogs(runId: string): Promise<RunLogsSummaryResponse> {
  const response = await fetch(`/api/runs/${runId}/logs`);
  return parseJsonResponse<RunLogsSummaryResponse>(response);
}

export async function fetchRunLogsForTask(
  runId: string,
  taskId: string,
): Promise<RunLogsStreamResponse> {
  const response = await fetch(`/api/runs/${runId}/logs/${taskId}`);
  return parseJsonResponse<RunLogsStreamResponse>(response);
}

type RunEventsSubscription = {
  onEvent: (event: RunEvent) => void;
  onError?: () => void;
};

export function subscribeToRunEvents(
  runId: string,
  handlers: RunEventsSubscription,
): () => void {
  const eventSource = new EventSource(`/api/runs/${runId}/events`);
  const listener = (message: MessageEvent<string>) => {
    handlers.onEvent(JSON.parse(message.data) as RunEvent);
  };

  ['run.snapshot', 'run.started', 'run.phase', 'run.completed', 'run.failed'].forEach(
    (eventName) => {
      eventSource.addEventListener(eventName, listener as EventListener);
    },
  );

  eventSource.onerror = () => {
    handlers.onError?.();
  };

  return () => {
    eventSource.close();
  };
}
