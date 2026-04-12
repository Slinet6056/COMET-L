import { ChangeEvent, useEffect, useMemo, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';

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
  type GitHubAuthStatus,
  type GitHubRepository,
} from '../lib/api';
import { CONFIG_SECTIONS, EXAMPLE_PROJECTS, type ConfigFieldDefinition } from './configFields';

type ConfigValue = Record<string, unknown>;
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

type SourceMode = 'local' | 'github';

const JAVA_VERSION_OPTIONS = ['8', '11', '17', '21', '25'];

export function HomePage() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const [config, setConfig] = useState<ConfigValue | null>(null);
  const [sourceMode, setSourceMode] = useState<SourceMode>('local');
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

  useEffect(() => {
    let active = true;

    async function loadDefaults() {
      try {
        const payload = await fetchConfigDefaults();
        if (!active) {
          return;
        }
        setConfig(payload.config);
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
      setConfig(payload.config);
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

    const targetJavaHome = getNestedValue(config, ['execution', 'target_java_home']);
    const hasManualTargetJavaHome =
      typeof targetJavaHome === 'string' && targetJavaHome.trim().length > 0;

    if (sourceMode === 'github') {
      if (!githubAuthStatus?.connected || githubAuthStatus.requiresReauth) {
        setPageError('请先连接 GitHub 账户后再使用仓库模式。');
        return;
      }

      if (!githubRepoUrl.trim()) {
        setFieldErrors({ githubRepoUrl: '请输入 GitHub 仓库 URL。' });
        return;
      }

      if (!selectedJavaVersion && !hasManualTargetJavaHome) {
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
        selectedJavaVersion: sourceMode === 'github' ? selectedJavaVersion || null : null,
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
      <section className="panel">
        <p className="eyebrow">配置</p>
        <h2>运行配置首页</h2>
        <p>正在从后端加载默认设置...</p>
      </section>
    );
  }

  if (config === null) {
    return (
      <section className="panel">
        <p className="eyebrow">配置</p>
        <h2>运行配置首页</h2>
        <p role="alert">{pageError ?? '默认配置当前不可用。'}</p>
      </section>
    );
  }

  return (
    <section className="panel config-page">
      <div className="config-hero">
        <div>
          <p className="eyebrow">配置</p>
          <h2>运行配置首页</h2>
          <p>
            上传 YAML 配置后，后端会进行规范化并回填表单。上传文件只用于本次运行参数；GitHub
            授权使用单独的连接流程。你可以继续调整分组设置，然后使用本地 Maven
            项目路径启动一次运行。
          </p>
        </div>

        <label className="upload-card" htmlFor="config-upload">
          <span className="upload-card__title">上传 YAML</span>
          <span className="upload-card__body">
            使用 <code>/api/config/parse</code> 规范化运行配置并回填表单。
          </span>
          <input
            id="config-upload"
            name="config-upload"
            type="file"
            aria-label="上传 YAML"
            accept=".yaml,.yml,application/x-yaml,text/yaml,text/x-yaml"
            onChange={handleConfigUpload}
          />
        </label>
      </div>

      {pageError ? (
        <div className="feedback-banner feedback-banner--error" role="alert">
          {pageError}
        </div>
      ) : null}

      {uploadNotice ? (
        <div className="feedback-banner feedback-banner--success">{uploadNotice}</div>
      ) : null}

      <div className="panel project-panel">
        <div>
          <p className="eyebrow">项目</p>
          <h3>目标来源</h3>
          <p>选择本地 Maven 项目路径或 GitHub 仓库作为运行目标。</p>
        </div>

        <div className="source-mode-tabs">
          <button
            type="button"
            className={`secondary-button ${sourceMode === 'local' ? 'source-mode-tab--active' : ''}`}
            onClick={() => setSourceMode('local')}
          >
            本地路径
          </button>
          <button
            type="button"
            className={`secondary-button ${sourceMode === 'github' ? 'source-mode-tab--active' : ''}`}
            onClick={() => setSourceMode('github')}
          >
            GitHub 仓库
          </button>
        </div>

        {sourceMode === 'local' ? (
          <>
            <label className="field" htmlFor="project-path">
              <span className="field__label">项目路径</span>
              <input
                id="project-path"
                name="projectPath"
                type="text"
                aria-label="项目路径"
                value={projectPath}
                placeholder="/path/to/project 或 examples/calculator-demo"
                onChange={(event) => {
                  setProjectPath(event.target.value);
                  setFieldErrors((current) => {
                    const nextErrors = { ...current };
                    delete nextErrors.projectPath;
                    return nextErrors;
                  });
                }}
              />
              <span className="field__hint">接受的路径会在后端所在主机上解析。</span>
              {fieldErrors.projectPath ? (
                <span className="field__error" role="alert">
                  {fieldErrors.projectPath}
                </span>
              ) : null}
            </label>

            <label className="field" htmlFor="bug-reports-dir">
              <span className="field__label">缺陷报告目录</span>
              <input
                id="bug-reports-dir"
                name="bugReportsDir"
                type="text"
                aria-label="缺陷报告目录"
                value={bugReportsDir}
                placeholder="examples/calculator-demo/bug-reports"
                onChange={(event) => {
                  setBugReportsDir(event.target.value);
                  setFieldErrors((current) => {
                    const nextErrors = { ...current };
                    delete nextErrors.bugReportsDir;
                    return nextErrors;
                  });
                }}
              />
              <span className="field__hint">
                可选的 Markdown 缺陷报告目录，会为本次运行的知识库建立索引。
              </span>
              {fieldErrors.bugReportsDir ? (
                <span className="field__error" role="alert">
                  {fieldErrors.bugReportsDir}
                </span>
              ) : null}
            </label>

            <div>
              <p className="eyebrow">示例</p>
              <div className="example-shortcuts">
                {EXAMPLE_PROJECTS.map((example) => (
                  <button
                    key={example.path}
                    type="button"
                    className="secondary-button"
                    onClick={() => setProjectPath(example.path)}
                  >
                    {example.label}
                  </button>
                ))}
              </div>
            </div>
          </>
        ) : (
          <>
            <div className="github-auth-section">
              <div className="github-auth-status">
                {githubAuthStatus?.connected && !githubAuthStatus.requiresReauth ? (
                  <div className="github-auth-connected">
                    <span className="github-auth-badge github-auth-badge--connected">已连接</span>
                    {githubAuthStatus.username ? (
                      <span className="github-auth-username">{githubAuthStatus.username}</span>
                    ) : null}
                  </div>
                ) : githubAuthStatus?.requiresReauth ? (
                  <div className="github-auth-reauth">
                    <span className="github-auth-badge github-auth-badge--reauth">需重新授权</span>
                    <span className="github-auth-hint">授权已过期或失效，请重新连接。</span>
                  </div>
                ) : (
                  <div className="github-auth-disconnected">
                    <span className="github-auth-badge github-auth-badge--disconnected">
                      未连接
                    </span>
                    <span className="github-auth-hint">请连接 GitHub 账户以使用仓库模式。</span>
                  </div>
                )}
              </div>

              <div className="github-auth-actions">
                {!githubAuthStatus?.connected || githubAuthStatus.requiresReauth ? (
                  <button
                    type="button"
                    className="primary-button"
                    data-testid="github-connect-button"
                    onClick={handleConnectGithub}
                    disabled={isConnectingGithub}
                  >
                    {isConnectingGithub ? '正在连接...' : '连接 GitHub'}
                  </button>
                ) : (
                  <button
                    type="button"
                    className="secondary-button"
                    data-testid="disconnect-github-button"
                    onClick={handleDisconnectGithub}
                    disabled={isDisconnectingGithub}
                  >
                    {isDisconnectingGithub ? '正在断开...' : '断开连接'}
                  </button>
                )}
              </div>
            </div>

            <label className="field" htmlFor="github-repo-picker">
              <span className="field__label">选择仓库</span>
              <div className="repo-picker">
                <input
                  id="github-repo-picker"
                  name="githubRepoFilter"
                  type="text"
                  aria-label="搜索 GitHub 仓库"
                  data-testid="repo-picker-filter"
                  value={repoFilterQuery}
                  placeholder="搜索仓库名称..."
                  onChange={(event) => {
                    setRepoFilterQuery(event.target.value);
                    setFieldErrors((current) => {
                      const nextErrors = { ...current };
                      delete nextErrors.githubRepoUrl;
                      return nextErrors;
                    });
                  }}
                  disabled={!githubAuthStatus?.connected || githubAuthStatus.requiresReauth}
                />
                {isLoadingRepositories ? (
                  <div className="repo-picker__loading" role="status">
                    正在加载仓库列表...
                  </div>
                ) : githubRepositories.length === 0 &&
                  githubAuthStatus?.connected &&
                  !githubAuthStatus.requiresReauth ? (
                  <div className="repo-picker__empty" role="status">
                    暂无可用仓库
                  </div>
                ) : (
                  <ul className="repo-picker__list" role="listbox" aria-label="GitHub 仓库列表">
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
                            className={`repo-picker__item ${githubRepoUrl === repo.url ? 'repo-picker__item--selected' : ''}`}
                            data-testid={`repo-item-${repo.fullName}`}
                            onClick={() => {
                              setGithubRepoUrl(repo.url);
                              setFieldErrors((current) => {
                                const nextErrors = { ...current };
                                delete nextErrors.githubRepoUrl;
                                return nextErrors;
                              });
                            }}
                            disabled={
                              !githubAuthStatus?.connected || githubAuthStatus.requiresReauth
                            }
                          >
                            <span className="repo-picker__item-name">{repo.fullName}</span>
                            {repo.private ? (
                              <span className="repo-picker__item-badge repo-picker__item-badge--private">
                                私有
                              </span>
                            ) : null}
                            {repo.description ? (
                              <span className="repo-picker__item-desc">{repo.description}</span>
                            ) : null}
                          </button>
                        </li>
                      ))}
                  </ul>
                )}
              </div>
              <span className="field__hint">从已授权的 GitHub 账户仓库中选择目标仓库。</span>
              {fieldErrors.githubRepoUrl ? (
                <span className="field__error" role="alert">
                  {fieldErrors.githubRepoUrl}
                </span>
              ) : null}
            </label>

            <label className="field" htmlFor="github-base-branch">
              <span className="field__label">基线分支</span>
              <input
                id="github-base-branch"
                name="githubBaseBranch"
                type="text"
                aria-label="基线分支"
                value={githubBaseBranch}
                placeholder="main 或 master"
                onChange={(event) => {
                  setGithubBaseBranch(event.target.value);
                  setFieldErrors((current) => {
                    const nextErrors = { ...current };
                    delete nextErrors.githubBaseBranch;
                    return nextErrors;
                  });
                }}
                disabled={!githubAuthStatus?.connected || githubAuthStatus.requiresReauth}
              />
              <span className="field__hint">
                可选的基线分支名称，留空时后端会尝试解析默认分支。
              </span>
              {fieldErrors.githubBaseBranch ? (
                <span className="field__error" role="alert">
                  {fieldErrors.githubBaseBranch}
                </span>
              ) : null}
            </label>

            <label className="field" htmlFor="selected-java-version">
              <span className="field__label">目标 Java 版本</span>
              <select
                id="selected-java-version"
                name="selectedJavaVersion"
                aria-label="目标 Java 版本"
                data-testid="java-version-select"
                value={selectedJavaVersion}
                onChange={(event) => {
                  setSelectedJavaVersion(event.target.value);
                  setFieldErrors((current) => {
                    const nextErrors = { ...current };
                    delete nextErrors.selectedJavaVersion;
                    return nextErrors;
                  });
                }}
                disabled={!githubAuthStatus?.connected || githubAuthStatus.requiresReauth}
              >
                <option value="">请选择版本</option>
                {JAVA_VERSION_OPTIONS.map((version) => (
                  <option key={version} value={version}>
                    Java {version}
                  </option>
                ))}
              </select>
              <span className="field__hint">
                选择目标项目使用的 Java 版本，会映射到容器内固定 JDK 路径；若手动填写目标项目 Java
                目录，则以手填路径为准。
              </span>
              {fieldErrors.selectedJavaVersion ? (
                <span className="field__error" role="alert">
                  {fieldErrors.selectedJavaVersion}
                </span>
              ) : null}
            </label>

            {!githubAuthStatus?.connected || githubAuthStatus.requiresReauth ? (
              <div className="feedback-banner feedback-banner--warning">
                请先连接 GitHub 账户后再使用仓库模式运行。
              </div>
            ) : null}
          </>
        )}
      </div>

      <div className="config-sections">
        {groupedSections.map((section) => (
          <section key={section.key} className="panel config-section">
            <div className="section-heading">
              <p className="eyebrow">分组</p>
              <h3>{section.title}</h3>
              <p>{section.description}</p>
            </div>

            <div className="field-grid">
              {section.fields.map((field) => {
                const fieldKey = getFieldKey(field.path);
                const fieldId = `field-${fieldKey.replaceAll('.', '-')}`;
                const hintId = getFieldHintId(field.path);
                const value = getNestedValue(config, field.path);
                const error = fieldErrors[fieldKey];

                return (
                  <div key={fieldKey} className="field">
                    <label className="field__label" htmlFor={fieldId}>
                      {field.label}
                    </label>
                    {field.kind === 'boolean' ? (
                      <span className="checkbox-field">
                        <input
                          id={fieldId}
                          type="checkbox"
                          aria-describedby={hintId}
                          checked={Boolean(value)}
                          onChange={(event) =>
                            handleFieldChange(field, event.target.value, event.target.checked)
                          }
                        />
                        <span>启用</span>
                      </span>
                    ) : field.kind === 'nullable-boolean' ? (
                      <select
                        id={fieldId}
                        aria-describedby={hintId}
                        value={value === null || value === undefined ? '' : String(value)}
                        onChange={(event) => handleFieldChange(field, event.target.value, false)}
                      >
                        <option value="">继承默认值</option>
                        <option value="true">是</option>
                        <option value="false">否</option>
                      </select>
                    ) : (
                      <input
                        id={fieldId}
                        type={field.kind === 'number' ? 'number' : field.kind}
                        aria-describedby={hintId}
                        value={valueToInputString(value)}
                        step={field.step}
                        placeholder={field.placeholder}
                        onChange={(event) =>
                          handleFieldChange(field, event.target.value, event.target.checked)
                        }
                      />
                    )}
                    <span id={hintId} className="field__hint">
                      {field.description}
                    </span>
                    {error ? <span className="field__error">{error}</span> : null}
                  </div>
                );
              })}
            </div>
          </section>
        ))}
      </div>

      <div className="action-row">
        <button
          type="button"
          className="primary-button"
          onClick={handleSubmit}
          disabled={
            isSubmitting ||
            (sourceMode === 'github' &&
              (!githubAuthStatus?.connected || githubAuthStatus.requiresReauth))
          }
        >
          {isSubmitting
            ? '正在启动运行...'
            : sourceMode === 'github' &&
                (!githubAuthStatus?.connected || githubAuthStatus.requiresReauth)
              ? '请先连接 GitHub'
              : '启动运行'}
        </button>
      </div>
    </section>
  );
}
