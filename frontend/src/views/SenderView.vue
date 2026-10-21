<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { useSenderStore } from '@/stores/sender'
import type { ReplyRule } from '@/types'

const senderStore = useSenderStore()
const showPlain = ref(false)
const danmuInput = ref('')
const thanksInput = ref('')
const replyKeyword = ref('')
const replyContent = ref('')

// 匹配方式动态说明（随选中切换）
const matchModeHints: Record<'contains' | 'exact' | 'regex', string> = {
  contains: '弹幕中出现关键词即触发。例：关键词「666」，弹幕「主播666」→ 触发',
  exact: '弹幕与关键词完全一致才触发。例：关键词「666」，弹幕「666」→ 触发；弹幕「主播666」→ 不触发',
  regex: '关键词按正则表达式匹配弹幕。例：关键词「加油[0-9]+」，弹幕「加油666」→ 触发',
}
const matchModeHint = computed(() => matchModeHints[senderStore.config.replyMatchMode])

// App.vue 启动时已全局加载 cookies 与 sender_config；
// 此处兜底：切页重建时若未加载成功则再拉一次
onMounted(async () => {
  if (!senderStore.cookiesLoaded) await senderStore.loadCookies()
  if (!senderStore.configLoaded) await senderStore.loadConfig()
})

// 添加 / 删除均直接改 config，store 内部 deep watch + 800ms 防抖自动保存
function handleAdd() {
  const text = danmuInput.value.trim()
  if (!text) {
    ElMessage.warning('请输入弹幕内容')
    return
  }
  senderStore.config.text_list = [...senderStore.config.text_list, text]
  danmuInput.value = ''
}

function handleRemove(index: number) {
  senderStore.config.text_list = senderStore.config.text_list.filter((_, i) => i !== index)
}

function handleAddThanks() {
  const text = thanksInput.value.trim()
  if (!text) {
    ElMessage.warning('请输入答谢语句')
    return
  }
  senderStore.config.thanks_list = [...senderStore.config.thanks_list, text]
  thanksInput.value = ''
}

function handleRemoveThanks(index: number) {
  senderStore.config.thanks_list = senderStore.config.thanks_list.filter((_, i) => i !== index)
}

// ---- 自动回复 ----
// 关键词集合等价判定（顺序无关）：排序后拼接作为 key
function keySet(keywords: string[]): string {
  return [...keywords].sort().join('\u0001')
}
function findReplyRuleIndex(keywords: string[]): number {
  const key = keySet(keywords)
  return senderStore.config.reply_list.findIndex((rule) => keySet(rule.keywords) === key)
}

function handleAddReply() {
  // 解析关键词：去空白，兼容中英文逗号分隔
  const keywords = replyKeyword.value.split(/[,，]/).map((k) => k.trim()).filter(Boolean)
  const content = replyContent.value.trim()
  if (!keywords.length) {
    ElMessage.warning('请输入触发关键词（多个用英文逗号分隔）')
    return
  }
  if (!content) {
    ElMessage.warning('请输入回复弹幕内容')
    return
  }
  const idx = findReplyRuleIndex(keywords)
  if (idx >= 0) {
    // 关键词组合完全重复且该回复已存在 → 拒绝
    if (senderStore.config.reply_list[idx].replies.includes(content)) {
      ElMessage.warning('该关键词组合已存在相同回复，请勿重复添加')
      return
    }
    senderStore.config.reply_list[idx].replies.push(content)
    ElMessage.success(`已向规则追加回复（随机池 +1）：${keywords.join(',')}`)
  } else {
    senderStore.config.reply_list.push({ keywords, replies: [content] } as ReplyRule)
    ElMessage.success(`已新建规则：${keywords.join(',')}`)
  }
  replyKeyword.value = ''
  replyContent.value = ''
}

function handleRemoveReplyReply(ruleIndex: number, replyIndex: number) {
  const rule = senderStore.config.reply_list[ruleIndex]
  if (!rule) return
  rule.replies.splice(replyIndex, 1)
  if (!rule.replies.length) {
    senderStore.config.reply_list = senderStore.config.reply_list.filter((_, i) => i !== ruleIndex)
    ElMessage.success('该规则已无回复内容，整条规则已删除')
  } else {
    ElMessage.success('已删除该条回复')
  }
}

function handleRemoveReplyRule(ruleIndex: number) {
  senderStore.config.reply_list = senderStore.config.reply_list.filter((_, i) => i !== ruleIndex)
  ElMessage.success('已删除规则')
}

