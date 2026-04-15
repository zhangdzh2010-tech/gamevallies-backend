import { TIMEOUT_CONFIG_CATALOG_BY_KEY } from '../game/catalogs/timeout-catalog';

type TimeoutSectionKind = 'flow' | 'extra' | 'infra';

interface TimeoutRecordLike {
  id?: string | null;
  configKey?: string | null;
  configValue?: string | null;
  createdAt?: Date | null;
  updatedAt?: Date | null;
}

interface VisibleTimeoutUpdate {
  key: string;
  value: string;
}

interface VisibleTimeoutSectionMeta {
  id: string;
  kind: TimeoutSectionKind;
  order: number;
  tag: string;
  title: string;
  description: string;
}

interface VisibleTimeoutCatalogEntry {
  key: string;
  displayName: string;
  description: string;
  unit: 's';
  valueType: 'int';
  optional?: boolean;
  itemOrder: number;
  section: VisibleTimeoutSectionMeta;
  backingKeys: string[];
  anchorKey: string;
  readCurrentValue: (reader: TimeoutValueReader) => number;
  readDefaultValue: (reader: TimeoutValueReader) => number;
  buildUpdates: (value: number) => VisibleTimeoutUpdate[];
}

interface TimeoutValueReader {
  current(key: string): number;
  default(key: string): number;
}

const SECTION_CREATE_ENTRY: VisibleTimeoutSectionMeta = {
  id: 'business_create_entry',
  kind: 'flow',
  order: 10,
  tag: 'Create Entry',
  title: '创建入口',
  description: '只保留会直接影响创建会话等待感和规格冻结的超时项。',
};

const SECTION_CREATE_MAINLINE: VisibleTimeoutSectionMeta = {
  id: 'business_create_mainline',
  kind: 'flow',
  order: 20,
  tag: 'Create Mainline',
  title: '首次生成主链路',
  description: '影响首次生成成功率、时长和成本的关键超时项。',
};

const SECTION_ITERATE: VisibleTimeoutSectionMeta = {
  id: 'business_iterate',
  kind: 'extra',
  order: 30,
  tag: 'Iterate',
  title: '迭代链路',
  description: '只保留用户最容易感知的迭代生成超时，内部分类和细项默认即可。',
};

const SECTION_GLOBAL: VisibleTimeoutSectionMeta = {
  id: 'business_global',
  kind: 'infra',
  order: 40,
  tag: 'Global',
  title: '全局守卫',
  description: '全流程总超时作为唯一的全局总闸门，其他 transport / relay / cache 细项保持默认。',
};

