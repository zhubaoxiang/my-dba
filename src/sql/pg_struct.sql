-- 创建角色与库

-- ============================================================
-- 全量表结构（新建库执行本文件即可）
--
-- 已有库的结构变更**不要改这里**，请追加到同目录的 patch.sql，
-- 并在本文件同步维护对应的建表语句，保持「新库一次到位」。
--
-- 禁止使用 Django migration。
--
-- 模块：数据源纳管与元数据采集（openspec: add-datasource-catalog）
-- ============================================================

-- 被纳管的数据库数据源
CREATE TABLE IF NOT EXISTS datasource (
    id            serial       PRIMARY KEY,
    name          varchar(64)  NOT NULL,
    db_type       smallint     NOT NULL,
    host          varchar(128) NOT NULL,
    port          integer      NOT NULL,
    db_name       varchar(128) NOT NULL,
    username      varchar(64)  NOT NULL,
    password      varchar(512) NOT NULL,
    description   varchar(255) NOT NULL DEFAULT '',
    is_enabled    boolean      NOT NULL DEFAULT true,
    create_time   timestamp    NOT NULL DEFAULT now(),
    update_time   timestamp    NOT NULL DEFAULT now(),
    creator       varchar(32)  NOT NULL DEFAULT '',
    is_deleted    boolean      NOT NULL DEFAULT false
);

COMMENT ON TABLE datasource IS '被纳管的数据库数据源';
COMMENT ON COLUMN datasource.db_type IS '数据库类型：1=PostgreSQL 2=MySQL';
COMMENT ON COLUMN datasource.password IS '可逆加密后的密码，解密见 utils/crypto.py';

-- 名称在未删除范围内唯一
CREATE UNIQUE INDEX IF NOT EXISTS uk_datasource_name ON datasource (name) WHERE is_deleted = false;

-- 一次采集产生的元数据快照，生成后不可变
CREATE TABLE IF NOT EXISTS metadata_snapshot (
    id               serial      PRIMARY KEY,
    datasource_id    integer     NOT NULL REFERENCES datasource (id),
    database_version varchar(128) NOT NULL DEFAULT '',
    schema_count     integer     NOT NULL DEFAULT 0,
    table_count      integer     NOT NULL DEFAULT 0,
    collect_time     timestamp   NOT NULL DEFAULT now(),
    raw_data         jsonb       NOT NULL DEFAULT '{}'::jsonb,
    unavailable      jsonb       NOT NULL DEFAULT '[]'::jsonb,
    create_time      timestamp   NOT NULL DEFAULT now(),
    update_time      timestamp   NOT NULL DEFAULT now(),
    creator          varchar(32) NOT NULL DEFAULT '',
    is_deleted       boolean     NOT NULL DEFAULT false
);

COMMENT ON TABLE metadata_snapshot IS '元数据采集快照，每次采集生成一条，不可变';
COMMENT ON COLUMN metadata_snapshot.raw_data IS '原始采集结果（模式/表/列/索引/约束/体积）';
COMMENT ON COLUMN metadata_snapshot.unavailable IS '因权限或版本原因未能采集的项，分析时据此跳过相关规则';

CREATE INDEX IF NOT EXISTS idx_metadata_snapshot_datasource ON metadata_snapshot (datasource_id, collect_time DESC);

-- 元数据采集任务
CREATE TABLE IF NOT EXISTS collect_task (
    id            serial        PRIMARY KEY,
    datasource_id integer       NOT NULL REFERENCES datasource (id),
    snapshot_id   integer       REFERENCES metadata_snapshot (id),
    status        smallint      NOT NULL DEFAULT 1,
    start_time    timestamp,
    end_time      timestamp,
    fail_reason   varchar(1024) NOT NULL DEFAULT '',
    create_time   timestamp     NOT NULL DEFAULT now(),
    update_time   timestamp     NOT NULL DEFAULT now(),
    creator       varchar(32)   NOT NULL DEFAULT '',
    is_deleted    boolean       NOT NULL DEFAULT false
);

COMMENT ON TABLE collect_task IS '元数据采集任务';
COMMENT ON COLUMN collect_task.status IS '状态：1=待执行 2=执行中 3=成功 4=失败';

CREATE INDEX IF NOT EXISTS idx_collect_task_datasource ON collect_task (datasource_id, id DESC);
-- snapshot_id 外键的前导索引（由本模块的分析规则发现缺失后补齐）
CREATE INDEX IF NOT EXISTS idx_collect_task_snapshot ON collect_task (snapshot_id);

-- 库表健康问题
CREATE TABLE IF NOT EXISTS catalog_issue (
    id            serial        PRIMARY KEY,
    datasource_id integer       NOT NULL REFERENCES datasource (id),
    snapshot_id   integer       NOT NULL REFERENCES metadata_snapshot (id),
    issue_level   smallint      NOT NULL,
    rule_code     varchar(64)   NOT NULL,
    rule_name     varchar(128)  NOT NULL DEFAULT '',
    object_level  smallint      NOT NULL,
    target        varchar(512)  NOT NULL DEFAULT '',
    schema_name   varchar(128)  NOT NULL DEFAULT '',
    table_name    varchar(128)  NOT NULL DEFAULT '',
    column_name   varchar(128)  NOT NULL DEFAULT '',
    description   varchar(1024) NOT NULL DEFAULT '',
    suggestion    varchar(1024) NOT NULL DEFAULT '',
    create_time   timestamp     NOT NULL DEFAULT now(),
    update_time   timestamp     NOT NULL DEFAULT now(),
    creator       varchar(32)   NOT NULL DEFAULT '',
    is_deleted    boolean       NOT NULL DEFAULT false
);

COMMENT ON TABLE catalog_issue IS '库表健康问题清单';
COMMENT ON COLUMN catalog_issue.issue_level IS '严重级别：1=高 2=中 3=低';
COMMENT ON COLUMN catalog_issue.rule_code IS '产出该问题的规则标识，规则清单见 apps/datasource/rules/definitions.py';
COMMENT ON COLUMN catalog_issue.rule_name IS '产出时的规则名称快照，规则改名或停用后历史问题仍可正确展示';
COMMENT ON COLUMN catalog_issue.object_level IS '适用层级：1=库 2=模式 3=表 4=列';
COMMENT ON COLUMN catalog_issue.schema_name IS '所在模式名，供非表级问题定位';

CREATE INDEX IF NOT EXISTS idx_catalog_issue_snapshot ON catalog_issue (snapshot_id, issue_level);
-- datasource_id 外键的前导索引（由本模块的分析规则发现缺失后补齐）
CREATE INDEX IF NOT EXISTS idx_catalog_issue_datasource ON catalog_issue (datasource_id);

-- 分析规则的可覆盖项（规则清单与默认值来自代码声明，本表只存运行时可改的部分）
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