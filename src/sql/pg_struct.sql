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


-- ============================================================
-- 知识问答（openspec: add-rag-knowledge-qa）
-- 模型接入配置 / 文档知识库 / 问答会话
-- ============================================================

-- 向量不存本库，由独立的 Qdrant 服务承载（见 design.md D1）。
-- 本库只存文档与块的正文，是可审计的事实来源；Qdrant 侧是可随时重建的索引。

-- 大模型接入配置（API Key 可逆加密，解密见 utils/crypto.py）
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

COMMENT ON TABLE llm_provider IS '大模型接入配置，一条记录只承载一种用途（对话或嵌入）';
COMMENT ON COLUMN llm_provider.model_type IS '用途：1=对话模型 2=嵌入模型；两类各自独立选一条生效';
COMMENT ON COLUMN llm_provider.api_key IS '可逆加密后的 API Key，任何接口响应与日志都不得输出';
COMMENT ON COLUMN llm_provider.is_active IS '当前生效配置；每类用途在未删除范围内至多一条为 true';

CREATE UNIQUE INDEX IF NOT EXISTS uk_llm_provider_name ON llm_provider (name) WHERE is_deleted = false;
-- 每类用途至多一条生效，由部分唯一索引在数据库层兜底（按 model_type 分别约束）
CREATE UNIQUE INDEX IF NOT EXISTS uk_llm_provider_active ON llm_provider (model_type) WHERE is_deleted = false AND is_active;

-- 知识库
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

-- 知识库中的文档
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
COMMENT ON COLUMN kb_document.source_type IS '来源类型：1=上传文件 2=网页链接';
COMMENT ON COLUMN kb_document.status IS '状态：1=待处理 2=处理中 3=成功 4=失败';
COMMENT ON COLUMN kb_document.content_hash IS '内容指纹，用于识别重复来源';

CREATE INDEX IF NOT EXISTS idx_kb_document_kb ON kb_document (knowledge_base_id, id DESC);
CREATE INDEX IF NOT EXISTS idx_kb_document_hash ON kb_document (knowledge_base_id, content_hash);

-- 文档切分后的块（正文在此，Qdrant 只存向量与检索过滤字段）
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
COMMENT ON COLUMN kb_chunk.heading_path IS '块所属的标题路径，供回答时展示上下文';

CREATE INDEX IF NOT EXISTS idx_kb_chunk_kb ON kb_chunk (knowledge_base_id);
CREATE INDEX IF NOT EXISTS idx_kb_chunk_document ON kb_chunk (document_id, ordinal);

-- 问答会话
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

CREATE INDEX IF NOT EXISTS idx_qa_session_id ON qa_session (id DESC);

-- 会话中的消息
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
COMMENT ON COLUMN qa_message.role IS '角色：1=提问 2=回答';
COMMENT ON COLUMN qa_message.sources IS '回答引用的来源，须对本次检索实际返回的片段';
COMMENT ON COLUMN qa_message.tools_used IS '本次回答实际调用过的工具';
COMMENT ON COLUMN qa_message.is_fallback IS '是否由通用模型回退产生；回退不得静默';

CREATE INDEX IF NOT EXISTS idx_qa_message_session ON qa_message (session_id, id);