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
