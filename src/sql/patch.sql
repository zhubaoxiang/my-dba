-- ============================================================
-- 增量补丁脚本
--
-- 用途：给**已有数据的库**做结构变更。新建库直接执行 pg_struct.sql 即可，不需要本文件。
-- 约定：
--   * 每个变更集对应一个 openspec change，按时间追加，不修改历史条目
--   * 语句写成幂等（ADD COLUMN IF NOT EXISTS / DROP COLUMN IF EXISTS），重复执行安全
--   * 破坏性数据操作（DELETE / UPDATE / 改类型）只写在注释里，由人工确认后手动执行
--   * 禁止使用 Django migration
--
-- 执行方式：
--   psql "postgresql://<用户>:<密码>@<主机>:<端口>/<库名>" -f src/sql/patch.sql
-- ============================================================


-- ============================================================
-- 2026-09-22  add-analysis-rule-registry
-- 分析规则改为声明式注册：catalog_issue 以 rule_code 取代 issue_type，
-- 并新增 analysis_rule 存储规则的可覆盖项。
-- ============================================================

-- 1) catalog_issue 补齐新列并移除旧的数字类型列
ALTER TABLE catalog_issue ADD COLUMN IF NOT EXISTS rule_code varchar(64) NOT NULL DEFAULT '';
ALTER TABLE catalog_issue ADD COLUMN IF NOT EXISTS rule_name varchar(128) NOT NULL DEFAULT '';
ALTER TABLE catalog_issue ADD COLUMN IF NOT EXISTS object_level smallint NOT NULL DEFAULT 3;
ALTER TABLE catalog_issue ADD COLUMN IF NOT EXISTS schema_name varchar(128) NOT NULL DEFAULT '';
ALTER TABLE catalog_issue ALTER COLUMN rule_code DROP DEFAULT;
ALTER TABLE catalog_issue ALTER COLUMN object_level DROP DEFAULT;
ALTER TABLE catalog_issue DROP COLUMN IF EXISTS issue_type;

COMMENT ON COLUMN catalog_issue.rule_code IS '产出该问题的规则标识，规则清单见 apps/datasource/rules/definitions.py';
COMMENT ON COLUMN catalog_issue.rule_name IS '产出时的规则名称快照，规则改名或停用后历史问题仍可正确展示';
COMMENT ON COLUMN catalog_issue.object_level IS '适用层级：1=库 2=模式 3=表 4=列';
COMMENT ON COLUMN catalog_issue.schema_name IS '所在模式名，供非表级问题定位';

-- 2) 新增规则注册表
CREATE TABLE IF NOT EXISTS analysis_rule (
    id            serial       PRIMARY KEY,
    code          varchar(64)  NOT NULL,
    name          varchar(128) NOT NULL DEFAULT '',
    description   varchar(512) NOT NULL DEFAULT '',
    level         smallint     NOT NULL,
    object_level  smallint     NOT NULL,
    enabled       boolean      NOT NULL DEFAULT true,
    thresholds    jsonb        NOT NULL DEFAULT '{}'::jsonb,
    create_time   timestamp    NOT NULL DEFAULT now(),
    update_time   timestamp    NOT NULL DEFAULT now(),
    creator       varchar(32)  NOT NULL DEFAULT '',
    is_deleted    boolean      NOT NULL DEFAULT false
);

COMMENT ON TABLE analysis_rule IS '分析规则的可覆盖项，库中无记录时分析使用代码声明的默认值';
COMMENT ON COLUMN analysis_rule.code IS '规则稳定标识，与代码声明中的 RuleDefinition.code 对应';
COMMENT ON COLUMN analysis_rule.level IS '严重级别：1=高 2=中 3=低';
COMMENT ON COLUMN analysis_rule.object_level IS '适用层级：1=库 2=模式 3=表 4=列';
COMMENT ON COLUMN analysis_rule.thresholds IS '该规则的判定阈值，键名与代码声明中的 default_thresholds 一致';

CREATE UNIQUE INDEX IF NOT EXISTS uk_analysis_rule_code ON analysis_rule (code) WHERE is_deleted = false;

-- 3) 清理失效的历史问题记录（人工确认后执行）
--
-- 上面的 ADD COLUMN 会给旧行补上默认值：rule_code = ''、object_level = 3，
-- 这些行找不回对应的规则，属失效数据。本模块此前只有验证数据，确认可清空后执行：
--
--   DELETE FROM catalog_issue;
--
-- 若不清理，它们会以空规则名出现在问题清单里。故意不放在脚本中自动执行。
