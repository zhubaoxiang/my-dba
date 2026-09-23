"""
文档摄入：解析 → 切分 → 嵌入 → 写块 → 写向量

两步写入（先写库、再写 Qdrant）配补偿：任一步失败都不留半成品——
嵌入失败则整份文档不入库；写向量失败则回滚刚写入的块并置文档为失败。
"""

import hashlib
import io
import re

import requests
from bs4 import BeautifulSoup
from django.db import transaction
from pypdf import PdfReader

from apps.knowledge import llm, models, vectorstore
from utils import custom_enum
from utils.configure import CONF_ATTR
from utils.logger import get_logger

LOGGER = get_logger("knowledge.log")

# 支持的上传后缀（网页链接走单独的入口）
SUPPORTED_SUFFIXES = (".md", ".markdown", ".txt", ".text", ".pdf")

# 上传体积上限（字节）。摄入是进程内异步任务，文件内容会随任务一起驻留内存
MAX_UPLOAD_BYTES = 20 * 1024 * 1024

_URL_TIMEOUT = 30

_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_STRIP_TAGS = ("script", "style", "nav", "header", "footer", "noscript")


class IngestError(Exception):
    """
    可预期的摄入失败（格式不支持、内容为空、来源不可达等），消息会直接回给用户
    """


def _int_config(key: str, default: int) -> int:
    try:
        return int(CONF_ATTR.get(key))
    except (TypeError, ValueError):
        return default


def chunk_size() -> int:
    return _int_config("knowledge_chunk_size", 800)


def chunk_overlap() -> int:
    return _int_config("knowledge_chunk_overlap", 120)


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def find_duplicate(knowledge_base_id: int, digest: str, exclude_document_id: int = None):
    """
    在同一知识库内按内容指纹找已存在的文档。

    以**内容**而非文件名/URL 判定重复：换个文件名上传同一份文档也算重复，
    这正是「同一来源被再次摄入」要拦的情况。
    """
    if not digest:
        return None
    queryset = models.KbDocument.objects.filter(
        knowledge_base_id=knowledge_base_id, content_hash=digest, is_deleted=False
    )
    if exclude_document_id:
        queryset = queryset.exclude(id=exclude_document_id)
    return queryset.first()


# ----------------------------------------------------------------------
# 解析
# ----------------------------------------------------------------------


def parse_file(file_name: str, raw: bytes) -> tuple:
    """
    解析上传的文件，返回 (标题, 正文)
    """
    name = (file_name or "").strip()
    lowered = name.lower()
    if not lowered.endswith(SUPPORTED_SUFFIXES):
        raise IngestError(f"暂不支持的文件格式，可用的有：{', '.join(SUPPORTED_SUFFIXES)}")
    if not raw:
        raise IngestError("文件内容为空")
    if len(raw) > MAX_UPLOAD_BYTES:
        raise IngestError(f"文件超过 {MAX_UPLOAD_BYTES // 1024 // 1024}MB 上限")

    if lowered.endswith(".pdf"):
        return _parse_pdf(name, raw)
    return _parse_text(name, raw)


def _parse_pdf(name: str, raw: bytes) -> tuple:
    try:
        reader = PdfReader(io.BytesIO(raw))
        pages = [(page.extract_text() or "") for page in reader.pages]
    except IngestError:
        raise
    except Exception as exc:  # noqa: BLE001 解析库对损坏文件抛的异常类型不定
        raise IngestError(f"PDF 解析失败: {exc}") from exc

    text = "\n\n".join(pages).strip()
    if not text:
        # 纯扫描件没有文本层。必须明确报出来，否则使用者会以为摄入成功了
        raise IngestError("未能从该 PDF 抽取到任何文本（可能是扫描件，需要先做 OCR）")
    return _title_from(name), text


