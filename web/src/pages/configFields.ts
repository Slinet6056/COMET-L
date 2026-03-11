export type ConfigFieldKind = 'text' | 'password' | 'number' | 'boolean' | 'nullable-boolean';

export type ConfigFieldDefinition = {
  path: string[];
  label: string;
  description: string;
  kind: ConfigFieldKind;
  step?: string;
  placeholder?: string;
};

export type ConfigSectionDefinition = {
  key: string;
  title: string;
  description: string;
  fields: ConfigFieldDefinition[];
};

export const CONFIG_SECTIONS: ConfigSectionDefinition[] = [
  {
    key: 'llm',
    title: 'LLM',
    description: 'Model endpoint, credentials, and response behavior.',
    fields: [
      { path: ['llm', 'base_url'], label: 'Base URL', description: 'LLM API base URL.', kind: 'text' },
      { path: ['llm', 'api_key'], label: 'API key', description: 'LLM provider credential.', kind: 'password' },
      { path: ['llm', 'model'], label: 'Model', description: 'Primary generation model.', kind: 'text' },
      { path: ['llm', 'temperature'], label: 'Temperature', description: 'Sampling temperature.', kind: 'number', step: '0.1' },
      { path: ['llm', 'max_tokens'], label: 'Max tokens', description: 'Maximum response tokens.', kind: 'number', step: '1' },
      { path: ['llm', 'supports_json_mode'], label: 'Supports JSON mode', description: 'Whether the model supports JSON mode.', kind: 'boolean' },
      { path: ['llm', 'timeout'], label: 'Timeout', description: 'Request timeout in seconds.', kind: 'number', step: '1' },
      { path: ['llm', 'reasoning_effort'], label: 'Reasoning effort', description: 'Optional reasoning effort hint.', kind: 'text', placeholder: 'none | low | medium | high' },
      { path: ['llm', 'reasoning_enabled'], label: 'Reasoning enabled', description: 'Explicit reasoning toggle or inherit.', kind: 'nullable-boolean' },
      { path: ['llm', 'verbosity'], label: 'Verbosity', description: 'Optional response detail hint.', kind: 'text', placeholder: 'low | medium | high' },
    ],
  },
  {
    key: 'execution',
    title: 'Execution',
    description: 'Runtime timeouts and Java or Maven paths.',
    fields: [
      { path: ['execution', 'timeout'], label: 'Execution timeout', description: 'Overall timeout in seconds.', kind: 'number', step: '1' },
      { path: ['execution', 'test_timeout'], label: 'Test timeout', description: 'Test execution timeout in seconds.', kind: 'number', step: '1' },
      { path: ['execution', 'coverage_timeout'], label: 'Coverage timeout', description: 'Coverage collection timeout in seconds.', kind: 'number', step: '1' },
      { path: ['execution', 'max_retries'], label: 'Max retries', description: 'Retry count for execution failures.', kind: 'number', step: '1' },
      { path: ['execution', 'runtime_java_home'], label: 'Runtime Java home', description: 'JDK used by COMET-L runtime.', kind: 'text' },
      { path: ['execution', 'target_java_home'], label: 'Target Java home', description: 'JDK used by the target project.', kind: 'text' },
      { path: ['execution', 'maven_home'], label: 'Maven home', description: 'Optional Maven installation path.', kind: 'text' },
    ],
  },
  {
    key: 'paths',
    title: 'Paths',
    description: 'Cache, output, and sandbox directories.',
    fields: [
      { path: ['paths', 'cache'], label: 'Cache directory', description: 'Cache output directory.', kind: 'text' },
      { path: ['paths', 'output'], label: 'Output directory', description: 'Run output directory.', kind: 'text' },
      { path: ['paths', 'sandbox'], label: 'Sandbox directory', description: 'Sandbox working directory.', kind: 'text' },
    ],
  },
  {
    key: 'evolution',
    title: 'Evolution',
    description: 'Iteration limits, stopping rules, and quality targets.',
    fields: [
      { path: ['evolution', 'max_iterations'], label: 'Max iterations', description: 'Maximum planner iterations.', kind: 'number', step: '1' },
      { path: ['evolution', 'min_improvement_threshold'], label: 'Minimum improvement threshold', description: 'Absolute improvement threshold.', kind: 'number', step: '0.01' },
      { path: ['evolution', 'budget_llm_calls'], label: 'LLM call budget', description: 'Total LLM call budget.', kind: 'number', step: '1' },
      { path: ['evolution', 'stop_on_no_improvement_rounds'], label: 'Stop after stagnant rounds', description: 'Stop after repeated no-improvement rounds.', kind: 'number', step: '1' },
      { path: ['evolution', 'excellent_mutation_score'], label: 'Excellent mutation score', description: 'Early-stop mutation score threshold.', kind: 'number', step: '0.01' },
      { path: ['evolution', 'excellent_line_coverage'], label: 'Excellent line coverage', description: 'Early-stop line coverage threshold.', kind: 'number', step: '0.01' },
      { path: ['evolution', 'excellent_branch_coverage'], label: 'Excellent branch coverage', description: 'Early-stop branch coverage threshold.', kind: 'number', step: '0.01' },
      { path: ['evolution', 'min_method_lines'], label: 'Minimum method lines', description: 'Skip methods smaller than this size.', kind: 'number', step: '1' },
    ],
  },
  {
    key: 'knowledge',
    title: 'Knowledge',
    description: 'RAG, embedding, vector store, and retrieval behavior.',
    fields: [
      { path: ['knowledge', 'enabled'], label: 'Knowledge enabled', description: 'Enable RAG knowledge retrieval.', kind: 'boolean' },
      { path: ['knowledge', 'enable_dynamic_update'], label: 'Dynamic updates', description: 'Allow knowledge base updates during runs.', kind: 'boolean' },
      { path: ['knowledge', 'pattern_confidence_threshold'], label: 'Pattern confidence threshold', description: 'Minimum confidence to accept patterns.', kind: 'number', step: '0.01' },
      { path: ['knowledge', 'contract_extraction_enabled'], label: 'Contract extraction enabled', description: 'Extract contracts from source code.', kind: 'boolean' },
      { path: ['knowledge', 'embedding', 'base_url'], label: 'Embedding base URL', description: 'Embedding API base URL.', kind: 'text' },
      { path: ['knowledge', 'embedding', 'api_key'], label: 'Embedding API key', description: 'Optional embedding credential.', kind: 'password' },
      { path: ['knowledge', 'embedding', 'model'], label: 'Embedding model', description: 'Embedding model name.', kind: 'text' },
      { path: ['knowledge', 'embedding', 'batch_size'], label: 'Embedding batch size', description: 'Batch size for embeddings.', kind: 'number', step: '1' },
      { path: ['knowledge', 'vector_db', 'type'], label: 'Vector DB type', description: 'Vector database driver.', kind: 'text' },
      { path: ['knowledge', 'vector_db', 'persist_directory'], label: 'Vector DB directory', description: 'Persistent vector database path.', kind: 'text' },
      { path: ['knowledge', 'retrieval', 'top_k'], label: 'Retrieval top K', description: 'Documents retrieved per query.', kind: 'number', step: '1' },
      { path: ['knowledge', 'retrieval', 'score_threshold'], label: 'Retrieval score threshold', description: 'Minimum semantic match score.', kind: 'number', step: '0.01' },
    ],
  },
  {
    key: 'logging',
    title: 'Logging',
    description: 'Run log level and file target.',
    fields: [
      { path: ['logging', 'level'], label: 'Log level', description: 'Application log level.', kind: 'text' },
      { path: ['logging', 'file'], label: 'Log file', description: 'Log file name or path.', kind: 'text' },
    ],
  },
  {
    key: 'preprocessing',
    title: 'Preprocessing',
    description: 'Parallel preprocessing before the main loop.',
    fields: [
      { path: ['preprocessing', 'enabled'], label: 'Preprocessing enabled', description: 'Enable preprocessing stage.', kind: 'boolean' },
      { path: ['preprocessing', 'max_workers'], label: 'Max workers', description: 'Optional preprocessing worker count.', kind: 'number', step: '1' },
      { path: ['preprocessing', 'timeout_per_method'], label: 'Timeout per method', description: 'Timeout per method in seconds.', kind: 'number', step: '1' },
    ],
  },
  {
    key: 'formatting',
    title: 'Formatting',
    description: 'Generated Java formatting behavior.',
    fields: [
      { path: ['formatting', 'enabled'], label: 'Formatting enabled', description: 'Format generated Java code.', kind: 'boolean' },
      { path: ['formatting', 'style'], label: 'Formatting style', description: 'Formatter style name.', kind: 'text' },
    ],
  },
  {
    key: 'agent',
    title: 'Agent',
    description: 'Parallel agent execution settings.',
    fields: [
      { path: ['agent', 'parallel', 'enabled'], label: 'Parallel mode enabled', description: 'Enable parallel agent mode.', kind: 'boolean' },
      { path: ['agent', 'parallel', 'max_parallel_targets'], label: 'Max parallel targets', description: 'Concurrent target count.', kind: 'number', step: '1' },
      { path: ['agent', 'parallel', 'max_eval_workers'], label: 'Max evaluation workers', description: 'Concurrent mutant evaluation workers.', kind: 'number', step: '1' },
      { path: ['agent', 'parallel', 'timeout_per_target'], label: 'Timeout per target', description: 'Per-target timeout in seconds.', kind: 'number', step: '1' },
    ],
  },
];

export const EXAMPLE_PROJECTS = [
  { label: 'Calculator demo', path: 'examples/calculator-demo' },
  { label: 'Mockito demo', path: 'examples/mockito-demo' },
  { label: 'Multi-file demo', path: 'examples/multi-file-demo' },
];
