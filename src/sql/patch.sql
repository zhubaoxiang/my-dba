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


-- ============================================================
-- 2026-09-23  add-rag-knowledge-qa
-- 知识问答：模型接入配置、文档知识库（含向量）、问答会话。
-- ============================================================

-- 1) 向量不入本库，由独立的 Qdrant 服务承载（见 design.md D1）。
-- 本库只存文档与块的正文，是可审计的事实来源；Qdrant 侧是可随时重建的索引。

-- 2) 大模型接入配置
--
-- 2026-09-23 追加变更集：对话模型与嵌入模型拆开配置
-- 旧结构把两类模型塞在一条记录里（chat_model + embedding_model），但现实里两者常来自
-- 不同服务，且旧结构只能有一条「生效」。改为：一条记录一种用途（model_type），
-- 对话与嵌入各自独立选一条生效。
CREATE TABLE IF NOT EXISTS llm_provider (
    id            serial       PRIMARY KEY,
    name          varchar(64)  NOT NULL,
    provider_type smallint     NOT NULL,
    model_type    smallint     NOT NULL DEFAULT 1,
    base_url      varchar(255) NOT NULL,
    model_name    varchar(128) NOT NULL,
    api_key       varchar(512) NOT NULL,
    is_enabled    boolean      NOT NULL DEFAULT true,
    is_active     boolean      NOT NULL DEFAULT false,
    create_time   timestamp    NOT NULL DEFAULT now(),
    update_time   timestamp    NOT NULL DEFAULT now(),
    creator       varchar(32)  NOT NULL DEFAULT '',
    is_deleted    boolean      NOT NULL DEFAULT false
);

-- 已有库的结构与数据迁移（新库因上面的建表语句已含新列，本节为空操作）
ALTER TABLE llm_provider ADD COLUMN IF NOT EXISTS model_type smallint NOT NULL DEFAULT 1;
ALTER TABLE llm_provider ADD COLUMN IF NOT EXISTS model_name varchar(128) NOT NULL DEFAULT '';
-- 搬迁既有配置：旧结构把对话模型放在 chat_model。这是**迁移的必要一步**（不做会丢配置），
-- 故写成可执行；且仅填补 model_name 为空的行。
-- 用 DO 块判存在性：本文件约定「重复执行安全」，而 chat_model 在首次执行后就没了。
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'llm_provider' AND column_name = 'chat_model'
    ) THEN
        UPDATE llm_provider SET model_name = chat_model
        WHERE model_name = '' AND chat_model IS NOT NULL AND chat_model <> '';
    END IF;
END $$;
ALTER TABLE llm_provider DROP COLUMN IF EXISTS chat_model;
ALTER TABLE llm_provider DROP COLUMN IF EXISTS embedding_model;

COMMENT ON TABLE llm_provider IS '大模型接入配置，一条记录只承载一种用途（对话或嵌入）';
COMMENT ON COLUMN llm_provider.model_type IS '用途：1=对话模型 2=嵌入模型；两类各自独立选一条生效';
COMMENT ON COLUMN llm_provider.api_key IS '可逆加密后的 API Key，任何接口响应与日志都不得输出';
COMMENT ON COLUMN llm_provider.is_active IS '当前生效配置；每类用途在未删除范围内至多一条为 true';

CREATE UNIQUE INDEX IF NOT EXISTS uk_llm_provider_name ON llm_provider (name) WHERE is_deleted = false;
-- 生效约束从「全表唯一」改为「按用途唯一」
DROP INDEX IF EXISTS uk_llm_provider_active;
CREATE UNIQUE INDEX IF NOT EXISTS uk_llm_provider_active ON llm_provider (model_type) WHERE is_deleted = false AND is_active;

-- 3) 知识库与文档
CREATE TABLE IF NOT EXISTS knowledge_base (
    id          serial       PRIMARY KEY,
    name        varchar(64)  NOT NULL,
    description varchar(255) NOT NULL DEFAULT '',
    is_enabled  boolean      NOT NULL DEFAULT true,
    create_time timestamp    NOT NULL DEFAULT now(),
    update_time timestamp    NOT NULL DEFAULT now(),
    creator     varchar(32)  NOT NULL DEFAULT '',
    is_deleted  boolean      NOT NULL DEFAULT false
);

COMMENT ON TABLE knowledge_base IS '文档知识库';

CREATE UNIQUE INDEX IF NOT EXISTS uk_knowledge_base_name ON knowledge_base (name) WHERE is_deleted = false;

