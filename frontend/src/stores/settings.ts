import { defineStore } from 'pinia'
import { ref, reactive } from 'vue'
import { ElMessage } from 'element-plus'
import { http } from '@/api/http'
import { sseBridge } from '@/api/sse'
import type { Settings, QueueSettings, MusicSettings, LyricsSettings, GallerySettings } from '@/types'

const EMPTY_QUEUE = (): QueueSettings => ({
  queueType: 'true', queueLimit: '10', queueCommands: '排队，排队新深渊', cancelCommands: '取消排队',
  titleText: '***   弹幕自动排队   ***', titleCheckbox: true, titleFontFamily: 'Microsoft YaHei, sans-serif',
  titleFontSize: '34', titleFontColor: '#24fffb', titleUnderlineCheckbox: true, titleUnderlineText: '————————————',
  titleUnderlineColor: '#7294f1', queueFontFamily: 'Microsoft YaHei, sans-serif', queueFontSize: '33',
  queueFontColor: '#1be90c', BackgroundColor: '#c9cdd9', BackgroundOpacity: '0', QueueBackgroundColorCheckbox: false,
  QueueBackgroundColor: '#a790a3', QueueOpacity: '10', giftPriority: 'true', queueEnabled: false,
})

const EMPTY_MUSIC = (): MusicSettings => ({
  musicCommands: '点歌', skipCommands: '切歌', musicTitleText: '待播歌曲：', musicTitleCheckbox: true,
  musicTitleFontFamily: 'Microsoft YaHei, sans-serif', musicTitleFontSize: '21', musicTitleFontColor: '#7694ee',
  musicTitleUnderlineCheckbox: false, musicTitleUnderlineText: '————————————', musicTitleUnderlineColor: '#d78484',
  musicFontFamily: 'Microsoft YaHei, sans-serif', musicFontSize: '22', musicFontColor: '#f27878',
  musicBackgroundColor: '#7694ee', musicBackgroundOpacity: '6', musicBackgroundColorCheckbox: false,
  musicQueueBackgroundColor: '#d278dd', musicQueueOpacity: '52', musicEnabled: false,
})

const EMPTY_LYRICS = (): LyricsSettings => ({
  lyricstitleEnabled: true, lyricstitleFontFamily: 'Microsoft YaHei, sans-serif', lyricstitleFontColor: '#aaff7f',
  lyricstitleOpacity: '100', lyricstitleFontSize: '24', lyricsartistEnabled: false,
  lyricsartistFontFamily: 'Microsoft YaHei, sans-serif', lyricsartistFontColor: '#ffaaff', lyricsartistOpacity: '60',
  lyricsartistFontSize: '17', lyricsFontFamily: 'Microsoft YaHei, sans-serif', lyricsFontColor: '#ffaaff',
  lyricsOpacity: '100', lyricsFontSize: '28', layoutposition: 'horizontal', lyricstitleAlign: 'left',
  lyricsartistAlign: 'left', lyricsAlign: 'left', lyricstitleWidth: '432', lyricsbackgroundColor: '#19192c',
  lyricsbackgroundOpacity: '9', lyricsFadein: false, lineSpacing: '8', layoutMargin: '20', layoutGap: '14', layoutWidth: '900',
  titleBoxWidth: '320',
})

const EMPTY_GALLERY = (): GallerySettings => ({
  biliclientRunning: false, livecode: '',
  openlive: { accessKeyId: '', accessKeySecret: '', appId: '' },
})

export const useSettingsStore = defineStore('settings', () => {
  const loaded = ref(false)
  const saving = ref(false)
  // 实时自动保存防抖定时器（按 section 隔离，400ms 合并高频设置项变化）
  const autoSaveTimers: Record<string, ReturnType<typeof setTimeout>> = {}
  const gallery = reactive<GallerySettings>(EMPTY_GALLERY())
  const queue = reactive<QueueSettings>(EMPTY_QUEUE())
  const music = reactive<MusicSettings>(EMPTY_MUSIC())
  const lyrics = reactive<LyricsSettings>(EMPTY_LYRICS())

  function applySettings(data: Partial<Settings>): void {
    if (!data) return
    if (data.gallery) Object.assign(gallery, data.gallery)
    if (data.queue) Object.assign(queue, data.queue)
    if (data.music) Object.assign(music, data.music)
    if (data.lyrics) Object.assign(lyrics, data.lyrics)
    loaded.value = true
  }

  async function load(): Promise<void> {
    try {
      const data = await http.get<Settings>('/loadSettings')
      applySettings(data)
    } catch (e) {
      console.error('加载设置失败', e)
    }
  }

  async function changeValue(key: string, value: any): Promise<boolean> {
    try {
      const res = await http.post<any>('/changeValue', { [key]: value })
      // 兼容原接口返回
      if (res && typeof res === 'object' && 'code' in res && res.code !== 200) {
        console.warn('changeValue 返回非 200', res)
        return false
      }
      return true
    } catch (e) {
      console.error('changeValue 失败', key, e)
      return false
    }
  }

  // 分组保存：单次提交整个 section（嵌套 JSON，如 { queue: {...} }）。
  // 旧实现逐字段提交 'section.key' 点号键，后端会当作顶层扁平键追加，导致
  // settings_temp[section] 从不更新、设置项保存后不生效；改为整组提交并统一
  // 序列化（boolean 原样、数字转字符串），保证 settings.json 结构与类型一致。
  async function saveSection(section: 'gallery' | 'queue' | 'music' | 'lyrics'): Promise<boolean> {
    saving.value = true
    try {
      const target = section === 'gallery' ? gallery : section === 'queue' ? queue : section === 'music' ? music : lyrics
      const snapshot: Record<string, any> = {}
      for (const [key, value] of Object.entries(target)) {
        if (typeof value === 'boolean') snapshot[key] = value
        else if (value !== null && typeof value === 'object') snapshot[key] = value
        else snapshot[key] = String(value ?? '')
      }
      return await changeValue(section, snapshot)
    } finally {
      saving.value = false
    }
  }

  // 设置项变化后调用：防抖 400ms 后整组提交（复用 saveSection 链路），成功静默、失败轻提示
  function scheduleAutoSave(section: 'gallery' | 'queue' | 'music' | 'lyrics'): void {
    clearTimeout(autoSaveTimers[section])
    autoSaveTimers[section] = setTimeout(async () => {
      const ok = await saveSection(section)
      if (!ok) ElMessage.error('设置自动保存失败')
    }, 400)
  }

  // 恢复某个分组的默认设置并保存
  async function resetSection(section: 'gallery' | 'queue' | 'music' | 'lyrics'): Promise<boolean> {
    const target = section === 'gallery' ? gallery : section === 'queue' ? queue : section === 'music' ? music : lyrics
    const def = section === 'gallery' ? EMPTY_GALLERY() : section === 'queue' ? EMPTY_QUEUE() : section === 'music' ? EMPTY_MUSIC() : EMPTY_LYRICS()
    Object.assign(target, def)
    return saveSection(section)
  }

  function listenSse(): void {
    sseBridge.on('settings_changed', (payload: any) => {
      if (payload && typeof payload === 'object') applySettings(payload)
    })
  }

  return { loaded, saving, gallery, queue, music, lyrics, load, changeValue, saveSection, scheduleAutoSave, resetSection, listenSse, applySettings }
})
