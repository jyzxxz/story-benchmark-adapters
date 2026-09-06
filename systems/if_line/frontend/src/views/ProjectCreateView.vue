<template>
  <div class="project-create-container">
    <div class="create-card">
      <h1 class="title">创建小说项目</h1>

      <el-form :model="form" label-position="top" class="create-form">
        <el-form-item label="小说标题" required>
          <el-input v-model="form.title" placeholder="为你的小说起一个名字" />
        </el-form-item>

        <el-form-item label="核心人物">
          <el-tag
            v-for="char in form.characters"
            :key="char"
            closable
            @close="removeCharacter(char)"
            class="character-tag"
          >
            {{ char }}
          </el-tag>
          <el-input
            v-model="newCharacter"
            placeholder="输入角色名后按回车添加"
            @keyup.enter="addCharacter"
            class="character-input"
          />
        </el-form-item>

        <el-form-item label="创作类型">
          <el-radio-group v-model="creationType">
            <el-radio-button value="original">原创</el-radio-button>
            <el-radio-button value="fanwork">二创</el-radio-button>
          </el-radio-group>
        </el-form-item>

        <el-form-item v-if="creationType === 'fanwork'" label="原作名称" required>
          <el-input
            v-model="form.source_work"
            placeholder="例如：原神、崩坏：星穹铁道"
            clearable
          />
        </el-form-item>

        <el-form-item label="故事开头" required>
          <el-input
            v-model="form.story_start"
            type="textarea"
            :rows="3"
            placeholder="描述故事的开头..."
          />
        </el-form-item>

        <el-form-item label="故事结尾" required>
          <el-input
            v-model="form.story_end"
            type="textarea"
            :rows="3"
            placeholder="描述故事的结尾..."
          />
        </el-form-item>

        <el-form-item label="风格基调">
          <el-select v-model="form.style" placeholder="选择风格">
            <el-option label="轻松幽默" value="轻松幽默" />
            <el-option label="悬疑推理" value="悬疑推理" />
            <el-option label="浪漫爱情" value="浪漫爱情" />
            <el-option label="热血冒险" value="热血冒险" />
            <el-option label="黑暗深沉" value="黑暗深沉" />
            <el-option label="治愈温馨" value="治愈温馨" />
          </el-select>
        </el-form-item>

        <el-form-item label="叙事节奏">
          <el-radio-group v-model="form.pace">
            <el-radio-button value="fast">紧凑</el-radio-button>
            <el-radio-button value="medium">均衡</el-radio-button>
            <el-radio-button value="slow">舒缓</el-radio-button>
          </el-radio-group>
        </el-form-item>

        <el-form-item label="额外要求">
          <el-input
            v-model="form.extra_requirements"
            type="textarea"
            :rows="2"
            placeholder="其他特殊要求..."
          />
        </el-form-item>

        <el-form-item>
          <el-button type="primary" size="large" @click="createProject" :loading="loading" class="create-btn">
            开始创作
          </el-button>
        </el-form-item>
      </el-form>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { storyPathApi } from '@/api/storyPathApi'

const router = useRouter()

const form = ref({
  title: '',
  characters: [] as string[],
  story_start: '',
  story_end: '',
  style: '',
  source_work: '',
  pace: 'medium',
  extra_requirements: ''
})

const newCharacter = ref('')
const creationType = ref<'original' | 'fanwork'>('original')
const loading = ref(false)

const addCharacter = () => {
  if (newCharacter.value.trim() && !form.value.characters.includes(newCharacter.value.trim())) {
    form.value.characters.push(newCharacter.value.trim())
    newCharacter.value = ''
  }
}

const removeCharacter = (char: string) => {
  form.value.characters = form.value.characters.filter(c => c !== char)
}

const createProject = async () => {
  if (!form.value.title || !form.value.story_start || !form.value.story_end) {
    ElMessage.warning('请填写必填项')
    return
  }
  if (creationType.value === 'fanwork' && !form.value.source_work.trim()) {
    ElMessage.warning('请填写二创原作名称')
    return
  }
  if (creationType.value === 'original') {
    form.value.source_work = ''
  }

  loading.value = true
  try {
    const response = await storyPathApi.projects.create({
      title: form.value.title.trim(),
      characters: form.value.characters.map((name) => ({ name })),
      story_start: form.value.story_start,
      story_end: form.value.story_end,
      style: form.value.style,
      source_work: form.value.source_work || null,
      pace: form.value.pace,
      extra_requirements: form.value.extra_requirements || null,
    })
    ElMessage.success('项目创建成功')
    router.push(`/project/${response.data.id}`)
  } catch (error) {
    ElMessage.error('创建失败，请重试')
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.project-create-container {
  min-height: calc(100vh - 56px);
  display: flex;
  justify-content: center;
  align-items: center;
  padding: 40px 20px;
  background: #eef1f4;
}

.create-card {
  width: 100%;
  max-width: 680px;
  background: white;
  border: 1px solid #dfe3e8;
  border-radius: 8px;
  padding: 40px;
  box-shadow: 0 8px 24px rgba(28, 39, 49, 0.08);
}

.title {
  margin-bottom: 24px;
  color: #222930;
  font-size: 24px;
  font-weight: 680;
}

.create-form {
  margin-top: 24px;
}

.create-form :deep(.el-radio-group) {
  display: flex;
  flex-wrap: wrap;
}

.character-tag {
  margin-right: 8px;
  margin-bottom: 8px;
}

.character-input {
  margin-top: 8px;
}

.create-btn {
  width: 100%;
  height: 48px;
  font-size: 16px;
  font-weight: bold;
}

@media (max-width: 600px) {
  .project-create-container {
    align-items: flex-start;
    padding: 20px 12px;
  }

  .create-card {
    padding: 24px 18px;
  }

  .title {
    font-size: 21px;
  }
}
</style>
