// 歌名展示加工（仅展示层）：删除歌名【末尾】的成对括号及内容（全角（）/半角()/【】），
// 歌名中间的括号内容保留；悬浮 title 属性显示完整原名。数据层（store 原始字段、
// queue.json、后端返回）一律不改。
// 方案 B：入参兼容 string（旧数据）与 SongEntry（新数据），统一取 name 字段处理。
import type { SongEntry } from '@/types'
import { entryName } from '@/types'

export function formatSongTitle(title: string | SongEntry | null | undefined): string {
  const raw = entryName(title)
  if (!raw) return raw
  let prev = ''
  let cur = String(raw).trim()
  // 循环删除末尾的成对括号组（末尾多个括号连排如 "歌名（A）（B）" 全部删掉），
  // 中间括号不受影响；无末尾括号时立即退出
  while (cur !== prev) {
    prev = cur
    cur = cur.replace(/\s*[（(【][^()（）【】]*[)）】]\s*$/, '').trim()
  }
  return cur
}