const BUSINESS_TIMEOUT_CATALOG: VisibleTimeoutCatalogEntry[] = [
  {
    key: 'timeout.business.creation_session_s',
    displayName: '创建会话超时',
    description: '控制 H5 创建会话初始化与 analyze-turn 外层等待，适合调用户首屏等待感。',
    unit: 's',
    valueType: 'int',
    optional: true,
    itemOrder: 10,
    section: SECTION_CREATE_ENTRY,
    backingKeys: [
      'timeout.game_service.creation_session_init_ms',
      'timeout.game_service.expand_prompt_request_ms',
    ],
    anchorKey: 'timeout.game_service.creation_session_init_ms',
    readCurrentValue: (reader) => Math.round(reader.current('timeout.game_service.creation_session_init_ms') / 1000),
    readDefaultValue: (reader) => Math.round(reader.default('timeout.game_service.creation_session_init_ms') / 1000),
    buildUpdates: (value) => {
      const sessionSeconds = clampInt(value, 15, 180);
      const analyzeTurnSeconds = clampInt(sessionSeconds - 15, 10, sessionSeconds);
      return [
        {
          key: 'timeout.game_service.creation_session_init_ms',
          value: String(sessionSeconds * 1000),
        },
        {
          key: 'timeout.game_service.expand_prompt_request_ms',
          value: String(analyzeTurnSeconds * 1000),
        },
      ];
    },
  },
  {
    key: 'timeout.business.intent_parse_s',
    displayName: '意图解析超时',
    description: '控制 create / iterate 进入结构化规格前的意图解析总预算。',
    unit: 's',
    valueType: 'int',
    itemOrder: 20,
    section: SECTION_CREATE_ENTRY,
    backingKeys: [
      'timeout.ai_engine.intent_parse.request_s',
      'timeout.ai_engine.intent_parse.overall_s',
    ],
    anchorKey: 'timeout.ai_engine.intent_parse.overall_s',
    readCurrentValue: (reader) => reader.current('timeout.ai_engine.intent_parse.overall_s'),
    readDefaultValue: (reader) => reader.default('timeout.ai_engine.intent_parse.overall_s'),
    buildUpdates: (value) => {
      const overall = clampInt(value, 30, 300);
      return [
        {
          key: 'timeout.ai_engine.intent_parse.request_s',
          value: String(clampInt(Math.round(overall * 0.5), 15, overall)),
        },
        {
          key: 'timeout.ai_engine.intent_parse.overall_s',
          value: String(overall),
        },
      ];
    },
  },
  {
    key: 'timeout.business.create_generation_s',
    displayName: '创建生成超时',
    description: '控制首次生成阶段的主 LLM 长请求预算，是 create 成功率和成本的关键项。',
    unit: 's',
    valueType: 'int',
    itemOrder: 10,
    section: SECTION_CREATE_MAINLINE,
    backingKeys: ['timeout.ai_engine.llm_long_generation_s'],
    anchorKey: 'timeout.ai_engine.llm_long_generation_s',
    readCurrentValue: (reader) => reader.current('timeout.ai_engine.llm_long_generation_s'),
    readDefaultValue: (reader) => reader.default('timeout.ai_engine.llm_long_generation_s'),
    buildUpdates: (value) => [
      {
        key: 'timeout.ai_engine.llm_long_generation_s',
        value: String(clampInt(value, 60, 600)),
      },
    ],
  },
  {
    key: 'timeout.business.qa_repair_s',
    displayName: 'QA 自动修复超时',
    description: '控制 QA 修复阶段的总预算，快修分支会按比例自动推导。',
    unit: 's',
    valueType: 'int',
    itemOrder: 20,
    section: SECTION_CREATE_MAINLINE,
    backingKeys: [
      'timeout.ai_engine.qa_repair_s',
      'timeout.ai_engine.qa_fast_repair_s',
    ],
    anchorKey: 'timeout.ai_engine.qa_repair_s',
    readCurrentValue: (reader) => reader.current('timeout.ai_engine.qa_repair_s'),
    readDefaultValue: (reader) => reader.default('timeout.ai_engine.qa_repair_s'),
    buildUpdates: (value) => {
      const repair = clampInt(value, 60, 360);
      return [
        {
          key: 'timeout.ai_engine.qa_repair_s',
          value: String(repair),
        },
        {
          key: 'timeout.ai_engine.qa_fast_repair_s',
          value: String(clampInt(Math.round(repair * (2 / 3)), 30, repair)),
        },
      ];
    },
  },
  {
    key: 'timeout.business.runtime_qa_s',
    displayName: '运行时验收超时',
    description: '控制运行时验收的总上限；phase、bonus、wait ratio 等内部细项维持默认策略。',
    unit: 's',
    valueType: 'int',
    itemOrder: 30,
    section: SECTION_CREATE_MAINLINE,
    backingKeys: [
      'timeout.ai_engine.runtime_qa.base_s',
      'timeout.ai_engine.runtime_qa.max_s',
    ],
    anchorKey: 'timeout.ai_engine.runtime_qa.max_s',
    readCurrentValue: (reader) => reader.current('timeout.ai_engine.runtime_qa.max_s'),
    readDefaultValue: (reader) => reader.default('timeout.ai_engine.runtime_qa.max_s'),
    buildUpdates: (value) => {
      const max = clampInt(value, 10, 120);
      const base = clampFloat(max * (8 / 30), 3, max, 2);
      return [
        {
          key: 'timeout.ai_engine.runtime_qa.base_s',
          value: formatNumber(base),
        },
        {
          key: 'timeout.ai_engine.runtime_qa.max_s',
          value: String(max),
        },
      ];
    },
  },
  {
    key: 'timeout.business.iterate_generation_s',
    displayName: '迭代生成超时',
    description: '控制 iterate 的改写阶段总预算，内部自动映射到参数调整、元素修改和机制改写。',
    unit: 's',
    valueType: 'int',
    itemOrder: 10,
    section: SECTION_ITERATE,
    backingKeys: [
      'timeout.ai_engine.iterate.param_adjust_request_s',
      'timeout.ai_engine.iterate.param_adjust_overall_s',
      'timeout.ai_engine.iterate.element_change_request_s',
      'timeout.ai_engine.iterate.element_change_overall_s',
      'timeout.ai_engine.iterate.mechanic_change_request_s',
      'timeout.ai_engine.iterate.mechanic_change_overall_s',
    ],
    anchorKey: 'timeout.ai_engine.iterate.element_change_overall_s',
    readCurrentValue: (reader) => reader.current('timeout.ai_engine.iterate.element_change_overall_s'),
    readDefaultValue: (reader) => reader.default('timeout.ai_engine.iterate.element_change_overall_s'),
    buildUpdates: (value) => {
      const iterate = clampInt(value, 90, 480);
      return [
        {
          key: 'timeout.ai_engine.iterate.param_adjust_request_s',
          value: String(clampInt(Math.round(iterate * 0.375), 30, iterate)),
        },
        {
          key: 'timeout.ai_engine.iterate.param_adjust_overall_s',
          value: String(clampInt(Math.round(iterate * 0.75), 30, iterate)),
        },
        {
          key: 'timeout.ai_engine.iterate.element_change_request_s',
          value: String(clampInt(Math.round(iterate * 0.5), 30, iterate)),
        },
        {
          key: 'timeout.ai_engine.iterate.element_change_overall_s',
          value: String(iterate),
        },
        {
          key: 'timeout.ai_engine.iterate.mechanic_change_request_s',
          value: String(clampInt(Math.round(iterate * 0.5625), 30, iterate)),
        },
        {
          key: 'timeout.ai_engine.iterate.mechanic_change_overall_s',
          value: String(clampInt(Math.round(iterate * 1.125), 30, 540)),
        },
      ];
    },
  },
  {
    key: 'timeout.business.pipeline_total_s',
    displayName: '全流程总超时',
    description: '控制 create / iterate 的全流程总预算，并同步作为 V2 主链路的最小超时下限。',
    unit: 's',
    valueType: 'int',
    itemOrder: 10,
    section: SECTION_GLOBAL,
    backingKeys: [
      'timeout.pipeline.default_s',
      'timeout.pipeline.v2_min_s',
    ],
    anchorKey: 'timeout.pipeline.default_s',
    readCurrentValue: (reader) => reader.current('timeout.pipeline.default_s'),
    readDefaultValue: (reader) => reader.default('timeout.pipeline.default_s'),
    buildUpdates: (value) => {
      const pipeline = clampInt(value, 60, 3600);
      return [
        {
          key: 'timeout.pipeline.default_s',
          value: String(pipeline),
        },
        {
          key: 'timeout.pipeline.v2_min_s',
          value: String(pipeline),
        },
      ];
    },
  },
];

