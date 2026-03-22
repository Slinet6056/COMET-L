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
    description: '模型端点、凭证和响应行为。',
    fields: [
      {
        path: ['llm', 'base_url'],
        label: '基础 URL',
        description: 'LLM API 基础 URL。',
        kind: 'text',
      },
      {
        path: ['llm', 'api_key'],
        label: 'API 密钥',
        description: 'LLM 提供方凭证。',
        kind: 'password',
      },
      { path: ['llm', 'model'], label: '模型', description: '主生成模型。', kind: 'text' },
      {
        path: ['llm', 'temperature'],
        label: '温度',
        description: '采样温度。',
        kind: 'number',
        step: '0.1',
      },
      {
        path: ['llm', 'max_tokens'],
        label: '最大令牌数',
        description: '单次请求的总令牌预算，包含输入与输出。',
        kind: 'number',
        step: '1',
      },
      {
        path: ['llm', 'supports_json_mode'],
        label: '支持 JSON 模式',
        description: '模型是否支持 JSON 模式。',
        kind: 'boolean',
      },
      {
        path: ['llm', 'timeout'],
        label: '超时时间',
        description: '请求超时时间，单位为秒。',
        kind: 'number',
        step: '1',
      },
      {
        path: ['llm', 'reasoning_effort'],
        label: '推理强度',
        description: '可选的推理强度提示。',
        kind: 'text',
        placeholder: 'none | low | medium | high',
      },
      {
        path: ['llm', 'reasoning_enabled'],
        label: '启用推理',
        description: '显式切换推理，或继承默认值。',
        kind: 'nullable-boolean',
      },
      {
        path: ['llm', 'verbosity'],
        label: '详细程度',
        description: '可选的响应详细程度提示。',
        kind: 'text',
        placeholder: 'low | medium | high',
      },
    ],
  },
  {
    key: 'execution',
    title: '执行',
    description: '运行超时以及 Java 或 Maven 路径。',
    fields: [
      {
        path: ['execution', 'timeout'],
        label: '执行超时',
        description: '整体超时时间，单位为秒。',
        kind: 'number',
        step: '1',
      },
      {
        path: ['execution', 'test_timeout'],
        label: '测试超时',
        description: '测试执行超时时间，单位为秒。',
        kind: 'number',
        step: '1',
      },
      {
        path: ['execution', 'coverage_timeout'],
        label: '覆盖率超时',
        description: '收集覆盖率的超时时间，单位为秒。',
        kind: 'number',
        step: '1',
      },
      {
        path: ['execution', 'max_retries'],
        label: '最大重试次数',
        description: '执行失败后的重试次数。',
        kind: 'number',
        step: '1',
      },
      {
        path: ['execution', 'runtime_java_home'],
        label: '运行时 Java 目录',
        description: 'COMET-L 运行时使用的 JDK。',
        kind: 'text',
      },
      {
        path: ['execution', 'target_java_home'],
        label: '目标项目 Java 目录',
        description: '目标项目使用的 JDK。',
        kind: 'text',
      },
      {
        path: ['execution', 'maven_home'],
        label: 'Maven 目录',
        description: '可选的 Maven 安装路径。',
        kind: 'text',
      },
    ],
  },
  {
    key: 'evolution',
    title: '演化',
    description: '迭代限制、停止规则和质量目标。',
    fields: [
      {
        path: ['evolution', 'max_iterations'],
        label: '最大迭代次数',
        description: '规划器最大迭代次数。',
        kind: 'number',
        step: '1',
      },
      {
        path: ['evolution', 'min_improvement_threshold'],
        label: '最小改进阈值',
        description: '绝对改进阈值。',
        kind: 'number',
        step: '0.01',
      },
      {
        path: ['evolution', 'budget_llm_calls'],
        label: 'LLM 调用预算',
        description: 'LLM 调用总预算。',
        kind: 'number',
        step: '1',
      },
      {
        path: ['evolution', 'stop_on_no_improvement_rounds'],
        label: '停滞轮次上限',
        description: '连续多轮无改进后停止。',
        kind: 'number',
        step: '1',
      },
      {
        path: ['evolution', 'excellent_mutation_score'],
        label: '优秀变异分数',
        description: '提前停止的变异分数阈值。',
        kind: 'number',
        step: '0.01',
      },
      {
        path: ['evolution', 'excellent_line_coverage'],
        label: '优秀行覆盖率',
        description: '提前停止的行覆盖率阈值。',
        kind: 'number',
        step: '0.01',
      },
      {
        path: ['evolution', 'excellent_branch_coverage'],
        label: '优秀分支覆盖率',
        description: '提前停止的分支覆盖率阈值。',
        kind: 'number',
        step: '0.01',
      },
      {
        path: ['evolution', 'min_method_lines'],
        label: '最小方法行数',
        description: '跳过小于该大小的方法。',
        kind: 'number',
        step: '1',
      },
    ],
  },
  {
    key: 'knowledge',
    title: '知识库',
    description: 'RAG、嵌入和检索行为。',
    fields: [
      {
        path: ['knowledge', 'enabled'],
        label: '启用知识库',
        description: '启用 RAG 知识检索。',
        kind: 'boolean',
      },
      {
        path: ['knowledge', 'enable_dynamic_update'],
        label: '动态更新',
        description: '允许运行期间更新知识库。',
        kind: 'boolean',
      },
      {
        path: ['knowledge', 'pattern_confidence_threshold'],
        label: '模式置信度阈值',
        description: '接受模式所需的最低置信度。',
        kind: 'number',
        step: '0.01',
      },
      {
        path: ['knowledge', 'contract_extraction_enabled'],
        label: '启用契约提取',
        description: '从源代码提取契约。',
        kind: 'boolean',
      },
      {
        path: ['knowledge', 'embedding', 'base_url'],
        label: '嵌入基础 URL',
        description: '嵌入 API 基础 URL。',
        kind: 'text',
      },
      {
        path: ['knowledge', 'embedding', 'api_key'],
        label: '嵌入 API 密钥',
        description: '可选的嵌入凭证。',
        kind: 'password',
      },
      {
        path: ['knowledge', 'embedding', 'model'],
        label: '嵌入模型',
        description: '嵌入模型名称。',
        kind: 'text',
      },
      {
        path: ['knowledge', 'embedding', 'batch_size'],
        label: '嵌入批大小',
        description: '嵌入请求的批大小。',
        kind: 'number',
        step: '1',
      },
      {
        path: ['knowledge', 'retrieval', 'top_k'],
        label: '检索 Top K',
        description: '每次查询检索的文档数量。',
        kind: 'number',
        step: '1',
      },
      {
        path: ['knowledge', 'retrieval', 'score_threshold'],
        label: '检索分数阈值',
        description: '最小语义匹配分数。',
        kind: 'number',
        step: '0.01',
      },
    ],
  },
  {
    key: 'logging',
    title: '日志',
    description: '运行日志级别和文件目标。',
    fields: [
      {
        path: ['logging', 'level'],
        label: '日志级别',
        description: '应用日志级别。',
        kind: 'text',
      },
      {
        path: ['logging', 'file'],
        label: '日志文件',
        description: '日志文件名或路径。',
        kind: 'text',
      },
    ],
  },
  {
    key: 'preprocessing',
    title: '预处理',
    description: '主循环开始前的并行预处理。',
    fields: [
      {
        path: ['preprocessing', 'enabled'],
        label: '启用预处理',
        description: '启用预处理阶段。',
        kind: 'boolean',
      },
      {
        path: ['preprocessing', 'max_workers'],
        label: '最大工作线程数',
        description: '可选的预处理工作线程数量。',
        kind: 'number',
        step: '1',
      },
      {
        path: ['preprocessing', 'timeout_per_method'],
        label: '单方法超时',
        description: '每个方法的超时时间，单位为秒。',
        kind: 'number',
        step: '1',
      },
    ],
  },
  {
    key: 'formatting',
    title: '格式化',
    description: '生成的 Java 代码格式化行为。',
    fields: [
      {
        path: ['formatting', 'enabled'],
        label: '启用格式化',
        description: '格式化生成的 Java 代码。',
        kind: 'boolean',
      },
      {
        path: ['formatting', 'style'],
        label: '格式化风格',
        description: '格式化器风格名称。',
        kind: 'text',
      },
    ],
  },
  {
    key: 'agent',
    title: 'Agent',
    description: '并行 Agent 执行设置。',
    fields: [
      {
        path: ['agent', 'parallel', 'enabled'],
        label: '启用并行模式',
        description: '启用并行 Agent 模式。',
        kind: 'boolean',
      },
      {
        path: ['agent', 'parallel', 'max_parallel_targets'],
        label: '最大并行目标数',
        description: '并发目标数量。',
        kind: 'number',
        step: '1',
      },
      {
        path: ['agent', 'parallel', 'max_eval_workers'],
        label: '最大评估工作线程数',
        description: '并发评估变异体的工作线程数。',
        kind: 'number',
        step: '1',
      },
      {
        path: ['agent', 'parallel', 'timeout_per_target'],
        label: '单目标超时',
        description: '每个目标的超时时间，单位为秒。',
        kind: 'number',
        step: '1',
      },
    ],
  },
];

export const EXAMPLE_PROJECTS = [
  { label: '计算器示例', path: 'examples/calculator-demo' },
  { label: 'Mockito 示例', path: 'examples/mockito-demo' },
  { label: '多文件示例', path: 'examples/multi-file-demo' },
];
