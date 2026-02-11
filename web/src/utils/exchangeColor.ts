/**
 * 根据交易所 ID 字符串哈希生成稳定的 HSL 颜色
 */

function hashCode(str: string): number {
  let hash = 0
  for (let i = 0; i < str.length; i++) {
    hash = ((hash << 5) - hash + str.charCodeAt(i)) | 0
  }
  return Math.abs(hash)
}

export function exchangeColor(exchangeId: string): string {
  const h = hashCode(exchangeId) % 360
  return `hsl(${h}, 55%, 45%)`
}

export function exchangeBgColor(exchangeId: string): string {
  const h = hashCode(exchangeId) % 360
  return `hsl(${h}, 50%, 94%)`
}