export const BUSINESS_TIMEOUT_CATALOG_BY_KEY = new Map(
  BUSINESS_TIMEOUT_CATALOG.map((entry) => [entry.key, entry]),
);

export function listBusinessTimeoutConfigs(configs: TimeoutRecordLike[]) {
  const configMap = new Map(
    (configs || []).map((config) => [String(config.configKey || ''), config]),
  );
  const reader: TimeoutValueReader = {
    current: (key) => readNumberValue(configMap, key, 'current'),
    default: (key) => readNumberValue(configMap, key, 'default'),
  };

  return BUSINESS_TIMEOUT_CATALOG.map((entry) => {
    const currentValue = entry.readCurrentValue(reader);
    const defaultValue = entry.readDefaultValue(reader);
    const existingRows = entry.backingKeys
      .map((key) => configMap.get(key))
      .filter((row): row is TimeoutRecordLike => Boolean(row));
    const hasOverride = entry.backingKeys.some((key) => {
      const current = readNumberValue(configMap, key, 'current');
      const fallback = readNumberValue(configMap, key, 'default');
      return current !== fallback;
    });

    return {
      id: `business:${entry.key}`,
      configKey: entry.key,
      configValue: formatNumber(currentValue),
      description: entry.description,
      category: 'timeout',
      createdAt: pickDate(existingRows, 'createdAt', 'min'),
      updatedAt: pickDate(existingRows, 'updatedAt', 'max'),
      defaultValue: formatNumber(defaultValue),
      unit: entry.unit,
      valueType: entry.valueType,
      service: 'shared',
      group: entry.section.id,
      sectionId: entry.section.id,
      sectionKind: entry.section.kind,
      sectionOrder: entry.section.order,
      sectionTag: entry.section.tag,
      sectionTitle: entry.section.title,
      sectionDescription: entry.section.description,
      itemOrder: entry.itemOrder,
      source: hasOverride ? 'db' : 'catalog',
      isDefault: !hasOverride,
      displayName: entry.displayName,
      optional: Boolean(entry.optional),
      backingKeys: [...entry.backingKeys],
    };
  });
}

