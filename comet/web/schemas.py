from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class FieldError(BaseModel):
    path: list[str | int] = Field(default_factory=list)
    code: str
    message: str


class ApiError(BaseModel):
    code: str
    message: str
    fieldErrors: list[FieldError] = Field(default_factory=list)


class ErrorResponse(BaseModel):
    error: ApiError


class HealthResponse(BaseModel):
    status: str
    activeRunId: str | None = Field(default=None)


class ConfigPayload(BaseModel):
    config: dict[str, object]


class ConfigParseResponse(ConfigPayload):
    pass


class RunCreateResponse(BaseModel):
    runId: str
    status: str
    mode: str


class RunRequestPayload(BaseModel):
    projectPath: str
    githubRepoUrl: str | None = None
    githubBaseBranch: str | None = None
    selectedJavaVersion: str | None = None


class GitHubRepositoryEntry(BaseModel):
    name: str
    fullName: str
    url: str
    description: str | None = None
    private: bool
    updatedAt: str | None = None


class GitHubRepositoriesResponse(BaseModel):
    repositories: list[GitHubRepositoryEntry] = Field(default_factory=list)


class ArtifactSummary(BaseModel):
    exists: bool
    downloadUrl: str | None = None


class RunHistoryArtifactSummary(ArtifactSummary):
    pass


class RunPhase(BaseModel):
    key: str
    label: str
    createdAt: str | None = None
    startedAt: str | None = None
    completedAt: str | None = None
    failedAt: str | None = None


class RunMetrics(BaseModel):
    mutationScore: float | None = None
    globalMutationScore: float | None = None
    lineCoverage: float
    branchCoverage: float
    totalTests: int
    totalMutants: int | None = None
    globalTotalMutants: int | None = None
    killedMutants: int | None = None
    globalKilledMutants: int | None = None
    survivedMutants: int | None = None
    globalSurvivedMutants: int | None = None
    currentMethodCoverage: float | None = None


class RunSnapshotResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    runId: str
    status: str
    mode: str
    selectedJavaVersion: str | None = None
    mutationEnabled: bool | None = None
    iteration: int
    llmCalls: int
    budget: int
    decisionReasoning: str | None = None
    currentTarget: dict[str, object] | None = None
    previousTarget: dict[str, object] | None = None
    recentImprovements: list[dict[str, object]] = Field(default_factory=list)
    improvementSummary: dict[str, object] = Field(default_factory=dict)
    metrics: RunMetrics
    phase: RunPhase
    artifacts: dict[str, ArtifactSummary]
    isHistorical: bool = False


class RunHistoryEntry(BaseModel):
    runId: str
    status: str
    mode: str
    selectedJavaVersion: str | None = None
    mutationEnabled: bool | None = None
    projectPath: str
    configPath: str
    createdAt: str
    startedAt: str | None = None
    completedAt: str | None = None
    failedAt: str | None = None
    error: str | None = None
    iteration: int
    llmCalls: int
    budget: int
    phase: RunPhase
    metrics: RunMetrics
    artifacts: dict[str, RunHistoryArtifactSummary]
    isHistorical: bool = False


class RunHistoryResponse(BaseModel):
    items: list[RunHistoryEntry] = Field(default_factory=list)


class RunResultsArtifact(BaseModel):
    exists: bool
    filename: str
    contentType: str
    sizeBytes: int | None = None
    updatedAt: str | None = None
    downloadUrl: str


class RunResultsSources(BaseModel):
    finalState: bool
    database: bool
    runLog: bool


class RunResultsTestsSummary(BaseModel):
    totalCases: int = 0
    compiledCases: int = 0
    totalMethods: int = 0
    targetMethods: int = 0


class RunResultsMutantsSummary(BaseModel):
    total: int = 0
    evaluated: int = 0
    killed: int = 0
    survived: int = 0
    pending: int = 0
    valid: int = 0
    invalid: int = 0
    outdated: int = 0


class RunResultsCoverageSummary(BaseModel):
    latestIteration: int | None = None
    methodsTracked: int = 0
    averageLineCoverage: float | None = None
    averageBranchCoverage: float | None = None


class RunResultsSummary(BaseModel):
    metrics: RunMetrics
    tests: RunResultsTestsSummary
    mutants: RunResultsMutantsSummary
    coverage: RunResultsCoverageSummary
    sources: RunResultsSources


class RunResultsResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    runId: str
    status: str
    mode: str
    selectedJavaVersion: str | None = None
    mutationEnabled: bool | None = None
    iteration: int
    llmCalls: int
    budget: int
    phase: RunPhase
    summary: RunResultsSummary
    artifacts: dict[str, RunResultsArtifact]
    pullRequestUrl: str | None = None
    pullRequestError: str | None = None
    reportArtifact: RunResultsArtifact
