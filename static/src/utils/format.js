// 展示用格式化工具

const UNITS = ['B', 'KB', 'MB', 'GB', 'TB', 'PB']

/**
 * 字节数转可读体积
 */
export function formatBytes(value) {
  const size = Number(value)
  if (!size || size < 0) return '0 B'
  let index = 0
  let current = size
  while (current >= 1024 && index < UNITS.length - 1) {
    current /= 1024
    index += 1
  }
  return `${current.toFixed(index === 0 ? 0 : 2)} ${UNITS[index]}`
}

/**
 * 行数转带千分位的字符串
 */
export function formatRows(value) {
  const rows = Number(value)
  if (!rows) return '0'
  return rows.toLocaleString('en-US')
}
