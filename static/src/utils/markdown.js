import DOMPurify from 'dompurify'
import { marked } from 'marked'

// 模型回答是 Markdown；GFM 支持表格、删除线等常用语法，breaks 保留单换行
marked.setOptions({ gfm: true, breaks: true })

/**
 * 把 Markdown 渲染为可直接插入的 HTML
 *
 * 模型输出属于不可信内容：必须经过 DOMPurify，不能直接把原文交给 v-html。
 */
export function renderMarkdown(text) {
  if (!text) return ''
  return DOMPurify.sanitize(marked.parse(text))
}
