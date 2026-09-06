<template>
  <el-dialog
    :model-value="modelValue"
    title="文本模型 API Key / BYOK"
    width="min(560px, calc(100vw - 32px))"
    :close-on-click-modal="false"
    @update:model-value="closeDialog"
    @open="refreshStatus"
  >
    <div class="byok-settings">
      <el-alert type="warning" :closable="false" show-icon>
        <template #title>仅保存在当前标签页的会话中</template>
        刷新页面后仍可使用；关闭标签页或退出登录时会清除。浏览器扩展和本页面脚本仍可能读取，请只在可信设备上使用。
      </el-alert>

      <div class="status-row">
        <div>
          <div class="status-title">使用自有文本模型 Key</div>
          <div class="status-description">
            {{ hasKey ? '已保存会话级 Key（不会回显）' : '尚未配置' }}
          </div>
        </div>
        <el-switch
          :model-value="enabled"
          :disabled="!hasKey"
          inline-prompt
          active-text="开"
          inactive-text="关"
          @change="toggleEnabled"
        />
      </div>

      <el-form label-position="top" @submit.prevent="saveAll">
        <el-form-item :label="hasKey ? '替换文本模型 API Key' : '文本模型 API Key'">
          <el-input
            v-model="draftKey"
            type="password"
            name="if-line-byok-api-key"
            autocomplete="off"
            data-1p-ignore
            data-lpignore="true"
            data-form-type="other"
            :maxlength="BYOK_API_KEY_MAX_LENGTH"
            :placeholder="hasKey ? '输入新 Key 以替换当前配置' : '请输入 API Key'"
          />
        </el-form-item>

        <el-form-item label="Base URL（可选）">
          <el-input
            v-model="draftBaseUrl"
            name="if-line-byok-base-url"
            autocomplete="off"
            data-1p-ignore
            data-lpignore="true"
            data-form-type="other"
            :maxlength="BYOK_BASE_URL_MAX_LENGTH"
            :placeholder="baseUrlPlaceholder"
          />
          <div class="field-hint">
            自定义服务商入口，留空则使用服务端默认。仅支持 http/https；内网地址会被拒绝。
          </div>
        </el-form-item>

        <el-form-item label="模型名（可选）">
          <el-input
            v-model="draftModel"
            name="if-line-byok-model"
            autocomplete="off"
            data-1p-ignore
            data-lpignore="true"
            data-form-type="other"
            :maxlength="BYOK_MODEL_MAX_LENGTH"
            :placeholder="modelPlaceholder"
          />
          <div class="field-hint">
            与 Base URL 匹配的模型标识，如 glm-4.6、deepseek-chat、gpt-4o。留空则使用服务端默认。
          </div>
        </el-form-item>
      </el-form>

      <p class="compatibility-note">
        Key / Base URL / 模型名仅替换文本类 LLM 调用（Story Bible、大纲、正文、改写、分类、分段、分析、AI 味检测、章节语音、VNGraph），不影响图片生成与语音合成。
      </p>
    </div>

    <template #footer>
      <div class="dialog-footer">
        <el-button v-if="hasKey" type="danger" plain @click="clearKey">清除 Key</el-button>
        <span class="footer-spacer" />
        <el-button @click="closeDialog(false)">关闭</el-button>
        <el-button type="primary" :disabled="!draftKey.trim() && !draftBaseUrl.trim() && !draftModel.trim()" @click="saveAll">
          保存并启用
        </el-button>
      </div>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { llmApi } from '@/api/llmApi'
import {
  BYOK_API_KEY_MAX_LENGTH,
  BYOK_BASE_URL_MAX_LENGTH,
  BYOK_MODEL_MAX_LENGTH,
  clearByokApiKey,
  getByokStatus,
  getActiveByokBaseUrl,
  getActiveByokModel,
  saveByokApiKey,
  saveByokBaseUrl,
  saveByokModel,
  setByokEnabled,
  type ByokStatus
} from '@/security/byok'

