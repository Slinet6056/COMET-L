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

function parseFieldValue(field: ConfigFieldDefinition, rawValue: string, checked: boolean): unknown {
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
        setPageError(
          error instanceof Error
            ? error.message
            : 'Unable to load default configuration.',
        );
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
      setUploadNotice(`${file.name} parsed and loaded into the form.`);
    } catch (error) {
      if (error instanceof ApiError) {
        setFieldErrors(buildFieldErrors(error));
        setPageError(error.message);
      } else {
        setPageError('Unable to parse the uploaded configuration file.');
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
    setConfig((current) => (current === null ? current : setNestedValue(current, field.path, nextValue)));
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
        setPageError('Unable to create a run.');
      }
    } finally {
      setIsSubmitting(false);
    }
  }

  if (isLoadingDefaults) {
    return (
      <section className="panel">
        <p className="eyebrow">Configuration</p>
        <h2>Run Configuration Home</h2>
        <p>Loading default settings from the backend...</p>
      </section>
    );
  }

  if (config === null) {
    return (
      <section className="panel">
        <p className="eyebrow">Configuration</p>
        <h2>Run Configuration Home</h2>
        <p role="alert">{pageError ?? 'Configuration defaults are unavailable.'}</p>
      </section>
    );
  }

  return (
    <section className="panel config-page">
      <div className="config-hero">
        <div>
          <p className="eyebrow">Configuration</p>
          <h2>Run Configuration Home</h2>
          <p>
            Upload a YAML config for backend-normalized backfill, tune grouped
            settings, then start a run with a local Maven project path.
          </p>
        </div>

        <label className="upload-card" htmlFor="config-upload">
          <span className="upload-card__title">Upload YAML</span>
          <span className="upload-card__body">
            Use <code>/api/config/parse</code> to normalize and refill the form.
          </span>
          <input
            id="config-upload"
            name="config-upload"
            type="file"
            aria-label="Upload YAML"
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
          <p className="eyebrow">Project</p>
          <h3>Target project path</h3>
          <p>Point COMET-L at a local Maven project directory containing `pom.xml`.</p>
        </div>

        <label className="field" htmlFor="project-path">
          <span className="field__label">Project path</span>
          <input
            id="project-path"
            name="projectPath"
            type="text"
            aria-label="Project path"
            value={projectPath}
            placeholder="/path/to/project or examples/calculator-demo"
            onChange={(event) => {
              setProjectPath(event.target.value);
              setFieldErrors((current) => {
                const nextErrors = { ...current };
                delete nextErrors.projectPath;
                return nextErrors;
              });
            }}
          />
          <span className="field__hint">Accepted values resolve on the backend host.</span>
          {fieldErrors.projectPath ? (
            <span className="field__error" role="alert">
              {fieldErrors.projectPath}
            </span>
          ) : null}
        </label>

        <label className="field" htmlFor="bug-reports-dir">
          <span className="field__label">Bug reports directory</span>
          <input
            id="bug-reports-dir"
            name="bugReportsDir"
            type="text"
            aria-label="Bug reports directory"
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
            Optional directory of Markdown bug reports to index for the run knowledge base.
          </span>
          {fieldErrors.bugReportsDir ? (
            <span className="field__error" role="alert">
              {fieldErrors.bugReportsDir}
            </span>
          ) : null}
        </label>

        <div>
          <p className="eyebrow">Examples</p>
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
              <p className="eyebrow">Section</p>
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
                        <span>Enabled</span>
                      </span>
                    ) : field.kind === 'nullable-boolean' ? (
                      <select
                        id={fieldId}
                        value={value === null || value === undefined ? '' : String(value)}
                        onChange={(event) => handleFieldChange(field, event.target.value, false)}
                      >
                        <option value="">Inherit default</option>
                        <option value="true">True</option>
                        <option value="false">False</option>
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
        <button type="button" className="primary-button" onClick={handleSubmit} disabled={isSubmitting}>
          {isSubmitting ? 'Starting run...' : 'Start run'}
        </button>
      </div>
    </section>
  );
}