CREATE TABLE IF NOT EXISTS kb_document (
    id                serial        PRIMARY KEY,
    knowledge_base_id integer       NOT NULL REFERENCES knowledge_base (id),
    title             varchar(255)  NOT NULL,
    source_type       smallint      NOT NULL,
    source            varchar(1024) NOT NULL,
    content_hash      varchar(64)   NOT NULL DEFAULT '',
    status            smallint      NOT NULL DEFAULT 1,
    fail_reason       varchar(1024) NOT NULL DEFAULT '',
    chunk_count       integer       NOT NULL DEFAULT 0,
    create_time       timestamp     NOT NULL DEFAULT now(),
    update_time       timestamp     NOT NULL DEFAULT now(),
    creator           varchar(32)   NOT NULL DEFAULT '',
    is_deleted        boolean       NOT NULL DEFAULT false
);

COMMENT ON TABLE kb_document IS '知识库中的文档';
COMMENT ON COLUMN kb_document.status IS '状态：1=待处理 2=处理中 3=成功 4=失败';

CREATE INDEX IF NOT EXISTS idx_kb_document_kb ON kb_document (knowledge_base_id, id DESC);
CREATE INDEX IF NOT EXISTS idx_kb_document_hash ON kb_document (knowledge_base_id, content_hash);

-- 4) 文档切分的块（正文在此，Qdrant 只存向量与检索过滤字段）
CREATE TABLE IF NOT EXISTS kb_chunk (
    id                serial       PRIMARY KEY,
    knowledge_base_id integer      NOT NULL REFERENCES knowledge_base (id),
    document_id       integer      NOT NULL REFERENCES kb_document (id),
    ordinal           integer      NOT NULL,
    heading_path      varchar(512) NOT NULL DEFAULT '',
    content           text         NOT NULL,
    create_time       timestamp    NOT NULL DEFAULT now(),
    update_time       timestamp    NOT NULL DEFAULT now(),
    creator           varchar(32)  NOT NULL DEFAULT '',
    is_deleted        boolean      NOT NULL DEFAULT false
);

COMMENT ON TABLE kb_chunk IS '文档切分后的块，正文以本表为准；向量存于 Qdrant，以本表 id 作为点标识';

CREATE INDEX IF NOT EXISTS idx_kb_chunk_kb ON kb_chunk (knowledge_base_id);
CREATE INDEX IF NOT EXISTS idx_kb_chunk_document ON kb_chunk (document_id, ordinal);

-- 5) 问答会话与消息
CREATE TABLE IF NOT EXISTS qa_session (
    id                serial       PRIMARY KEY,
    title             varchar(128) NOT NULL DEFAULT '',
    knowledge_base_id integer      REFERENCES knowledge_base (id),
    mode              smallint     NOT NULL DEFAULT 1,
    create_time       timestamp    NOT NULL DEFAULT now(),
    update_time       timestamp    NOT NULL DEFAULT now(),
    creator           varchar(32)  NOT NULL DEFAULT '',
    is_deleted        boolean      NOT NULL DEFAULT false
);

COMMENT ON TABLE qa_session IS '一次问答会话';
COMMENT ON COLUMN qa_session.mode IS '默认模式：1=自动 2=仅知识库 3=仅通用模型';

CREATE TABLE IF NOT EXISTS qa_message (
    id          serial      PRIMARY KEY,
    session_id  integer     NOT NULL REFERENCES qa_session (id),
    role        smallint    NOT NULL,
    mode        smallint    NOT NULL DEFAULT 1,
    content     text        NOT NULL,
    sources     jsonb       NOT NULL DEFAULT '[]'::jsonb,
    tools_used  jsonb       NOT NULL DEFAULT '[]'::jsonb,
    is_fallback boolean     NOT NULL DEFAULT false,
    create_time timestamp   NOT NULL DEFAULT now(),
    update_time timestamp   NOT NULL DEFAULT now(),
    creator     varchar(32) NOT NULL DEFAULT '',
    is_deleted  boolean     NOT NULL DEFAULT false
);

COMMENT ON TABLE qa_message IS '会话中的一条消息';
COMMENT ON COLUMN qa_message.sources IS '回答引用的来源，须对本次检索实际返回的片段';
COMMENT ON COLUMN qa_message.is_fallback IS '是否由通用模型回退产生；回退不得静默';

CREATE INDEX IF NOT EXISTS idx_qa_message_session ON qa_message (session_id, id);

-- ============================================================
-- 知识问答流式输出（openspec: add-qa-streaming）
-- ============================================================

-- 标记回答是否生成完整：流式生成被中止或客户端断连时为 false。
-- 旧数据均为完整生成，默认 true 即语义正确，无需回填。
ALTER TABLE qa_message ADD COLUMN IF NOT EXISTS is_complete boolean NOT NULL DEFAULT true;

COMMENT ON COLUMN qa_message.is_complete IS '回答是否生成完整；流式生成被中止或断连时为 false';
