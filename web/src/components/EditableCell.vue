<script lang="ts">
export interface SelectOption {
  label: string
  value: string
}
</script>

<script setup lang="ts">
import { ref, computed, nextTick, watch } from 'vue'

const props = withDefaults(defineProps<{
  value: string | number | null | undefined
  editing: boolean
  type?: 'text' | 'number' | 'select'
  suffix?: string
  options?: SelectOption[]
  min?: number
  max?: number
  nullable?: boolean
  disabled?: boolean
}>(), {
  type: 'text',
  nullable: false,
  disabled: false,
})

const emit = defineEmits<{
  (e: 'start'): void
  (e: 'save', value: string | number | null): void
  (e: 'cancel'): void
}>()

const inputRef = ref<any>(null)
const selectRef = ref<any>(null)
const localValue = ref<string | number | null>(props.value ?? null)

watch(() => props.editing, async (val) => {
  if (val) {
    localValue.value = props.value ?? null
    await nextTick()
    if (props.type === 'select') {
      selectRef.value?.focus?.()
    } else {
      inputRef.value?.focus?.()
      inputRef.value?.select?.()
    }
  }
})

function handleSave() {
  emit('save', localValue.value)
}

function handleKeydown(e: KeyboardEvent) {
  if (e.key === 'Enter') {
    e.preventDefault()
    handleSave()
  } else if (e.key === 'Escape') {
    e.preventDefault()
    emit('cancel')
  }
}

function handleClick() {
  if (!props.disabled) {
    emit('start')
  }
}

const displayValue = computed(() => {
  const v = props.value
  if (v === null || v === undefined || v === '') {
    return props.nullable ? '-' : ''
  }
  if (props.type === 'select' && props.options) {
    const opt = props.options.find(o => o.value === v)
    if (opt) return opt.label
  }
  return props.suffix ? `${v}${props.suffix}` : String(v)
})
</script>

<template>
  <div class="editable-cell" :class="{ disabled }" @click="handleClick">
    <template v-if="editing">
      <el-input
        v-if="type === 'text'"
        ref="inputRef"
        v-model="localValue"
        size="small"
        @blur="handleSave"
        @keydown="handleKeydown"
      />
      <el-input-number
        v-else-if="type === 'number'"
        ref="inputRef"
        v-model="localValue"
        size="small"
        :min="min"
        :max="max"
        controls-position="right"
        style="width: 100%"
        @blur="handleSave"
        @keydown="handleKeydown"
      />
      <el-select
        v-else-if="type === 'select'"
        ref="selectRef"
        v-model="localValue"
        size="small"
        clearable
        style="width: 100%"
        @change="handleSave"
        @visible-change="(visible: boolean) => { if (!visible) handleSave() }"
        @keydown="handleKeydown"
      >
        <el-option
          v-for="opt in options"
          :key="opt.value"
          :label="opt.label"
          :value="opt.value"
        />
      </el-select>
    </template>
    <span v-else class="display-text" :class="{ editable: !disabled }">
      {{ displayValue }}
    </span>
  </div>
</template>

<style scoped>
.editable-cell {
  width: 100%;
  min-height: 23px;
}
.editable-cell.disabled {
  cursor: not-allowed;
}
.display-text.editable {
  cursor: pointer;
  border-bottom: 1px dashed transparent;
  transition: border-color 0.2s;
}
.editable-cell:not(.disabled):hover .display-text.editable {
  border-bottom-color: var(--el-color-primary);
}
</style>
