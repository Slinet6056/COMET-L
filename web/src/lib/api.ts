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
  configPolicy?: ConfigPolicy;
};

export type ConfigPolicy = {
  overriddenFields?: string[];
  clampedFields?: string[];
  redactedFields?: string[];
};

export type RunConfigPayload = Record<string, unknown> & {
  evolution?: {
    mutation_enabled?: boolean;
  };
};

export type UploadResponse = {
  uploadId: string;
  kind: 'project' | 'bug_reports';
  status: string;
  originalFilename: string;
  extractedRoot: string;
};

export type RunCreateResponse = {
  runId: string;
  status: string;
  mode: string;
  queuePosition?: number | null;
  effectiveConfig?: Record<string, unknown>;
  configPolicy?: Record<string, unknown>;
  uploadSource?: {
    projectUploadId?: string | null;
    bugReportsUploadId?: string | null;
  } | null;
};

export type RunHistoryEntry = {
  runId: string;
  status: string;
  mode: string;
  projectSourceType?: 'local' | 'upload' | 'github' | string;
  projectPath: string;
  configPath: string;
  queuePosition?: number | null;
  createdAt: string;
  startedAt?: string | null;
  completedAt?: string | null;
  failedAt?: string | null;
  error?: string | null;
  iteration: number;
  llmCalls: number;
  budget: number;
  phase: RunPhase;
  metrics: RunMetrics;
  artifacts: Record<string, RunArtifact>;
  isHistorical?: boolean;
  mutationEnabled?: boolean | null;
};

export type RunHistoryResponse = {
  items: RunHistoryEntry[];
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
  mutationScore: number | null;
  globalMutationScore: number | null;
  lineCoverage: number;
  branchCoverage: number;
  totalTests: number;
  totalMutants: number | null;
  globalTotalMutants: number | null;
  killedMutants: number | null;
  globalKilledMutants: number | null;
  survivedMutants: number | null;
  globalSurvivedMutants: number | null;
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
  mutantsGenerated: number | null;
  mutantsEvaluated: number | null;
  mutantsKilled: number | null;
  localMutationScore: number | null;
  processingTime: number;
  methodCoverage?: number | null;
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
  queuePosition?: number | null;
  iteration: number;
  llmCalls: number;
  budget: number;
  phase: RunPhase;
  summary: RunResultsSummary;
  artifacts: Record<string, RunResultsArtifact>;
  pullRequestUrl?: string | null;
  pullRequestError?: string | null;
  reportArtifact?: RunResultsArtifact;
  selectedJavaVersion?: string | null;
  mutationEnabled?: boolean | null;
};