defineProps<{
  modelValue: boolean
}>()

const emit = defineEmits<{
  (event: 'update:modelValue', value: boolean): void
  (event: 'change', status: ByokStatus): void
}>()

const draftKey = ref('')
const draftBaseUrl = ref('')
const draftModel = ref('')
const hasKey = ref(false)
const hasBaseUrl = ref(false)
const hasModel = ref(false)
const enabled = ref(false)
const baseUrlPlaceholder = ref('加载中…')
const modelPlaceholder = ref('加载中…')

function applyStatus(status: ByokStatus) {
  hasKey.value = status.hasKey
  hasBaseUrl.value = status.hasBaseUrl
  hasModel.value = status.hasModel
  enabled.value = status.enabled
  emit('change', status)
}

async function loadDefaults() {
  try {
    const { data } = await llmApi.getByokDefaults()
    baseUrlPlaceholder.value = data?.base_url_masked || '服务端默认 Base URL'
    modelPlaceholder.value = data?.model_masked || '服务端默认模型'
  } catch {
    baseUrlPlaceholder.value = '服务端默认 Base URL'
    modelPlaceholder.value = '服务端默认模型'
  }
}

async function refreshStatus() {
  draftKey.value = ''
  // Pre-fill base URL / model from session so the user can review without retyping.
  draftBaseUrl.value = getActiveByokBaseUrl() ?? ''
  draftModel.value = getActiveByokModel() ?? ''
  applyStatus(getByokStatus())
  await loadDefaults()
}

function closeDialog(value = false) {
  draftKey.value = ''
  draftBaseUrl.value = ''
  draftModel.value = ''
  emit('update:modelValue', value)
}

function saveAll() {
  // An empty API Key draft is a no-op (clearing is an explicit confirmed action).
  if (draftKey.value.trim()) {
    try {
      applyStatus(saveByokApiKey(draftKey.value))
    } catch (error) {
      ElMessage.error(error instanceof Error ? error.message : 'API Key 保存失败')
      return
    }
  }

  try {
    applyStatus(saveByokBaseUrl(draftBaseUrl.value))
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : 'Base URL 保存失败')
    return
  }

  try {
    applyStatus(saveByokModel(draftModel.value))
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '模型名保存失败')
    return
  }

  draftKey.value = ''
  ElMessage.success('BYOK 配置已保存')
}

function toggleEnabled(value: string | number | boolean) {
  try {
    const nextEnabled = Boolean(value)
    applyStatus(setByokEnabled(nextEnabled))
    ElMessage.success(nextEnabled ? '已启用自有文本模型 API Key' : '已暂停使用自有文本模型 API Key')
  } catch (error) {
    applyStatus(getByokStatus())
    ElMessage.error(error instanceof Error ? error.message : 'API Key 状态更新失败')
  }
}

async function clearKey() {
  try {
    await ElMessageBox.confirm(
      '将从当前标签页会话中永久清除此文本模型 API Key 及关联的 Base URL / 模型名。',
      '清除 API Key',
      {
        type: 'warning',
        confirmButtonText: '清除',
        cancelButtonText: '取消'
      }
    )
  } catch {
    return
  }

  clearByokApiKey()
  draftKey.value = ''
  draftBaseUrl.value = ''
  draftModel.value = ''
  applyStatus(getByokStatus())
  ElMessage.success('文本模型 API Key 已清除')
}
</script>

<style scoped>
.byok-settings {
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.status-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 20px;
}

.status-title {
  color: #303133;
  font-size: 15px;
  font-weight: 600;
}

.status-description,
.compatibility-note,
.field-hint {
  color: #909399;
  font-size: 13px;
  line-height: 1.6;
}

.status-description {
  margin-top: 4px;
}

.field-hint {
  margin-top: 4px;
  font-size: 12px;
}

.compatibility-note {
  margin: -6px 0 0;
}

.dialog-footer {
  display: flex;
  align-items: center;
  width: 100%;
}

.footer-spacer {
  flex: 1;
}
</style>