async function handleToggle() {
  if (senderStore.running) {
    const res = await senderStore.stop()
    if (res.ok) ElMessage.success(res.message)
    else ElMessage.error(res.message)
    return
  }
  // 启动前校验
  if (!senderStore.cookies.room_id.trim()) {
    ElMessage.warning('请先填写房间号')
    return
  }
  if (!senderStore.cookies.SESSDATA.trim() || !senderStore.cookies.bili_jct.trim()) {
    ElMessage.warning('请先填写 SESSDATA / bili_jct cookies')
    return
  }
  if (!senderStore.config.text_list.length) {
    ElMessage.warning('请先添加至少一条刷屏弹幕')
    return
  }
  const res = await senderStore.start()
  if (res.ok) ElMessage.success(res.message)
  else ElMessage.error(res.message)
}
</script>

<template>
  <div>
    <div class="shui-page-head">
      <div>
        <h2>自动刷屏</h2>
        <div class="sub">定时向直播间发送预设弹幕 · cookies 仅保存在本地</div>
      </div>
      <div class="shui-page-actions">
        <span class="shui-chip"><span class="dim">运行状态</span><span class="v" :class="senderStore.running ? 'ok' : 'off'">{{ senderStore.running ? '● 运行中' : '● 已停止' }}</span></span>
        <el-button v-if="senderStore.running" size="small" plain type="danger" @click="handleToggle">停止刷屏</el-button>
      </div>
    </div>

    <div class="sender-grid">
    <!-- 账号配置 -->
    <div class="shui-card">
      <div class="shui-card-title">账号配置</div>
      <div class="shui-cookies-grid">
        <div class="shui-form-row">
          <span class="shui-form-label">房间号</span>
          <el-input v-model="senderStore.cookies.room_id" placeholder="直播间房间号" />
        </div>
        <div class="shui-form-row">
          <span class="shui-form-label">SESSDATA</span>
          <el-input v-model="senderStore.cookies.SESSDATA" :type="showPlain ? 'text' : 'password'" placeholder="登录 cookie" />
        </div>
        <div class="shui-form-row">
          <span class="shui-form-label">bili_jct</span>
          <el-input v-model="senderStore.cookies.bili_jct" :type="showPlain ? 'text' : 'password'" placeholder="登录 cookie" />
        </div>
        <div class="shui-form-row">
          <span class="shui-form-label">buvid3</span>
          <el-input v-model="senderStore.cookies.buvid3" :type="showPlain ? 'text' : 'password'" placeholder="设备 cookie" />
        </div>
        <div class="shui-form-row">
          <span class="shui-form-label">dedeuserid</span>
          <el-input v-model="senderStore.cookies.dedeuserid" :type="showPlain ? 'text' : 'password'" placeholder="用户 id cookie" />
        </div>
        <div class="shui-form-row">
          <span class="shui-form-label" />
          <el-switch v-model="showPlain" />
          <span class="dim">明文显示 cookies</span>
        </div>
      </div>
      <div class="cookies-mask-hint">已存凭据以掩码显示，重新输入完整值可覆盖</div>
      <div class="cookies-actions">
        <span class="dim">cookies 仅保存在本地 cookies.json，不会上传</span>
      </div>
    </div>

    <!-- 刷屏内容 -->
    <div class="shui-card">
      <div class="shui-card-title">刷屏内容</div>
      <div class="shui-form-row">
        <span class="shui-form-label">刷屏模式</span>
        <el-radio-group v-model="senderStore.config.screeningMode" :disabled="senderStore.running">
          <el-radio value="loop">循环全部</el-radio>
          <el-radio value="random">随机一条</el-radio>
        </el-radio-group>
        <span class="shui-form-label" style="width: auto; text-align: left;">间隔</span>
        <el-input v-model="senderStore.config.interval" placeholder="5" style="width: 80px" :disabled="senderStore.running" />
        <span class="dim">秒</span>
      </div>
      <div class="shui-form-row">
        <span class="shui-form-label">添加弹幕</span>
        <el-input v-model="danmuInput" placeholder="输入要发送的弹幕内容" style="width: 320px" :disabled="senderStore.running" @keyup.enter="handleAdd" />
        <el-button type="primary" :disabled="senderStore.running" @click="handleAdd">添加</el-button>
      </div>
      <table class="list">
        <thead>
          <tr><th style="width: 50px;">#</th><th>弹幕内容</th><th style="text-align: right; width: 88px;">操作</th></tr>
        </thead>
        <tbody>
          <tr v-if="!senderStore.config.text_list.length">
            <td colspan="3"><span class="dim">暂无弹幕，先添加几条吧</span></td>
          </tr>
          <tr v-for="(text, index) in senderStore.config.text_list" :key="index">
            <td>{{ index + 1 }}</td>
            <td>{{ text }}</td>
            <td>
              <div class="ops">
                <el-button size="small" type="danger" plain :disabled="senderStore.running" @click="handleRemove(index)">删除</el-button>
              </div>
            </td>
          </tr>
        </tbody>
      </table>
      <div class="sender-actions">
        <span class="dim">建议刷屏间隔3秒以上，太快了可能被封</span>
        <el-button
          class="start-btn" 
          :type="senderStore.running ? 'danger' : 'success'"
          :loading="senderStore.saving"
          @click="handleToggle"
        >
          {{ senderStore.running ? '停止刷屏' : '启动刷屏' }}
        </el-button>
      </div>
    </div>

    <!-- 自动答谢 -->
    <div class="shui-card thanks-card">
      <div class="shui-card-title">
        <span>自动答谢</span>
        <el-switch v-model="senderStore.config.thanksEnabled" style="margin-left: auto;" />
        <span class="dim thanks-state">{{ senderStore.config.thanksEnabled ? '已开启' : '已关闭' }}</span>
      </div>
      <div class="shui-form-row">
        <span class="shui-form-label">答谢方式</span>
        <el-radio-group v-model="senderStore.config.thanksMode" :disabled="senderStore.config.thanksEnabled">
          <el-radio value="loop">循环全部</el-radio>
          <el-radio value="random">随机一条</el-radio>
        </el-radio-group>
        <span class="dim">收到礼物 / 醒目留言 / 大航海时自动发送答谢语句</span>
      </div>
      <div class="shui-form-row">
        <span class="shui-form-label">添加语句</span>
        <el-input
          v-model="thanksInput"
          placeholder="输入答谢语句，如：感谢老板的小心心~"
          style="width: 320px"
          :disabled="senderStore.config.thanksEnabled"
          @keyup.enter="handleAddThanks"
        />
        <el-button type="primary" :disabled="senderStore.config.thanksEnabled" @click="handleAddThanks">添加</el-button>
      </div>
      <table class="list">
        <thead>
          <tr><th style="width: 50px;">#</th><th>答谢语句</th><th style="text-align: right; width: 88px;">操作</th></tr>
        </thead>
        <tbody>
          <tr v-if="!senderStore.config.thanks_list.length">
            <td colspan="3"><span class="dim">暂无答谢语句，先添加几条吧</span></td>
          </tr>
          <tr v-for="(text, index) in senderStore.config.thanks_list" :key="index">
            <td>{{ index + 1 }}</td>
            <td>{{ text }}</td>
            <td>
              <div class="ops">
                <el-button size="small" type="danger" plain :disabled="senderStore.config.thanksEnabled" @click="handleRemoveThanks(index)">删除</el-button>
              </div>
            </td>
          </tr>
        </tbody>
      </table>
      <div class="sender-actions">
        <span class="dim">自动答谢开关关闭时不会触发发送</span>
      </div>
    </div>

    <!-- 自动回复 -->
    <div class="shui-card reply-card">
      <div class="shui-card-title">
        <span>自动回复</span>
        <el-switch v-model="senderStore.config.replyEnabled" style="margin-left: auto;" />
        <span class="dim thanks-state">{{ senderStore.config.replyEnabled ? '已开启' : '已关闭' }}</span>
      </div>
      <div class="shui-form-row">
        <span class="shui-form-label">匹配方式</span>
        <el-radio-group v-model="senderStore.config.replyMatchMode" :disabled="senderStore.config.replyEnabled">
          <el-radio value="contains">包含匹配</el-radio>
          <el-radio value="exact">完全匹配</el-radio>
          <el-radio value="regex">正则匹配</el-radio>
        </el-radio-group>
      </div>
      <div class="shui-form-row">
        <span class="shui-form-label" />
        <span class="dim">{{ matchModeHint }}</span>
      </div>
      <div class="shui-form-row">
        <span class="shui-form-label">冷却间隔</span>
        <el-input v-model.number="senderStore.config.replyCooldown" type="number" min="0" style="width: 90px" :disabled="senderStore.config.replyEnabled" />
        <span class="dim">秒，同一关键词 N 秒内只回复一次；0 = 不冷却</span>
      </div>
      <div class="shui-form-row">
        <span class="shui-form-label">忽略自身弹幕</span>
        <el-switch v-model="senderStore.config.replyIgnoreSelf" :disabled="senderStore.config.replyEnabled" />
        <span class="dim">不响应自己发送的弹幕，防止自动回复触发刷屏弹幕死循环</span>
      </div>
      <div class="shui-form-row">
        <span class="shui-form-label">添加规则</span>
        <el-input v-model="replyKeyword" placeholder="触发关键词，多个用英文逗号分隔，如：666, 6" style="width: 240px" :disabled="senderStore.config.replyEnabled" @keyup.enter="handleAddReply" />
        <el-input v-model="replyContent" placeholder="回复弹幕内容，如：感谢老铁的 666~" style="width: 320px" :disabled="senderStore.config.replyEnabled" @keyup.enter="handleAddReply" />
        <el-button type="primary" :disabled="senderStore.config.replyEnabled" @click="handleAddReply">添加</el-button>
      </div>
      <div class="shui-form-row">
        <span class="shui-form-label" />
        <span class="dim">关键词组合已存在时向该规则追加一条回复（随机池），不存在则新建规则</span>
      </div>
      <table class="list">
        <thead>
          <tr><th style="width: 50px;">#</th><th style="width: 220px;">触发关键词</th><th>回复弹幕（随机池）</th><th style="text-align: right; width: 88px;">操作</th></tr>
        </thead>
        <tbody>
          <tr v-if="!senderStore.config.reply_list.length">
            <td colspan="4"><span class="dim">暂无自动回复规则，先添加几条吧</span></td>
          </tr>
          <tr v-for="(rule, ruleIndex) in senderStore.config.reply_list" :key="ruleIndex">
            <td>{{ ruleIndex + 1 }}</td>
            <td>
              <span v-for="(kw, ki) in rule.keywords" :key="ki" class="kw-tag">{{ kw }}</span>
            </td>
            <td>
              <div v-for="(reply, replyIndex) in rule.replies" :key="replyIndex" class="reply-item">
                <span class="txt">{{ reply }}</span>
                <el-button size="small" type="danger" plain :disabled="senderStore.config.replyEnabled" @click="handleRemoveReplyReply(ruleIndex, replyIndex)">删</el-button>
              </div>
              <span v-if="!rule.replies.length" class="dim">（无回复内容）</span>
            </td>
            <td>
              <div class="ops">
                <el-button size="small" type="danger" plain :disabled="senderStore.config.replyEnabled" @click="handleRemoveReplyRule(ruleIndex)">删除规则</el-button>
              </div>
            </td>
          </tr>
        </tbody>
      </table>
      <div class="sender-actions">
        <span class="dim">自动回复开关关闭时不会触发发送</span>
      </div>
    </div>
    </div>
  </div>