export type RunSnapshot = {
  runId: string;
  status: string;
  mode: string;
  queuePosition?: number | null;
  selectedJavaVersion?: string | null;
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
  isHistorical?: boolean;
  mutationEnabled?: boolean | null;
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

export const AUTH_EXPIRED_EVENT = 'comet:auth-expired';

export const SAFE_RUN_NOT_FOUND_MESSAGE = '任务不存在或无权访问';
export const SESSION_EXPIRED_MESSAGE = '登录状态已过期，请重新登录。';

export const RUN_STATUS_LABELS: Record<string, string> = {
  created: '已创建',
  queued: '排队中',
  preprocessing: '预处理中',
  pending: '等待中',
  starting: '启动中',
  running: '运行中',
  cancelling: '取消中',
  cancelled: '已取消',
  failed: '失败',
  completed: '已完成',
  succeeded: '已完成',
  stale: '已失效',
};

export function translateRunStatus(value: string | null | undefined): string {
  if (!value) {
    return '未知';
  }

  return RUN_STATUS_LABELS[value] ?? value;
}

export function isTerminalRunStatus(value: string | null | undefined): boolean {
  return (
    value === 'completed' ||
    value === 'succeeded' ||
    value === 'failed' ||
    value === 'cancelled' ||
    value === 'stale'
  );
}

export function getSafeApiErrorMessage(error: unknown, fallback: string): string {
  if (error instanceof ApiError) {
    if (error.status === 404) {
      return SAFE_RUN_NOT_FOUND_MESSAGE;
    }

    if (error.status === 401) {
      return SESSION_EXPIRED_MESSAGE;
    }

    return error.message;
  }

  return error instanceof Error ? error.message : fallback;
}

function notifyAuthExpired() {
  if (typeof window === 'undefined') {
    return;
  }

  window.dispatchEvent(new CustomEvent(AUTH_EXPIRED_EVENT));
}

async function parseJsonResponse<T>(
  response: Response,
  options: { notifyAuthExpired?: boolean } = {},
): Promise<T> {
  const payload = (await response.json()) as T | ApiErrorPayload;

  if (!response.ok) {
    if (response.status === 401 && options.notifyAuthExpired !== false) {
      notifyAuthExpired();
    }

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

export async function uploadProjectZip(file: File): Promise<UploadResponse> {
  const formData = new FormData();
  formData.set('file', file);

  const response = await fetch('/api/uploads/project', {
    method: 'POST',
    body: formData,
  });

  return parseJsonResponse<UploadResponse>(response);
}

export async function uploadBugReportsZip(file: File): Promise<UploadResponse> {
  const formData = new FormData();
  formData.set('file', file);

  const response = await fetch('/api/uploads/bug-reports', {
    method: 'POST',
    body: formData,
  });

  return parseJsonResponse<UploadResponse>(response);
}

export async function createRun(options: {
  projectPath?: string;
  bugReportsDir?: string | null;
  githubRepoUrl?: string | null;
  githubBaseBranch?: string | null;
  selectedJavaVersion?: string | null;
  projectUploadId?: string | null;
  bugReportsUploadId?: string | null;
  config: RunConfigPayload;
}): Promise<RunCreateResponse> {
  const formData = new FormData();
  if (options.projectPath && options.projectPath.trim().length > 0) {
    formData.set('projectPath', options.projectPath);
  }
  if (options.bugReportsDir && options.bugReportsDir.trim().length > 0) {
    formData.set('bugReportsDir', options.bugReportsDir.trim());
  }
  if (options.githubRepoUrl && options.githubRepoUrl.trim().length > 0) {
    formData.set('githubRepoUrl', options.githubRepoUrl.trim());
  }
  if (options.githubBaseBranch && options.githubBaseBranch.trim().length > 0) {
    formData.set('githubBaseBranch', options.githubBaseBranch.trim());
  }
  if (options.selectedJavaVersion && options.selectedJavaVersion.trim().length > 0) {
    formData.set('selectedJavaVersion', options.selectedJavaVersion.trim());
  }
  if (options.projectUploadId && options.projectUploadId.trim().length > 0) {
    formData.set('projectUploadId', options.projectUploadId.trim());
  }
  if (options.bugReportsUploadId && options.bugReportsUploadId.trim().length > 0) {
    formData.set('bugReportsUploadId', options.bugReportsUploadId.trim());
  }
  if (typeof options.config.evolution?.mutation_enabled === 'boolean') {
    formData.set('mutationEnabled', String(options.config.evolution.mutation_enabled));
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

export async function fetchRunHistory(): Promise<RunHistoryResponse> {
  const response = await fetch('/api/runs/history');
  return parseJsonResponse<RunHistoryResponse>(response);
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
  const response = await fetch(`/api/runs/${runId}/logs/${encodeURIComponent(taskId)}`);
  return parseJsonResponse<RunLogsStreamResponse>(response);
}

type RunEventsSubscription = {
  onEvent: (event: RunEvent) => void;
  onError?: () => void;
};

export function subscribeToRunEvents(runId: string, handlers: RunEventsSubscription): () => void {
  const eventSource = new EventSource(`/api/runs/${runId}/events`);
  const listener = (message: MessageEvent<string>) => {
    try {
      handlers.onEvent(JSON.parse(message.data) as RunEvent);
    } catch {
      handlers.onError?.();
    }
  };

  [
    'run.snapshot',
    'run.started',
    'run.phase',
    'run.completed',
    'run.failed',
    'run.cancelled',
    'run.stale',
  ].forEach((eventName) => {
    eventSource.addEventListener(eventName, listener as EventListener);
  });

  eventSource.onerror = () => {
    handlers.onError?.();
  };

  return () => {
    eventSource.close();
  };
}

export type GitHubAuthStatus = {
  connected: boolean;
  username?: string | null;
  requiresReauth?: boolean;
  message?: string;
};

export type GitHubAuthConnectUrlResponse = {
  connectUrl: string;
};

export type GitHubAuthCallbackResponse = {
  provider: string;
  connected: boolean;
  requiresReauth: boolean;
  message: string;
};

export async function fetchGitHubAuthStatus(): Promise<GitHubAuthStatus> {
  const response = await fetch('/api/github/auth/status');
  return parseJsonResponse<GitHubAuthStatus>(response);
}

export async function fetchGitHubAuthConnectUrl(): Promise<GitHubAuthConnectUrlResponse> {
  const response = await fetch('/api/github/auth/connect-url');
  return parseJsonResponse<GitHubAuthConnectUrlResponse>(response);
}

export async function handleGitHubAuthCallback(
  code: string,
  state: string,
): Promise<GitHubAuthCallbackResponse> {
  const response = await fetch(
    `/api/github/auth/callback?code=${encodeURIComponent(code)}&state=${encodeURIComponent(state)}`,
  );
  return parseJsonResponse<GitHubAuthCallbackResponse>(response);
}

export async function disconnectGitHubAuth(): Promise<void> {
  const response = await fetch('/api/github/auth/disconnect', {
    method: 'POST',
  });
  if (!response.ok) {
    const payload = (await response.json()) as ApiErrorPayload;
    throw new ApiError(response.status, payload);
  }
}

export type GitHubRepository = {
  name: string;
  fullName: string;
  url: string;
  description?: string | null;
  private: boolean;
  updatedAt?: string | null;
};

export type GitHubRepositoriesResponse = {
  repositories: GitHubRepository[];
};

export async function fetchGitHubRepositories(): Promise<GitHubRepositoriesResponse> {
  const response = await fetch('/api/github/repositories');
  return parseJsonResponse<GitHubRepositoriesResponse>(response);
}

export type AuthUser = {
  id: number;
  username: string;
  role: 'admin' | 'user';
};

export type AuthResponse = {
  user: AuthUser;
};

export async function login(username: string, password: string): Promise<AuthResponse> {
  const response = await fetch('/api/auth/login', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ username, password }),
  });
  return parseJsonResponse<AuthResponse>(response);
}

export async function logout(): Promise<void> {
  const response = await fetch('/api/auth/logout', {
    method: 'POST',
  });
  if (!response.ok) {
    const payload = (await response.json()) as ApiErrorPayload;
    throw new ApiError(response.status, payload);
  }
}

export async function getCurrentUser(): Promise<AuthResponse> {
  const response = await fetch('/api/auth/me');
  return parseJsonResponse<AuthResponse>(response, { notifyAuthExpired: false });
}