def _parse_text(name: str, raw: bytes) -> tuple:
    for encoding in ("utf-8", "utf-8-sig", "gbk"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise IngestError("无法识别文件编码，请转存为 UTF-8")

    text = text.strip()
    if not text:
        raise IngestError("文件内容为空")
    return _title_from(name), text


def parse_url(url: str) -> tuple:
    """
    抓取网页并抽取正文，返回 (标题, 正文)
    """
    url = (url or "").strip()
    if not url.lower().startswith(("http://", "https://")):
        raise IngestError("链接必须以 http:// 或 https:// 开头")
    try:
        resp = requests.get(url, timeout=_URL_TIMEOUT, headers={"User-Agent": "my-dba-knowledge/1.0"})
        resp.raise_for_status()
    except Exception as exc:  # noqa: BLE001 来源不可达属于可预期失败
        raise IngestError(f"抓取失败: {exc}") from exc

    resp.encoding = resp.encoding or resp.apparent_encoding
    soup = BeautifulSoup(resp.text, "html.parser")
    for tag in soup(_STRIP_TAGS):
        tag.decompose()

    title = soup.title.get_text(strip=True) if soup.title else ""
    text = "\n".join(line.strip() for line in soup.get_text("\n").splitlines() if line.strip())
    if not text:
        raise IngestError("页面没有可提取的正文")
    return title or url, text


def _title_from(file_name: str) -> str:
    return re.sub(r"\.[^.]+$", "", file_name) or file_name


# ----------------------------------------------------------------------
# 切分
# ----------------------------------------------------------------------


def split_text(text: str, is_markdown: bool = True) -> list:
    """
    切分为 [(标题路径, 块内容), ...]

    按标题层级组织并保留重叠窗口——块脱离上下文时答案会断章取义。
    """
    size, overlap = chunk_size(), chunk_overlap()
    if not is_markdown:
        return [("", c) for c in _chunk_by_size(text, size, overlap)]

    sections, stack, buf = [], [], []
    for line in text.splitlines():
        matched = _HEADING.match(line)
        if not matched:
            buf.append(line)
            continue
        if buf:
            sections.append(("/".join(t for _, t in stack), "\n".join(buf)))
            buf = []
        level, title = len(matched.group(1)), matched.group(2).strip()
        while stack and stack[-1][0] >= level:
            stack.pop()
        stack.append((level, title))
    if buf:
        sections.append(("/".join(t for _, t in stack), "\n".join(buf)))

    result = []
    for heading_path, body in sections:
        for piece in _chunk_by_size(body, size, overlap):
            result.append((heading_path, piece))
    # 空内容统一返回空列表，由调用方判定为「切分后没有可用内容」并报给使用者
    return result


def _chunk_by_size(text: str, size: int, overlap: int) -> list:
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks, buf = [], ""
    for para in paragraphs:
        if len(para) > size:
            if buf:
                chunks.append(buf)
                buf = ""
            chunks.extend(_hard_split(para, size, overlap))
            continue
        if not buf:
            buf = para
        elif len(buf) + len(para) + 2 <= size:
            buf = f"{buf}\n\n{para}"
        else:
            chunks.append(buf)
            seed = buf[-overlap:] if overlap > 0 else ""
            buf = f"{seed}\n\n{para}" if seed else para
    if buf:
        chunks.append(buf)
    return chunks


def _hard_split(text: str, size: int, overlap: int) -> list:
    step = max(size - overlap, 1)
    return [text[i : i + size] for i in range(0, len(text), step)]


# ----------------------------------------------------------------------
# 摄入
# ----------------------------------------------------------------------


def ingest(document: models.KbDocument, text: str) -> int:
    """
    把已解析的正文写入知识库，返回块数

    先嵌入再落库：嵌入失败时一行都不写，避免留下没有向量的半成品块。
    """
    is_markdown = document.source.lower().endswith((".md", ".markdown"))
    pieces = [(path, body) for path, body in split_text(text, is_markdown) if body.strip()]
    if not pieces:
        raise IngestError("切分后没有可用内容")

    provider = llm.active_embedding_provider()
    if provider is None:
        raise IngestError(
            "尚未配置生效中的嵌入模型，无法生成向量。请在「模型配置」中新增一条「嵌入模型」配置并设为生效"
        )

    try:
        vectors = llm.embed_documents(provider, [body for _, body in pieces])
    except Exception as exc:  # noqa: BLE001 外部模型调用
        raise IngestError(f"生成向量失败: {exc}") from exc
    if len(vectors) != len(pieces):
        raise IngestError(f"嵌入返回条数与切分块数不一致（{len(vectors)} != {len(pieces)}）")

    with transaction.atomic():
        chunks = models.KbChunk.objects.bulk_create(
            [
                models.KbChunk(
                    knowledge_base_id=document.knowledge_base_id,
                    document_id=document.id,
                    ordinal=index,
                    heading_path=heading_path[:512],
                    content=body,
                )
                for index, (heading_path, body) in enumerate(pieces)
            ]
        )
        document.chunk_count = len(chunks)
        document.save(update_fields=["chunk_count", "update_time"])

    # 写向量放在事务外：Qdrant 不参与库事务，失败时用补偿删除刚写入的块
    try:
        vectorstore.upsert_chunks(
            [
                (chunk.id, vector, document.knowledge_base_id, document.id)
                for chunk, vector in zip(chunks, vectors, strict=False)
            ]
        )
    except Exception as exc:  # noqa: BLE001
        models.KbChunk.objects.filter(document_id=document.id).delete()
        document.chunk_count = 0
        document.save(update_fields=["chunk_count", "update_time"])
        raise IngestError(f"写入向量失败（已回滚本次切分块）: {exc}") from exc

    return len(chunks)


def purge_chunks(chunk_queryset):
    """
    删除一批块对应的向量。

    向量清不掉不该挡住业务侧删除——库内数据是事实来源，残留的向量在「检索后回库取正文」
    时会被 `is_deleted` 过滤掉，只影响检索的候选集大小。
    """
    chunk_ids = list(chunk_queryset.values_list("id", flat=True))
    if not chunk_ids:
        return
    try:
        vectorstore.delete_chunks(chunk_ids)
    except vectorstore.VectorStoreError as exc:
        LOGGER.warning("清理向量失败，仅影响检索候选集: %s", exc)


def reset_document(document):
    """
    清掉一份文档的块与向量，并把状态复位为待处理
    """
    purge_chunks(models.KbChunk.objects.filter(document_id=document.id))
    models.KbChunk.objects.filter(document_id=document.id).delete()
    document.chunk_count = 0
    document.status = custom_enum.DocumentStatusEnum.PENDING.value
    document.fail_reason = ""
    document.save(update_fields=["chunk_count", "status", "fail_reason", "update_time"])


def _discard_duplicate(document: models.KbDocument, duplicate, on_duplicate):
    """
    处理命中重复来源的情况，返回 True 表示「本次文档已被处理掉，不该继续摄入」

    关键约束是**不静默**：要么跳过并写明原因，要么按使用者的明确要求覆盖。
    """
    if on_duplicate == custom_enum.DuplicateActionEnum.OVERWRITE.value:
        reset_document(duplicate)
        duplicate.is_deleted = True
        duplicate.save(update_fields=["is_deleted", "update_time"])
        return False

    _finish(
        document,
        custom_enum.DocumentStatusEnum.FAILED,
        f"内容与已有文档《{duplicate.title}》(id={duplicate.id}) 重复，已跳过。"
        f"如需覆盖请带 on_duplicate={custom_enum.DuplicateActionEnum.OVERWRITE.value} 重新提交",
    )
    return True


def _finish(document: models.KbDocument, status, fail_reason: str = "", text_hash: str = ""):
    document.status = status.value
    document.fail_reason = (fail_reason or "")[:1024]
    if text_hash:
        document.content_hash = text_hash
    document.save(update_fields=["status", "fail_reason", "content_hash", "update_time"])


def run_ingest(document_id: int, raw: bytes = None, url: str = "", on_duplicate=None):
    """
    后台任务入口。raw 为上传文件的字节，url 为待抓取的链接（二选一）。

    on_duplicate 为命中重复来源时的处理方式（见 DuplicateActionEnum）；
    链接来源的内容要抓取后才知道，因此重复检测只能在这里做——那时已无法回头问使用者，
    所以默认是「跳过并写明原因」，而不是静默产生重复内容。

    任何失败都落到文档状态上，不向上抛——否则后台线程只会打一行日志，使用者看不到。
    """
    document = models.KbDocument.objects.filter(id=document_id, is_deleted=False).first()
    if document is None:
        LOGGER.warning("摄入任务找不到文档 document_id=%s", document_id)
        return

    document.status = custom_enum.DocumentStatusEnum.PROCESSING.value
    document.save(update_fields=["status", "update_time"])

    try:
        if url:
            title, text = parse_url(url)
            if title and not document.title:
                document.title = title[:255]
        else:
            title, text = parse_file(document.source, raw or b"")

        digest = content_hash(text)
        document.source = (url or document.source)[:1024]
        document.content_hash = digest
        document.save(update_fields=["title", "source", "content_hash", "update_time"])

        duplicate = find_duplicate(document.knowledge_base_id, digest, exclude_document_id=document.id)
        if duplicate is not None and _discard_duplicate(document, duplicate, on_duplicate):
            LOGGER.info("摄入跳过（重复来源）document_id=%s 已有=%s", document_id, duplicate.id)
            return

        count = ingest(document, text)
        _finish(document, custom_enum.DocumentStatusEnum.SUCCESS)
        LOGGER.info("摄入完成 document_id=%s chunks=%s", document_id, count)
    except IngestError as exc:
        LOGGER.warning("摄入失败 document_id=%s err=%s", document_id, exc)
        _finish(document, custom_enum.DocumentStatusEnum.FAILED, str(exc))
    except Exception as exc:  # noqa: BLE001 兜底：不能让后台线程静默吞掉
        LOGGER.error("摄入异常 document_id=%s err=%s", document_id, exc)
        _finish(document, custom_enum.DocumentStatusEnum.FAILED, f"内部错误: {exc}")
