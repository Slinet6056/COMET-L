import { ChangeEvent, useEffect, useMemo, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';

import { Alert, AlertDescription } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import {
  ApiError,
  createRun,
  disconnectGitHubAuth,
  fetchConfigDefaults,
  fetchGitHubAuthConnectUrl,
  fetchGitHubAuthStatus,
  fetchGitHubRepositories,
  handleGitHubAuthCallback,
  parseConfigFile,
  uploadBugReportsZip,
  uploadProjectZip,
  type AuthUser,
  type ConfigPolicy,
  type GitHubAuthStatus,
  type GitHubRepository,
} from '../lib/api';
import { CONFIG_SECTIONS, EXAMPLE_PROJECTS, type ConfigFieldDefinition } from './configFields';

type ConfigValue = Record<string, unknown>;
type DeploymentConfig = Record<string, unknown>;
type FieldErrors = Record<string, string>;

function getFieldKey(path: string[]): string {
  return path.join('.');
}

function getNestedValue(config: ConfigValue, path: string[]): unknown {
  return path.reduce<unknown>((current, key) => {
    if (current === null || typeof current !== 'object' || Array.isArray(current)) {
      return undefined;
    }

    return (current as Record<string, unknown>)[key];
  }, config);
}

function setNestedValue(config: ConfigValue, path: string[], value: unknown): ConfigValue {
  const nextConfig: ConfigValue = structuredClone(config);
  let current: Record<string, unknown> = nextConfig;

  path.slice(0, -1).forEach((segment) => {
    const existing = current[segment];
    if (existing === null || typeof existing !== 'object' || Array.isArray(existing)) {
      current[segment] = {};
    }
    current = current[segment] as Record<string, unknown>;
  });

  current[path[path.length - 1]] = value;
  return nextConfig;
}

function valueToInputString(value: unknown): string {
  if (value === null || value === undefined) {
    return '';
  }

  return String(value);
}

function parseFieldValue(
  field: ConfigFieldDefinition,
  rawValue: string,
  checked: boolean,
): unknown {
  if (field.kind === 'boolean') {
    return checked;
  }

  if (field.kind === 'nullable-boolean') {
    if (rawValue === '') {
      return null;
    }
    return rawValue === 'true';
  }

  if (field.kind === 'number') {
    if (rawValue.trim() === '') {
      return null;
    }
    return rawValue.includes('.') ? Number.parseFloat(rawValue) : Number.parseInt(rawValue, 10);
  }

  if (rawValue.trim() === '') {
    return null;
  }

  return rawValue;
}

function buildFieldErrors(error: ApiError): FieldErrors {
  return error.fieldErrors.reduce<FieldErrors>((accumulator, fieldError) => {
    if (fieldError.path.length > 0) {
      accumulator[fieldError.path.join('.')] = fieldError.message;
    }
    return accumulator;
  }, {});
}

function getFieldHintId(path: string[]): string {
  return `field-hint-${getFieldKey(path).replaceAll('.', '-')}`;
}

function getPolicyFields(fields: string[] | undefined): Set<string> {
  return new Set(fields ?? []);
}

function splitServerConfig(config: ConfigValue): {
  userConfig: ConfigValue;
  deploymentConfig: DeploymentConfig | null;
} {
  const userConfig = structuredClone(config);
  const deploymentValue = userConfig.deployment;
  delete userConfig.deployment;

  return {
    userConfig,
    deploymentConfig:
      deploymentValue !== null &&
      typeof deploymentValue === 'object' &&
      !Array.isArray(deploymentValue)
        ? (deploymentValue as DeploymentConfig)
        : null,
  };
}

function sanitizeRedactedConfigValues(
  config: ConfigValue,
  configPolicy: ConfigPolicy | null,
): ConfigValue {
  if (!configPolicy?.redactedFields?.length) {
    return config;
  }

  return configPolicy.redactedFields.reduce((currentConfig, fieldKey) => {
    const path = fieldKey.split('.');
    const value = getNestedValue(currentConfig, path);
    if (value !== '[REDACTED]') {
      return currentConfig;
    }

    return setNestedValue(currentConfig, path, null);
  }, config);
}

function isLocalPathModeEnabled(deploymentConfig: DeploymentConfig | null): boolean {
  return deploymentConfig?.allow_local_path_mode === true;
}

function getFieldPolicyNotes(fieldKey: string, configPolicy: ConfigPolicy | null): string[] {
  if (!configPolicy) {
    return [];
  }

  const notes: string[] = [];
  if (getPolicyFields(configPolicy.overriddenFields).has(fieldKey)) {
    notes.push('此字段由服务端固定，提交时会使用后端值。');
  }
  if (getPolicyFields(configPolicy.clampedFields).has(fieldKey)) {
    notes.push('超过部署上限时会由服务端自动收紧。');
  }
  return notes;
}

type SourceMode = 'upload' | 'local' | 'github';

const JAVA_VERSION_OPTIONS = ['8', '11', '17', '21', '25'];

function JavaVersionSelect({
  selectedJavaVersion,
  fieldError,
  disabled = false,
  onChange,
}: {
  selectedJavaVersion: string;
  fieldError?: string;
  disabled?: boolean;
  onChange: (version: string) => void;
}) {
  return (
    <div className="space-y-1.5">
      <Label htmlFor="selected-java-version" className="text-xs">
        目标 Java 版本
      </Label>
      <select
        id="selected-java-version"
        data-testid="java-version-select"
        aria-label="目标 Java 版本"
        value={selectedJavaVersion}
        onChange={(e) => onChange(e.target.value)}
        disabled={disabled}
        className="h-8 w-full text-sm rounded-lg border border-input bg-transparent px-2.5 focus-visible:border-ring focus-visible:ring-2 focus-visible:ring-ring/50 disabled:cursor-not-allowed disabled:opacity-50"
      >
        <option value="">请选择版本</option>
        {JAVA_VERSION_OPTIONS.map((version) => (
          <option key={version} value={version}>
            Java {version}
          </option>
        ))}
      </select>
      <p className="text-xs text-muted-foreground">
        选择目标项目使用的 Java 版本，后端会映射到服务端固定的 JDK 路径。
      </p>
      {fieldError ? (
        <p className="text-xs text-destructive" role="alert">
          {fieldError}
        </p>
      ) : null}
    </div>
  );
}

export function HomePage({ user }: { user: AuthUser }) {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const [config, setConfig] = useState<ConfigValue | null>(null);
  const [deploymentConfig, setDeploymentConfig] = useState<DeploymentConfig | null>(null);
  const [configPolicy, setConfigPolicy] = useState<ConfigPolicy | null>(null);
  const [sourceMode, setSourceMode] = useState<SourceMode>('upload');
  const [projectPath, setProjectPath] = useState('');
  const [bugReportsDir, setBugReportsDir] = useState('');
  const [githubRepoUrl, setGithubRepoUrl] = useState('');
  const [githubBaseBranch, setGithubBaseBranch] = useState('');
  const [selectedJavaVersion, setSelectedJavaVersion] = useState('');
  const [githubAuthStatus, setGithubAuthStatus] = useState<GitHubAuthStatus | null>(null);
  const [isConnectingGithub, setIsConnectingGithub] = useState(false);
  const [isDisconnectingGithub, setIsDisconnectingGithub] = useState(false);
  const [githubRepositories, setGithubRepositories] = useState<GitHubRepository[]>([]);
  const [isLoadingRepositories, setIsLoadingRepositories] = useState(false);
  const [repoFilterQuery, setRepoFilterQuery] = useState('');
  const [fieldErrors, setFieldErrors] = useState<FieldErrors>({});
  const [pageError, setPageError] = useState<string | null>(null);
  const [uploadNotice, setUploadNotice] = useState<string | null>(null);
  const [isLoadingDefaults, setIsLoadingDefaults] = useState(true);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [projectUploadId, setProjectUploadId] = useState<string | null>(null);
  const [bugReportsUploadId, setBugReportsUploadId] = useState<string | null>(null);
  const [projectUploadFile, setProjectUploadFile] = useState<File | null>(null);
  const [bugReportsUploadFile, setBugReportsUploadFile] = useState<File | null>(null);
  const [isUploadingProject, setIsUploadingProject] = useState(false);
  const [isUploadingBugReports, setIsUploadingBugReports] = useState(false);
  const allowLocalPathMode = useMemo(
    () => user.role === 'admin' && isLocalPathModeEnabled(deploymentConfig),
    [deploymentConfig, user.role],
  );

  useEffect(() => {
    let active = true;

    async function loadDefaults() {
      try {
        const payload = await fetchConfigDefaults();
        if (!active) {
          return;
        }
        const nextConfig = splitServerConfig(payload.config);
        setConfig(
          sanitizeRedactedConfigValues(nextConfig.userConfig, payload.configPolicy ?? null),
        );
        setDeploymentConfig(nextConfig.deploymentConfig);
        setConfigPolicy(payload.configPolicy ?? null);
      } catch (error) {
        if (!active) {
          return;
        }
        setPageError(error instanceof Error ? error.message : '无法加载默认配置。');
      } finally {
        if (active) {
          setIsLoadingDefaults(false);
        }
      }
    }

    void loadDefaults();

    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    if (sourceMode === 'local' && !allowLocalPathMode) {
      setSourceMode('upload');
    }
  }, [allowLocalPathMode, sourceMode]);

  useEffect(() => {
    let active = true;

    async function loadGithubAuthStatus() {
      try {
        const status = await fetchGitHubAuthStatus();
        if (!active) {
          return;
        }
        setGithubAuthStatus(status);
      } catch {
        if (!active) {
          return;
        }
        setGithubAuthStatus({ connected: false, requiresReauth: true });
      }
    }

    void loadGithubAuthStatus();

    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    let active = true;

    async function loadRepositories() {
      if (!githubAuthStatus?.connected || githubAuthStatus.requiresReauth) {
        setGithubRepositories([]);
        return;
      }

      setIsLoadingRepositories(true);
      try {
        const response = await fetchGitHubRepositories();
        if (!active) {
          return;
        }
        setGithubRepositories(response.repositories);
      } catch {
        if (!active) {
          return;
        }
        setGithubRepositories([]);
      } finally {
        if (active) {
          setIsLoadingRepositories(false);
        }
      }
    }

    void loadRepositories();

    return () => {
      active = false;
    };
  }, [githubAuthStatus]);

  useEffect(() => {
    const githubOAuthResult = searchParams.get('github_oauth');
    const githubOAuthMessage = searchParams.get('message');

    if (githubOAuthResult === 'connected' || githubOAuthResult === 'error') {
      let active = true;

      async function syncGithubAuthResult() {
        setIsConnectingGithub(true);
        setPageError(null);

        try {
          const status = await fetchGitHubAuthStatus();
          if (!active) {
            return;
          }

          setGithubAuthStatus({
            connected: status.connected,
            requiresReauth: status.requiresReauth,
          });

          if (githubOAuthResult === 'error') {
            setPageError(githubOAuthMessage || status.message || 'GitHub 授权失败，请重试。');
          } else if (!status.connected) {
            setPageError(status.message || 'GitHub 授权状态同步失败，请重试。');
          }
        } catch (error) {
          if (!active) {
            return;
          }
          setPageError(error instanceof Error ? error.message : 'GitHub 授权结果同步失败。');
          setGithubAuthStatus({ connected: false, requiresReauth: true });
        } finally {
          if (active) {
            setIsConnectingGithub(false);
            navigate('/', { replace: true });
          }
        }
      }

      void syncGithubAuthResult();

      return () => {
        active = false;
      };
    }

    const code = searchParams.get('code');
    const state = searchParams.get('state');

    if (!code || !state) {
      return;
    }

    let active = true;

    async function completeOAuthCallback() {
      setIsConnectingGithub(true);
      setPageError(null);

      try {
        const result = await handleGitHubAuthCallback(code!, state!);
        if (!active) {
          return;
        }

        if (result.connected) {
          setGithubAuthStatus({ connected: true, requiresReauth: false });
        } else {
          setPageError(result.message || 'GitHub 授权失败，请重试。');
          setGithubAuthStatus({ connected: false, requiresReauth: result.requiresReauth });
        }
      } catch (error) {
        if (!active) {
          return;
        }
        setPageError(error instanceof Error ? error.message : 'GitHub 授权回调失败。');
        setGithubAuthStatus({ connected: false, requiresReauth: true });
      } finally {
        if (active) {
          setIsConnectingGithub(false);
          navigate('/', { replace: true });
        }
      }
    }

    void completeOAuthCallback();

    return () => {
      active = false;
    };
  }, [searchParams, navigate]);

  const groupedSections = useMemo(() => CONFIG_SECTIONS, []);

  async function handleConfigUpload(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file) {
      return;
    }

    setPageError(null);
    setFieldErrors({});
    setUploadNotice(null);

    try {
      const payload = await parseConfigFile(file);
      const nextConfig = splitServerConfig(payload.config);
      setConfig(sanitizeRedactedConfigValues(nextConfig.userConfig, payload.configPolicy ?? null));
      setDeploymentConfig(nextConfig.deploymentConfig);
      setConfigPolicy(payload.configPolicy ?? null);
      setUploadNotice(`${file.name} 已解析并回填到表单中。`);
    } catch (error) {
      if (error instanceof ApiError) {
        setFieldErrors(buildFieldErrors(error));
        setPageError(error.message);
      } else {
        setPageError('无法解析上传的配置文件。');
      }
    } finally {
      event.target.value = '';
    }
  }

  async function handleProjectUpload(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file) {
      return;
    }

    setPageError(null);
    setFieldErrors({});
    setIsUploadingProject(true);

    try {
      const response = await uploadProjectZip(file);
      setProjectUploadId(response.uploadId);
      setProjectUploadFile(file);
      setUploadNotice(`项目 ${file.name} 已上传。`);
    } catch (error) {
      if (error instanceof ApiError) {
        setFieldErrors(buildFieldErrors(error));
        setPageError(error.message);
      } else {
        setPageError('无法上传项目文件。');
      }
      setProjectUploadId(null);
      setProjectUploadFile(null);
    } finally {
      setIsUploadingProject(false);
      event.target.value = '';
    }
  }

  async function handleBugReportsUpload(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file) {
      return;
    }

    setPageError(null);
    setFieldErrors({});
    setIsUploadingBugReports(true);

    try {
      const response = await uploadBugReportsZip(file);
      setBugReportsUploadId(response.uploadId);
      setBugReportsUploadFile(file);
      setUploadNotice(`缺陷报告 ${file.name} 已上传。`);
    } catch (error) {
      if (error instanceof ApiError) {
        setFieldErrors(buildFieldErrors(error));
        setPageError(error.message);
      } else {
        setPageError('无法上传缺陷报告文件。');
      }
      setBugReportsUploadId(null);
      setBugReportsUploadFile(null);
    } finally {
      setIsUploadingBugReports(false);
      event.target.value = '';
    }
  }

  function handleFieldChange(field: ConfigFieldDefinition, rawValue: string, checked: boolean) {
    if (config === null) {
      return;
    }

    const nextValue = parseFieldValue(field, rawValue, checked);
    setConfig((current) =>
      current === null ? current : setNestedValue(current, field.path, nextValue),
    );
    setFieldErrors((current) => {
      const nextErrors = { ...current };
      delete nextErrors[getFieldKey(field.path)];
      return nextErrors;
    });
  }

  async function handleConnectGithub() {
    setIsConnectingGithub(true);
    setPageError(null);

    try {
      const response = await fetchGitHubAuthConnectUrl();
      window.location.href = response.connectUrl;
    } catch (error) {
      setIsConnectingGithub(false);
      setPageError(error instanceof Error ? error.message : '无法获取 GitHub 授权链接。');
    }
  }

  async function handleDisconnectGithub() {
    setIsDisconnectingGithub(true);
    setPageError(null);

    try {
      await disconnectGitHubAuth();
      setGithubAuthStatus({ connected: false, requiresReauth: false });
    } catch (error) {
      setPageError(error instanceof Error ? error.message : '无法断开 GitHub 连接。');
    } finally {
      setIsDisconnectingGithub(false);
    }
  }

  async function handleSubmit() {
    if (config === null || isSubmitting) {
      return;
    }

    if (sourceMode === 'upload') {
      if (!projectUploadId) {
        setFieldErrors({ projectUpload: '请上传项目 ZIP 文件。' });
        return;
      }

      if (!selectedJavaVersion) {
        setFieldErrors({ selectedJavaVersion: '请选择目标 Java 版本。' });
        return;
      }
    }

    if (sourceMode === 'github') {
      if (!githubAuthStatus?.connected || githubAuthStatus.requiresReauth) {
        setPageError('请先连接 GitHub 账户后再使用仓库模式。');
        return;
      }

      if (!githubRepoUrl.trim()) {
        setFieldErrors({ githubRepoUrl: '请输入 GitHub 仓库 URL。' });
        return;
      }

      if (!selectedJavaVersion) {
        setFieldErrors({ selectedJavaVersion: '请选择目标 Java 版本。' });
        return;
      }
    }

    setIsSubmitting(true);
    setFieldErrors({});
    setPageError(null);

    try {
      const response = await createRun({
        projectPath: sourceMode === 'local' ? projectPath : '',
        bugReportsDir: sourceMode === 'local' ? bugReportsDir : null,
        githubRepoUrl: sourceMode === 'github' ? githubRepoUrl : null,
        githubBaseBranch: sourceMode === 'github' ? githubBaseBranch : null,
        selectedJavaVersion:
          sourceMode === 'upload' || sourceMode === 'github' ? selectedJavaVersion || null : null,
        projectUploadId: sourceMode === 'upload' ? projectUploadId : null,
        bugReportsUploadId: sourceMode === 'upload' ? bugReportsUploadId : null,
        config,
      });
      navigate(`/runs/${response.runId}`);
    } catch (error) {
      if (error instanceof ApiError) {
        setFieldErrors(buildFieldErrors(error));
        setPageError(error.message);
      } else {
        setPageError('无法创建运行。');
      }
    } finally {
      setIsSubmitting(false);
    }
  }

  if (isLoadingDefaults) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>运行配置</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground">正在从后端加载默认设置...</p>
        </CardContent>
      </Card>
    );
  }

  if (config === null) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>运行配置</CardTitle>
        </CardHeader>
        <CardContent>
          <p role="alert" className="text-sm text-destructive">
            {pageError ?? '默认配置当前不可用。'}
          </p>
        </CardContent>
      </Card>
    );
  }

  const githubConnected = githubAuthStatus?.connected && !githubAuthStatus.requiresReauth;

  return (
    <div className="space-y-4">
      {/* 顶部：标题 + YAML 上传 */}
      <div className="flex items-start justify-between gap-4">
        <div>
          <p className="text-xs font-mono text-muted-foreground tracking-widest uppercase mb-1">
            配置
          </p>
          <h1 className="text-xl font-semibold">运行配置首页</h1>
          <p className="text-sm text-muted-foreground mt-1">
            上传 YAML 后端规范化并回填表单。你可以继续调整参数，然后用本地路径或 GitHub
            仓库启动运行。
          </p>
        </div>
        <label
          className="flex-shrink-0 cursor-pointer border border-dashed border-border rounded-lg px-4 py-3 text-sm hover:bg-accent transition-colors text-center"
          htmlFor="config-upload"
        >
          <div className="font-medium">上传 YAML</div>
          <div className="text-xs text-muted-foreground mt-0.5">
            使用 <code>/api/config/parse</code> 规范化
          </div>
          <input
            id="config-upload"
            name="config-upload"
            type="file"
            aria-label="上传 YAML"
            accept=".yaml,.yml,application/x-yaml,text/yaml,text/x-yaml"
            className="hidden"
            onChange={handleConfigUpload}
          />
        </label>
      </div>

      {pageError ? (
        <Alert variant="destructive" role="alert">
          <AlertDescription>{pageError}</AlertDescription>
        </Alert>
      ) : null}

      {uploadNotice ? (
        <Alert>
          <AlertDescription>{uploadNotice}</AlertDescription>
        </Alert>
      ) : null}

      {/* 目标来源 */}
      <Card data-testid="target-source-section">
        <CardHeader className="pb-3">
          <div className="flex items-center justify-between">
            <CardTitle className="text-base">目标来源</CardTitle>
          </div>
          <p className="text-xs text-muted-foreground">
            选择本地 Maven 项目路径或 GitHub 仓库作为运行目标。
          </p>
        </CardHeader>
        <CardContent>
          <Tabs
            value={sourceMode}
            onValueChange={(value) => {
              if (value === 'local' && !allowLocalPathMode) {
                setSourceMode('upload');
                return;
              }
              setSourceMode(value as SourceMode);
            }}
            className="space-y-4"
          >
            <TabsList className="h-8">
              <TabsTrigger value="upload" className="text-xs h-6">
                上传项目
              </TabsTrigger>
              {allowLocalPathMode ? (
                <TabsTrigger value="local" className="text-xs h-6">
                  本地路径
                </TabsTrigger>
              ) : null}
              <TabsTrigger value="github" className="text-xs h-6">
                GitHub 仓库
              </TabsTrigger>
            </TabsList>

            <TabsContent value="upload" className="space-y-3 mt-3">
              <div className="space-y-1.5">
                <Label htmlFor="project-upload" className="text-xs">
                  项目 ZIP 文件
                </Label>
                <label
                  className={`flex flex-col items-center justify-center border border-dashed border-border rounded-lg px-4 py-6 text-sm hover:bg-accent transition-colors cursor-pointer ${
                    isUploadingProject ? 'opacity-50 cursor-not-allowed' : ''
                  }`}
                  htmlFor="project-upload"
                >
                  {projectUploadFile ? (
                    <div className="text-center">
                      <div className="font-medium text-primary">{projectUploadFile.name}</div>
                      <div className="text-xs text-muted-foreground mt-0.5">已上传</div>
                    </div>
                  ) : (
                    <div className="text-center">
                      <div className="font-medium">
                        {isUploadingProject ? '上传中...' : '点击上传项目 ZIP'}
                      </div>
                      <div className="text-xs text-muted-foreground mt-0.5">
                        必需，包含 Maven pom.xml 的项目目录
                      </div>
                    </div>
                  )}
                  <input
                    id="project-upload"
                    name="projectUpload"
                    type="file"
                    aria-label="上传项目 ZIP"
                    accept=".zip,application/zip,application/x-zip-compressed"
                    className="hidden"
                    onChange={handleProjectUpload}
                    disabled={isUploadingProject}
                  />
                </label>
                {fieldErrors.projectUpload ? (
                  <p className="text-xs text-destructive" role="alert">
                    {fieldErrors.projectUpload}
                  </p>
                ) : null}
              </div>

              <div className="space-y-1.5">
                <Label htmlFor="bug-reports-upload" className="text-xs">
                  缺陷报告 ZIP 文件
                </Label>
                <label
                  className={`flex flex-col items-center justify-center border border-dashed border-border rounded-lg px-4 py-6 text-sm hover:bg-accent transition-colors cursor-pointer ${
                    isUploadingBugReports ? 'opacity-50 cursor-not-allowed' : ''
                  }`}
                  htmlFor="bug-reports-upload"
                >
                  {bugReportsUploadFile ? (
                    <div className="text-center">
                      <div className="font-medium text-primary">{bugReportsUploadFile.name}</div>
                      <div className="text-xs text-muted-foreground mt-0.5">已上传</div>
                    </div>
                  ) : (
                    <div className="text-center">
                      <div className="font-medium">
                        {isUploadingBugReports ? '上传中...' : '点击上传缺陷报告 ZIP'}
                      </div>
                      <div className="text-xs text-muted-foreground mt-0.5">
                        可选，包含 Markdown 缺陷报告的目录
                      </div>
                    </div>
                  )}
                  <input
                    id="bug-reports-upload"
                    name="bugReportsUpload"
                    type="file"
                    aria-label="上传缺陷报告 ZIP"
                    accept=".zip,application/zip,application/x-zip-compressed"
                    className="hidden"
                    onChange={handleBugReportsUpload}
                    disabled={isUploadingBugReports}
                  />
                </label>
                <p className="text-xs text-muted-foreground">
                  可选的 Markdown 缺陷报告目录，会为本次运行的知识库建立索引。
                </p>
              </div>

              <JavaVersionSelect
                selectedJavaVersion={selectedJavaVersion}
                fieldError={fieldErrors.selectedJavaVersion}
                onChange={(version) => {
                  setSelectedJavaVersion(version);
                  setFieldErrors((current) => {
                    const nextErrors = { ...current };
                    delete nextErrors.selectedJavaVersion;
                    return nextErrors;
                  });
                }}
              />
            </TabsContent>

            <TabsContent value="local" className="space-y-3 mt-3">
              <Alert>
                <AlertDescription className="text-xs">
                  本地路径模式仅限管理员使用。服务端限制。
                </AlertDescription>
              </Alert>

              <div className="space-y-1.5">
                <Label htmlFor="project-path" className="text-xs">
                  项目路径
                </Label>
                <Input
                  id="project-path"
                  name="projectPath"
                  type="text"
                  aria-label="项目路径"
                  value={projectPath}
                  placeholder="/path/to/project 或 examples/calculator-demo"
                  className="h-8 text-sm"
                  onChange={(event) => {
                    setProjectPath(event.target.value);
                    setFieldErrors((current) => {
                      const nextErrors = { ...current };
                      delete nextErrors.projectPath;
                      return nextErrors;
                    });
                  }}
                />
                <p className="text-xs text-muted-foreground">接受的路径会在后端所在主机上解析。</p>
                {fieldErrors.projectPath ? (
                  <p className="text-xs text-destructive" role="alert">
                    {fieldErrors.projectPath}
                  </p>
                ) : null}
              </div>

              <div className="space-y-1.5">
                <Label htmlFor="bug-reports-dir" className="text-xs">
                  缺陷报告目录
                </Label>
                <Input
                  id="bug-reports-dir"
                  name="bugReportsDir"
                  type="text"
                  aria-label="缺陷报告目录"
                  value={bugReportsDir}
                  placeholder="examples/calculator-demo/bug-reports"
                  className="h-8 text-sm"
                  onChange={(event) => {
                    setBugReportsDir(event.target.value);
                    setFieldErrors((current) => {
                      const nextErrors = { ...current };
                      delete nextErrors.bugReportsDir;
                      return nextErrors;
                    });
                  }}
                />
                <p className="text-xs text-muted-foreground">
                  可选的 Markdown 缺陷报告目录，会为本次运行的知识库建立索引。
                </p>
                {fieldErrors.bugReportsDir ? (
                  <p className="text-xs text-destructive" role="alert">
                    {fieldErrors.bugReportsDir}
                  </p>
                ) : null}
              </div>

              <div>
                <p className="text-xs text-muted-foreground mb-1.5">示例项目</p>
                <div className="flex flex-wrap gap-1.5">
                  {EXAMPLE_PROJECTS.map((example) => (
                    <Button
                      key={example.path}
                      type="button"
                      variant="outline"
                      size="sm"
                      className="h-7 text-xs"
                      onClick={() => setProjectPath(example.path)}
                    >
                      {example.label}
                    </Button>
                  ))}
                </div>
              </div>
            </TabsContent>

            <TabsContent value="github" className="space-y-3 mt-3">
              {/* GitHub 授权状态 */}
              <div className="flex items-center justify-between py-2 px-3 rounded-md bg-muted/50">
                <div className="flex items-center gap-2">
                  {githubConnected ? (
                    <>
                      <Badge
                        variant="outline"
                        className="text-xs bg-green-50 text-green-700 border-green-200"
                      >
                        已连接
                      </Badge>
                      {githubAuthStatus?.username ? (
                        <span className="text-sm text-muted-foreground">
                          {githubAuthStatus.username}
                        </span>
                      ) : null}
                    </>
                  ) : githubAuthStatus?.requiresReauth ? (
                    <>
                      <Badge
                        variant="outline"
                        className="text-xs bg-yellow-50 text-yellow-700 border-yellow-200"
                      >
                        需重新授权
                      </Badge>
                      <span className="text-xs text-muted-foreground">
                        授权已过期或失效，请重新连接。
                      </span>
                    </>
                  ) : (
                    <>
                      <Badge variant="outline" className="text-xs">
                        未连接
                      </Badge>
                      <span className="text-xs text-muted-foreground">
                        请连接 GitHub 账户以使用仓库模式。
                      </span>
                    </>
                  )}
                </div>
                <div>
                  {!githubConnected ? (
                    <Button
                      type="button"
                      size="sm"
                      className="h-7 text-xs"
                      data-testid="github-connect-button"
                      onClick={handleConnectGithub}
                      disabled={isConnectingGithub}
                    >
                      {isConnectingGithub ? '连接中...' : '连接 GitHub'}
                    </Button>
                  ) : (
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      className="h-7 text-xs"
                      data-testid="disconnect-github-button"
                      onClick={handleDisconnectGithub}
                      disabled={isDisconnectingGithub}
                    >
                      {isDisconnectingGithub ? '断开中...' : '断开连接'}
                    </Button>
                  )}
                </div>
              </div>

              {/* 仓库选择 */}
              <div className="space-y-1.5">
                <Label htmlFor="github-repo-picker" className="text-xs">
                  选择仓库
                </Label>
                <Input
                  id="github-repo-picker"
                  name="githubRepoFilter"
                  type="text"
                  aria-label="搜索 GitHub 仓库"
                  data-testid="repo-picker-filter"
                  value={repoFilterQuery}
                  placeholder="搜索仓库名称..."
                  className="h-8 text-sm"
                  onChange={(event) => {
                    setRepoFilterQuery(event.target.value);
                    setFieldErrors((current) => {
                      const nextErrors = { ...current };
                      delete nextErrors.githubRepoUrl;
                      return nextErrors;
                    });
                  }}
                  disabled={!githubConnected}
                />
                {isLoadingRepositories ? (
                  <p className="text-xs text-muted-foreground" role="status">
                    正在加载仓库列表...
                  </p>
                ) : githubRepositories.length === 0 && githubConnected ? (
                  <p className="text-xs text-muted-foreground" role="status">
                    暂无可用仓库
                  </p>
                ) : (
                  <ul
                    className="max-h-48 overflow-y-auto rounded-md border border-border divide-y divide-border"
                    role="listbox"
                    aria-label="GitHub 仓库列表"
                  >
                    {githubRepositories
                      .filter((repo) =>
                        repoFilterQuery.trim() === ''
                          ? true
                          : repo.fullName.toLowerCase().includes(repoFilterQuery.toLowerCase()) ||
                            repo.name.toLowerCase().includes(repoFilterQuery.toLowerCase()),
                      )
                      .map((repo) => (
                        <li
                          key={repo.fullName}
                          role="option"
                          aria-selected={githubRepoUrl === repo.url}
                        >
                          <button
                            type="button"
                            className={`w-full text-left px-3 py-2 text-sm transition-colors ${
                              githubRepoUrl === repo.url
                                ? 'bg-primary/10 border-l-2 border-primary pl-[10px] repo-picker__item--selected'
                                : 'hover:bg-accent border-l-2 border-transparent pl-[10px]'
                            }`}
                            data-testid={`repo-item-${repo.fullName}`}
                            onClick={() => {
                              setGithubRepoUrl(repo.url);
                              setFieldErrors((current) => {
                                const nextErrors = { ...current };
                                delete nextErrors.githubRepoUrl;
                                return nextErrors;
                              });
                            }}
                            disabled={!githubConnected}
                          >
                            <div className="flex items-center gap-2">
                              <span
                                className={`font-medium ${githubRepoUrl === repo.url ? 'text-primary' : ''}`}
                              >
                                {repo.fullName}
                              </span>
                              {repo.private ? (
                                <Badge variant="secondary" className="text-xs h-4 px-1">
                                  私有
                                </Badge>
                              ) : null}
                              {githubRepoUrl === repo.url ? (
                                <svg
                                  className="ml-auto h-4 w-4 text-primary shrink-0"
                                  viewBox="0 0 24 24"
                                  fill="none"
                                  stroke="currentColor"
                                  strokeWidth="2.5"
                                  strokeLinecap="round"
                                  strokeLinejoin="round"
                                >
                                  <polyline points="20 6 9 17 4 12" />
                                </svg>
                              ) : null}
                            </div>
                            {repo.description ? (
                              <p className="text-xs text-muted-foreground mt-0.5 truncate">
                                {repo.description}
                              </p>
                            ) : null}
                          </button>
                        </li>
                      ))}
                  </ul>
                )}
                <p className="text-xs text-muted-foreground">
                  从已授权的 GitHub 账户仓库中选择目标仓库。
                </p>
                {fieldErrors.githubRepoUrl ? (
                  <p className="text-xs text-destructive" role="alert">
                    {fieldErrors.githubRepoUrl}
                  </p>
                ) : null}
              </div>

              <div className="space-y-1.5">
                <Label htmlFor="github-base-branch" className="text-xs">
                  基线分支
                </Label>
                <Input
                  id="github-base-branch"
                  name="githubBaseBranch"
                  type="text"
                  aria-label="基线分支"
                  value={githubBaseBranch}
                  placeholder="main 或 master"
                  className="h-8 text-sm"
                  onChange={(event) => {
                    setGithubBaseBranch(event.target.value);
                    setFieldErrors((current) => {
                      const nextErrors = { ...current };
                      delete nextErrors.githubBaseBranch;
                      return nextErrors;
                    });
                  }}
                  disabled={!githubConnected}
                />
                <p className="text-xs text-muted-foreground">
                  可选，留空时后端会尝试解析默认分支。
                </p>
                {fieldErrors.githubBaseBranch ? (
                  <p className="text-xs text-destructive" role="alert">
                    {fieldErrors.githubBaseBranch}
                  </p>
                ) : null}
              </div>

              <JavaVersionSelect
                selectedJavaVersion={selectedJavaVersion}
                fieldError={fieldErrors.selectedJavaVersion}
                disabled={!githubConnected}
                onChange={(version) => {
                  setSelectedJavaVersion(version);
                  setFieldErrors((current) => {
                    const nextErrors = { ...current };
                    delete nextErrors.selectedJavaVersion;
                    return nextErrors;
                  });
                }}
              />

              {!githubConnected ? (
                <Alert>
                  <AlertDescription className="text-xs">
                    请先连接 GitHub 账户后再使用仓库模式运行。
                  </AlertDescription>
                </Alert>
              ) : null}
            </TabsContent>
          </Tabs>
          <div className="flex justify-end pt-4">
            <Button
              type="button"
              onClick={handleSubmit}
              disabled={
                isSubmitting ||
                (sourceMode === 'upload' && (!projectUploadId || !selectedJavaVersion)) ||
                (sourceMode === 'github' && !githubConnected)
              }
            >
              {isSubmitting
                ? '正在启动运行...'
                : sourceMode === 'upload'
                  ? !selectedJavaVersion
                    ? '请选择 Java 版本'
                    : !projectUploadId
                      ? '请先上传项目'
                      : '启动运行'
                  : sourceMode === 'github' && !githubConnected
                    ? '请先连接 GitHub'
                    : '启动运行'}
            </Button>
          </div>
        </CardContent>
      </Card>

      {/* 配置分组 */}
      {groupedSections.map((section) => (
        <Card key={section.key}>
          <CardHeader className="pb-3">
            <CardTitle className="text-base">{section.title}</CardTitle>
            <p className="text-xs text-muted-foreground">{section.description}</p>
          </CardHeader>
          <CardContent>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-6 gap-y-3">
              {section.fields.map((field) => {
                const fieldKey = getFieldKey(field.path);
                const fieldId = `field-${fieldKey.replaceAll('.', '-')}`;
                const hintId = getFieldHintId(field.path);
                const value = getNestedValue(config, field.path);
                const error = fieldErrors[fieldKey];
                const policyNotes = getFieldPolicyNotes(fieldKey, configPolicy);

                return (
                  <div key={fieldKey} className="space-y-1">
                    <Label className="text-xs" htmlFor={fieldId}>
                      {field.label}
                    </Label>
                    {field.kind === 'boolean' ? (
                      <div className="flex items-center gap-2">
                        <input
                          id={fieldId}
                          type="checkbox"
                          aria-describedby={hintId}
                          checked={Boolean(value)}
                          className="h-3.5 w-3.5 rounded"
                          onChange={(event) =>
                            handleFieldChange(field, event.target.value, event.target.checked)
                          }
                        />
                        <span className="text-xs">启用</span>
                      </div>
                    ) : field.kind === 'nullable-boolean' ? (
                      <Select
                        value={value === null || value === undefined ? '' : String(value)}
                        onValueChange={(val) => handleFieldChange(field, val ?? '', false)}
                      >
                        <SelectTrigger
                          id={fieldId}
                          aria-describedby={hintId}
                          className="h-7 text-xs"
                        >
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem value="" className="text-xs">
                            继承默认值
                          </SelectItem>
                          <SelectItem value="true" className="text-xs">
                            是
                          </SelectItem>
                          <SelectItem value="false" className="text-xs">
                            否
                          </SelectItem>
                        </SelectContent>
                      </Select>
                    ) : (
                      <Input
                        id={fieldId}
                        type={field.kind === 'number' ? 'number' : field.kind}
                        aria-describedby={hintId}
                        value={valueToInputString(value)}
                        step={field.step}
                        placeholder={field.placeholder}
                        className="h-7 text-xs"
                        onChange={(event) =>
                          handleFieldChange(field, event.target.value, event.target.checked)
                        }
                      />
                    )}
                    <p id={hintId} className="text-xs text-muted-foreground leading-tight">
                      {field.description}
                    </p>
                    {policyNotes.length > 0 ? (
                      <div className="space-y-1" aria-label={`${field.label} 服务端限制`}>
                        {policyNotes.map((note) => (
                          <div
                            key={note}
                            className="flex items-start gap-1.5 text-xs text-muted-foreground"
                          >
                            <Badge variant="outline" className="h-4 px-1 text-[0.65rem]">
                              服务端限制
                            </Badge>
                            <span className="leading-tight">{note}</span>
                          </div>
                        ))}
                      </div>
                    ) : null}
                    {error ? <p className="text-xs text-destructive">{error}</p> : null}
                  </div>
                );
              })}
            </div>
          </CardContent>
        </Card>
      ))}
    </div>
  );
}
