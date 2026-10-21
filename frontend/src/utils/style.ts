// 字体与颜色等展示相关辅助工具

// 内置名称映射：用于 fontName 展示（旧值 -> 中文名），找不到时回退返回原值
const FONT_NAME_MAP: Record<string, string> = {
  'Microsoft YaHei, sans-serif': '微软雅黑',
  'SimHei, sans-serif': '黑体',
  'SimSun, serif': '宋体',
  'KaiTi, serif': '楷体',
  'FangSong, serif': '仿宋',
  'DengXian, sans-serif': '等线',
  'Arial, sans-serif': 'Arial',
  '"Times New Roman", serif': 'Times New Roman',
  '"Courier New", monospace': 'Courier New',
  'Verdana, sans-serif': 'Verdana',
  '微软雅黑': '微软雅黑',
  '黑体': '黑体',
  '宋体': '宋体',
  '仿宋': '仿宋',
  '楷体': '楷体',
}

export function fontName(value: string): string {
  if (!value) return '微软雅黑'
  return FONT_NAME_MAP[value] ?? value
}

export function alphaStyle(hexColor: string, opacityPercent: string): string {
  const pct = Math.max(0, Math.min(100, Number(opacityPercent) || 0)) / 100
  const hex = hexColor.replace('#', '')
  const full = hex.length === 3
    ? hex.split('').map((c) => c + c).join('')
    : hex.padEnd(6, '0')
  const r = parseInt(full.slice(0, 2), 16)
  const g = parseInt(full.slice(2, 4), 16)
  const b = parseInt(full.slice(4, 6), 16)
  return `rgba(${r}, ${g}, ${b}, ${pct})`
}
