-- ============================================================
-- 增量补丁脚本
--
-- 用途：给**已有数据的库**做结构变更。新建库直接执行 pg_struct.sql 即可，不需要本文件。
--
-- 约定：
--   * 本文件只保留**尚未在所有环境落地**的变更。某个发布已全量执行后，
--     把对应条目从这里删除；历史版本仍可在 git 历史与 openspec/changes/archive 中查到
--   * 语句写成幂等（ADD COLUMN IF NOT EXISTS / DROP COLUMN IF EXISTS），重复执行安全
--   * 破坏性数据操作（DELETE / UPDATE / 改类型）只写在注释里，由人工确认后手动执行
--   * 禁止使用 Django migration
--
-- 基线：2026-10-24。此前的变更已固化到 pg_struct.sql；
-- 更早的库请使用对应版本 tag 的 patch.sql，或直接用 pg_struct.sql 重建。
-- ============================================================


-- ============================================================
-- 2026-10-24  add-datasource-qa
-- 问答会话记住所选数据源，重新打开会话时恢复。
-- 只存 id，不建到 datasource 的外键：跨模块边界通过 service 维持，
-- 数据源软删除后会话记录仍保留，提问时再判存在性。
-- ============================================================

ALTER TABLE qa_session ADD COLUMN IF NOT EXISTS datasource_id integer NULL;

COMMENT ON COLUMN qa_session.datasource_id IS '默认数据源；仅存 id，不建跨模块外键';


-- ============================================================
-- 2026-10-30  add-llm-usage-stats
-- 模型调用用量：每次调用一行，查询时聚合；token 列可空，不记录正文。
-- 保留 90 天，超期物理删除；观测数据不保留已删除记录。
-- ============================================================

CREATE TABLE IF NOT EXISTS llm_usage (
    id                serial       PRIMARY KEY,
    username          varchar(150) NOT NULL DEFAULT '',
    feature           smallint     NOT NULL DEFAULT 8,
    call_type         smallint     NOT NULL DEFAULT 1,
    provider_id       integer      NULL,
    model_name        varchar(128) NOT NULL DEFAULT '',
    prompt_tokens     integer      NULL,
    completion_tokens integer      NULL,
    total_tokens      integer      NULL,
    units             integer      NULL,
    latency_ms        integer      NOT NULL DEFAULT 0,
    success           boolean      NOT NULL DEFAULT true,
    error_code        varchar(32)  NOT NULL DEFAULT '',
    request_id        varchar(36)  NOT NULL DEFAULT '',
    create_time       timestamp    NOT NULL DEFAULT now(),
    update_time       timestamp    NOT NULL DEFAULT now(),
    creator           varchar(32)  NOT NULL DEFAULT '',
    is_deleted        boolean      NOT NULL DEFAULT false
);

COMMENT ON TABLE llm_usage IS '模型调用用量；每次调用一行，不记录 prompt 与回答正文';
COMMENT ON COLUMN llm_usage.username IS '发起调用的用户；后台任务记为 system';
COMMENT ON COLUMN llm_usage.feature IS '来源功能：1=问答 2=SQL生成 3=SQL解读 4=DDL评审 5=知识库摄入 6=连通性测试 7=后台任务 8=其他';
COMMENT ON COLUMN llm_usage.call_type IS '调用类型：1=对话 2=嵌入';
COMMENT ON COLUMN llm_usage.prompt_tokens IS '输入 token；网关未返回 usage 时为 NULL，不估算';
COMMENT ON COLUMN llm_usage.units IS '嵌入调用处理的文本块数；对话调用为空';
COMMENT ON COLUMN llm_usage.request_id IS '一次用户请求内多次模型调用共享同一标识';

CREATE INDEX IF NOT EXISTS idx_llm_usage_create_time ON llm_usage (create_time);
CREATE INDEX IF NOT EXISTS idx_llm_usage_username ON llm_usage (username, create_time);
CREATE INDEX IF NOT EXISTS idx_llm_usage_feature ON llm_usage (feature, create_time);

-- ============================================================
-- 2026-11-02  add-schema-diff
-- 快照与同一数据源上一次采集的结构变更摘要（紧凑计数，不存完整 diff）
-- 空字典表示未记录（历史快照）；不可比时带 reason，不能读成「没有变化」
-- ============================================================

ALTER TABLE metadata_snapshot ADD COLUMN IF NOT EXISTS change_summary jsonb NOT NULL DEFAULT '{}'::jsonb;
COMMENT ON COLUMN metadata_snapshot.change_summary IS '与同一数据源上一次快照的紧凑结构变更摘要；空表示未记录，不可比时带 reason';

-- ============================================================
-- 2026-11-05  add-test-db-provisioning
-- 数据源环境类型与独立预置写账号；测试库预置记录
-- 默认生产：存量数据源全部不可预置，必须管理员显式改为测试
-- ============================================================

ALTER TABLE datasource ADD COLUMN IF NOT EXISTS env_type smallint NOT NULL DEFAULT 1;
ALTER TABLE datasource ADD COLUMN IF NOT EXISTS write_username varchar(64) NOT NULL DEFAULT '';
ALTER TABLE datasource ADD COLUMN IF NOT EXISTS write_password varchar(512) NOT NULL DEFAULT '';
COMMENT ON COLUMN datasource.env_type IS '环境类型：1=生产 2=测试；默认生产，只有测试环境可预置测试库';
COMMENT ON COLUMN datasource.write_username IS '测试库预置写账号；与只读采集账号分离';
COMMENT ON COLUMN datasource.write_password IS '可逆加密后的预置写密码；为空表示未配置';

CREATE TABLE IF NOT EXISTS provisioned_database (
    id            serial        PRIMARY KEY,
    datasource_id integer       NOT NULL REFERENCES datasource (id),
    db_name       varchar(128)  NOT NULL,
    status        smallint      NOT NULL DEFAULT 1,
    tables        jsonb         NOT NULL DEFAULT '[]'::jsonb,
    row_count     integer       NOT NULL DEFAULT 0,
    error         varchar(1024) NOT NULL DEFAULT '',
    create_time   timestamp     NOT NULL DEFAULT now(),
    update_time   timestamp     NOT NULL DEFAULT now(),
    creator       varchar(32)   NOT NULL DEFAULT '',
    is_deleted    boolean       NOT NULL DEFAULT false
);
COMMENT ON TABLE provisioned_database IS '由测试库预置功能创建的独立数据库记录';
COMMENT ON COLUMN provisioned_database.status IS '状态：1=创建中 2=可用 3=失败 4=已删除';
COMMENT ON COLUMN provisioned_database.tables IS '选中的表与每表行数';
COMMENT ON COLUMN provisioned_database.row_count IS '实际写入的总行数';

CREATE UNIQUE INDEX IF NOT EXISTS uk_provisioned_database_name ON provisioned_database (datasource_id, db_name);
CREATE INDEX IF NOT EXISTS idx_provisioned_database_datasource ON provisioned_database (datasource_id, id DESC);
