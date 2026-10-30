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
-- 保留 90 天，超期按项目规则软删除（is_deleted），不做物理删除。
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