export function isBusinessTimeoutConfigKey(key: string): boolean {
  return BUSINESS_TIMEOUT_CATALOG_BY_KEY.has(key);
}

export function getBusinessTimeoutConfig(key: string, configs: TimeoutRecordLike[]) {
  return listBusinessTimeoutConfigs(configs).find((entry) => entry.configKey === key) || null;
}

export function expandBusinessTimeoutConfigUpdates(key: string, rawValue: string): VisibleTimeoutUpdate[] {
  const entry = BUSINESS_TIMEOUT_CATALOG_BY_KEY.get(key);
  if (!entry) {
    throw new Error(`Unknown business timeout key: ${key}`);
  }
  const numericValue = Number.parseInt(String(rawValue), 10);
  if (!Number.isFinite(numericValue)) {
    throw new Error(`Invalid timeout value for ${key}: ${rawValue}`);
  }
  return entry.buildUpdates(numericValue);
}

function readNumberValue(
  configMap: Map<string, TimeoutRecordLike>,
  key: string,
  mode: 'current' | 'default',
): number {
  const catalogEntry = TIMEOUT_CONFIG_CATALOG_BY_KEY.get(key);
  if (!catalogEntry) {
    throw new Error(`Unknown timeout catalog key: ${key}`);
  }
  const rawValue = mode === 'current'
    ? (configMap.get(key)?.configValue ?? catalogEntry.defaultValue)
    : catalogEntry.defaultValue;
  const parsed = catalogEntry.valueType === 'float'
    ? Number.parseFloat(String(rawValue))
    : Number.parseInt(String(rawValue), 10);
  const fallback = catalogEntry.valueType === 'float'
    ? Number.parseFloat(catalogEntry.defaultValue)
    : Number.parseInt(catalogEntry.defaultValue, 10);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function pickDate(
  rows: TimeoutRecordLike[],
  field: 'createdAt' | 'updatedAt',
  mode: 'min' | 'max',
): Date | null {
  const timestamps = rows
    .map((row) => row[field])
    .filter((value): value is Date => value instanceof Date)
    .map((value) => value.getTime());
  if (!timestamps.length) {
    return null;
  }
  const timestamp = mode === 'min'
    ? Math.min(...timestamps)
    : Math.max(...timestamps);
  return new Date(timestamp);
}

function clampInt(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, Math.round(value)));
}

function clampFloat(value: number, min: number, max: number, precision = 2): number {
  const clamped = Math.min(max, Math.max(min, value));
  return Number(clamped.toFixed(precision));
}

function formatNumber(value: number): string {
  if (!Number.isFinite(value)) {
    return '';
  }
  if (Number.isInteger(value)) {
    return String(value);
  }
  return String(Number(value.toFixed(2)));
}
