<script setup lang="ts">
import { ref, onMounted, computed } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import type { NotificationChannel, NotificationChannelCreate, NotifyEventInfo } from '@/types'
import { notificationApi } from '@/api/notification'

const channels = ref<NotificationChannel[]>([])
const events = ref<NotifyEventInfo[]>([])
const loading = ref(false)
const dialogVisible = ref(false)
const testingId = ref<number | null>(null)
const currentChannel = ref<NotificationChannel | null>(null)

const channelTypes = [
  { value: 'telegram', label: 'Telegram' },
  { value: 'dingtalk', label: '钉钉' },
  { value: 'feishu', label: '飞书' },
]

const form = ref<NotificationChannelCreate>({
  channel_type: 'telegram',
  name: '',
  config: {},
  enabled_events: [],
})

const channelTypeLabel = computed(() => {
  const map: Record<string, string> = { telegram: 'Telegram', dingtalk: '钉钉', feishu: '飞书' }
  return (type: string) => map[type] ?? type
})

const channelTypeTag = computed(() => {
  const map: Record<string, string> = { telegram: '', dingtalk: 'warning', feishu: 'success' }
  return (type: string) => map[type] ?? 'info'
})

async function fetchChannels() {
  loading.value = true
  try {
    channels.value = await notificationApi.getAll()
  } finally {
    loading.value = false
  }
}

async function fetchEvents() {
  try {
    events.value = await notificationApi.getEvents()
  } catch {
    // fallback
  }
}

function resetForm() {
  form.value = {
    channel_type: 'telegram',
    name: '',
    config: {},
    enabled_events: [],
  }
}

function handleAdd() {
  currentChannel.value = null
  resetForm()
  dialogVisible.value = true
}

function handleEdit(ch: NotificationChannel) {
  currentChannel.value = ch
  form.value = {
    channel_type: ch.channel_type,
    name: ch.name,
    config: { ...ch.config },
    enabled_events: [...ch.enabled_events],
  }
  dialogVisible.value = true
}

async function handleSubmit() {
  try {
    if (currentChannel.value) {
      await notificationApi.update(currentChannel.value.id, {
        name: form.value.name,
        config: form.value.config,
        enabled_events: form.value.enabled_events,
      })
      ElMessage.success('更新成功')
    } else {
      await notificationApi.create(form.value)
      ElMessage.success('创建成功')
    }
    dialogVisible.value = false
    fetchChannels()
  } catch {
    // 错误已在拦截器处理
  }
}

async function handleDelete(ch: NotificationChannel) {
  try {
    await ElMessageBox.confirm(
      `确定要删除通知渠道 "${ch.name}" 吗？`,
      '删除确认',
      { type: 'warning' },
    )
    await notificationApi.delete(ch.id)
    ElMessage.success('删除成功')
    fetchChannels()
  } catch {
    // 用户取消
  }
}

async function handleToggle(ch: NotificationChannel) {
  try {
    await notificationApi.update(ch.id, { is_active: !ch.is_active })
    fetchChannels()
  } catch {
    // 错误已在拦截器处理
  }
}

async function handleTest(ch: NotificationChannel) {
  testingId.value = ch.id
  try {
    await notificationApi.test(ch.id)
    ElMessage.success('测试通知已发送')
  } catch {
    // 错误已在拦截器处理
  } finally {
    testingId.value = null
  }
}

onMounted(() => {
  fetchChannels()
  fetchEvents()
})
</script>

<template>
  <div>
    <div class="page-header">
      <el-row justify="space-between" align="middle">
        <h2>通知管理</h2>
        <el-button type="primary" @click="handleAdd">
          <el-icon><Plus /></el-icon>
          新增渠道
        </el-button>
      </el-row>
    </div>

    <el-card>
      <el-table :data="channels" v-loading="loading" stripe>
        <el-table-column prop="name" label="名称" />
        <el-table-column prop="channel_type" label="渠道类型" width="120">
          <template #default="{ row }">
            <el-tag :type="channelTypeTag(row.channel_type)">
              {{ channelTypeLabel(row.channel_type) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="订阅事件">
          <template #default="{ row }">
            <template v-if="row.enabled_events.length">
              <el-tag
                v-for="ev in row.enabled_events"
                :key="ev"
                size="small"
                class="event-tag"
              >{{ events.find(e => e.value === ev)?.label ?? ev }}</el-tag>
            </template>
            <span v-else class="text-muted">全部事件</span>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="80">
          <template #default="{ row }">
            <el-switch
              :model-value="row.is_active"
              @change="handleToggle(row)"
            />
          </template>
        </el-table-column>
        <el-table-column label="操作" width="200" fixed="right">
          <template #default="{ row }">
            <el-button
              link type="primary"
              :loading="testingId === row.id"
              @click="handleTest(row)"
            >测试</el-button>
            <el-button link type="primary" @click="handleEdit(row)">编辑</el-button>
            <el-button link type="danger" @click="handleDelete(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-dialog
      :title="currentChannel ? '编辑通知渠道' : '新增通知渠道'"
      v-model="dialogVisible"
      width="520px"
      destroy-on-close
    >
      <el-form :model="form" label-width="90px">
        <el-form-item label="渠道类型" v-if="!currentChannel">
          <el-select v-model="form.channel_type" @change="form.config = {}">
            <el-option
              v-for="t in channelTypes"
              :key="t.value"
              :label="t.label"
              :value="t.value"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="名称">
          <el-input v-model="form.name" placeholder="如：我的 Telegram" />
        </el-form-item>

        <!-- Telegram 配置 -->
        <template v-if="form.channel_type === 'telegram'">
          <el-form-item label="Bot Token">
            <el-input v-model="form.config.bot_token" placeholder="从 @BotFather 获取" />
          </el-form-item>
          <el-form-item label="Chat ID">
            <el-input v-model="form.config.chat_id" placeholder="用户或群组 ID" />
          </el-form-item>
        </template>

        <!-- 钉钉配置 -->
        <template v-if="form.channel_type === 'dingtalk'">
          <el-form-item label="Access Token">
            <el-input v-model="form.config.access_token" placeholder="钉钉机器人 access_token" />
          </el-form-item>
          <el-form-item label="签名密钥">
            <el-input v-model="form.config.secret" placeholder="加签密钥（可选，与关键字二选一）" />
          </el-form-item>
          <el-form-item label="关键字">
            <el-input v-model="form.config.keyword" placeholder="默认 ares" />
          </el-form-item>
        </template>

        <!-- 飞书配置 -->
        <template v-if="form.channel_type === 'feishu'">
          <el-form-item label="Webhook">
            <el-input v-model="form.config.webhook_url" placeholder="飞书机器人 Webhook 地址" />
          </el-form-item>
          <el-form-item label="签名密钥">
            <el-input v-model="form.config.secret" placeholder="签名密钥（可选）" />
          </el-form-item>
        </template>

        <el-form-item label="订阅事件">
          <el-checkbox-group v-model="form.enabled_events">
            <el-checkbox
              v-for="ev in events"
              :key="ev.value"
              :value="ev.value"
            >{{ ev.label }}</el-checkbox>
          </el-checkbox-group>
          <div class="text-muted" style="font-size: 12px; margin-top: 4px;">
            不勾选则订阅全部事件
          </div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" @click="handleSubmit">确定</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.event-tag {
  margin-right: 4px;
  margin-bottom: 2px;
}
.text-muted {
  color: #909399;
}
</style>
