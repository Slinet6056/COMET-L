import { ChangeEvent, useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';

import { ApiError, createRun, fetchConfigDefaults, parseConfigFile } from '../lib/api';
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

export function HomePage() {
  const navigate = useNavigate();
  const [config, setConfig] = useState<ConfigValue | null>(null);
  const [projectPath, setProjectPath] = useState('');
  const [bugReportsDir, setBugReportsDir] = useState('');
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

  async function handleSubmit() {
    if (config === null || isSubmitting) {
      return;
    }

    setIsSubmitting(true);
    setFieldErrors({});
    setPageError(null);

    try {
      const response = await createRun({ projectPath, bugReportsDir, config });
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
            上传 YAML 配置后，后端会进行规范化并回填表单。你可以继续调整分组设置， 然后使用本地
            Maven 项目路径启动一次运行。
          </p>
        </div>

        <label className="upload-card" htmlFor="config-upload">
          <span className="upload-card__title">上传 YAML</span>
          <span className="upload-card__body">
            使用 <code>/api/config/parse</code> 规范化配置并回填表单。
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
          <h3>目标项目路径</h3>
          <p>将 COMET-L 指向包含 `pom.xml` 的本地 Maven 项目目录。</p>
        </div>

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
                        value={valueToInputString(value)}
                        step={field.step}
                        placeholder={field.placeholder}
                        onChange={(event) =>
                          handleFieldChange(field, event.target.value, event.target.checked)
                        }
                      />
                    )}
                    <span className="field__hint">{field.description}</span>
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
          disabled={isSubmitting}
        >
          {isSubmitting ? '正在启动运行...' : '启动运行'}
        </button>
      </div>
    </section>
  );
}