</template>

<style scoped>
.sub { font-size: 13px; color: var(--shui-text-dim); margin-top: 4px; }
.dim { color: var(--shui-text-dim); font-size: 12px; }
.save-hint { color: var(--shui-text-dim); font-size: 12px; }
.cookies-mask-hint {
  margin-top: 10px;
  padding: 6px 10px;
  border-radius: 6px;
  background: rgba(240, 183, 107, 0.10);
  border: 1px solid rgba(240, 183, 107, 0.35);
  color: #f0b76b;
  font-size: 12px;
}
.cookies-actions {
  display: flex;
  align-items: center;
  gap: 14px;
  margin-top: 12px;
  padding-top: 12px;
  border-top: 1px solid var(--shui-border);
}
.list { width: 100%; border-collapse: collapse; font-size: 13px; }
.list th { text-align: left; color: var(--shui-text-dim); font-weight: 500; padding: 8px 10px; border-bottom: 1px solid var(--shui-border); }
.list td { padding: 9px 10px; border-bottom: 1px solid rgba(38, 49, 74, 0.5); }
.list tr:last-child td { border-bottom: none; }
.ops { display: flex; gap: 6px; justify-content: flex-end; }
.sender-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-top: 14px;
  padding-top: 14px;
  border-top: 1px solid var(--shui-border);
}
.start-btn { min-width: 130px; font-weight: 600; }
.thanks-card { border-color: rgba(77, 141, 255, 0.55); }
.reply-card { border-color: rgba(77, 141, 255, 0.55); }
.thanks-card .shui-card-title, .reply-card .shui-card-title { display: flex; align-items: center; gap: 8px; }
.thanks-state { margin-left: 6px; }
.kw-tag {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 4px;
  background: rgba(77, 141, 255, 0.12);
  border: 1px solid rgba(77, 141, 255, 0.35);
  color: #9cc2ff;
  font-size: 12px;
  margin: 0 4px 4px 0;
}
.reply-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 3px 0;
}
.reply-item + .reply-item { border-top: 1px dashed rgba(45, 58, 92, 0.6); }
.reply-item .txt { flex: 1; min-width: 0; }

/* 横屏 2x2：外层 grid 两列，同列等高，卡内 flex 撑满 */
.sender-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 14px;
  align-items: stretch;
}
.sender-grid .shui-card {
  display: flex;
  flex-direction: column;
  margin-bottom: 0;
}
.sender-grid .cookies-actions,
.sender-grid .sender-actions {
  margin-top: auto;
}
@media (max-width: 1099px) {
  .sender-grid { grid-template-columns: 1fr; }
}
</style>
