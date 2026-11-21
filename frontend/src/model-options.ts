import type { ModelOptions, ModelProfile, ModelReasoning, ModelSpeed } from './types'

type ModelTarget = Pick<ModelProfile, 'provider' | 'model' | 'base_url'>

const reasoningLabels: Record<ModelReasoning, string> = {
  default: '服务默认', none: '关闭', minimal: '极低 · Minimal', low: '低 · Low',
  medium: '中 · Medium', high: '高 · High', xhigh: '极高 · XHigh', max: '最高 · Max',
}
const speedLabels: Record<ModelSpeed, string> = {
  default: '服务默认', standard: '标准 · Standard', fast: '快速 · Fast', ultrafast: '极速 · UltraFast',
}
const effort: ModelReasoning[] = ['low', 'medium', 'high']

export function reasoningLabel(value?: ModelReasoning): string {
  return reasoningLabels[value ?? 'default']
}

export function speedLabel(value?: ModelSpeed): string {
  return speedLabels[value ?? 'default']
}

const geminiThinking: [string, ModelReasoning[]][] = [
  ['gemini-4-neon', [...effort, 'xhigh']],
  ['gemini-4-hydrogen', [...effort, 'xhigh']],
  ['gemini-4-carbon', effort], ['gemini-4-argon', effort],
  ['gemini-3.8-flash', effort],
  ['gemini-3.6-flash', ['minimal', ...effort]],
  ['gemini-3.5-flash-lite', ['minimal', ...effort]],
  ['gemini-3.1-flash-lite-image', ['minimal', 'high']],
  ['gemini-3.1-flash-lite', ['minimal', ...effort]],
  ['gemini-3.1-pro', effort], ['gemini-3-flash', ['minimal', ...effort]], ['gemini-3-pro', ['low', 'high']],
  ['gemini-robotics-er-2', ['minimal', ...effort]],
]

function anthropicThinking(model: string): ModelReasoning[] {
  if (model === 'claude-mythos-preview') return [...effort, 'max']
  const family = /^claude-(opus|sonnet|haiku|fable|mythos)-(4|5)(?:-([0-9])(?=-|$))?(?:-|$)/.exec(model)
  if (!family) return model.startsWith('claude-3-7-sonnet') ? ['none', ...effort] : []
  const [,kind,major,minor] = family
  const levels: ModelReasoning[] = ['fable', 'mythos'].includes(kind) ? [...effort] : ['none', ...effort]
  if (major === '5' || (major === '4' && kind === 'opus' && Number(minor) >= 7)) levels.push('xhigh')
  if (major === '5' || (major === '4' && ['opus', 'sonnet'].includes(kind) && Number(minor) >= 6)) levels.push('max')
  return levels
}

export function reasoningOptions(profile?: ModelTarget | null) {
  const model = profile?.model.trim().toLowerCase().replace(/^models\//, '') ?? ''
  let levels: ModelReasoning[] = []
  switch (profile?.provider) {
    case 'openai':
    case 'openai-responses':
    case 'chatgpt-plan':
      levels = /(?:^|\/)gpt-6\.2-(?:sol|fa(?:ble|st)?)(?:[-.:]|$)/.test(model)
        ? [...effort, 'xhigh', 'max']
        : ['none', 'minimal', ...effort, 'xhigh', 'max']
      break
    case 'ollama':
      levels = /(?:^|\/)gpt-oss(?::|$)/.test(model) ? effort : ['none', 'low']
      break
    case 'anthropic':
      levels = anthropicThinking(model)
      break
    case 'gemini':
      if (model.startsWith('gemini-2.5-pro')) levels = effort
      else if (model.startsWith('gemini-2.5-flash')) levels = ['none', ...effort]
      else levels = geminiThinking.find(([prefix]) => model === prefix || model.startsWith(prefix + '-'))?.[1] ?? []
      break
  }
  return (['default', ...levels] as ModelReasoning[]).map(value => ({ label: reasoningLabel(value), value }))
}

export function speedOptions(profile?: ModelTarget | null) {
  const levels: ModelSpeed[] = profile?.provider === 'openai-responses' || profile?.provider === 'chatgpt-plan'
    ? ['default', 'standard', 'fast', 'ultrafast']
    : ['default']
  return levels.map(value => ({ label: speedLabel(value), value }))
}

export type NumericModelOption = Exclude<keyof ModelOptions, 'reasoning' | 'speed'>

interface NumericField {
  key: NumericModelOption
  label: string
  min: number
  max: number
  step: number
  hint: string
}

export function taskOptionFields(profile: ModelTarget): NumericField[] {
  const fields: NumericField[] = [
    { key: 'input_tokens', label: '输入 Token 限制', min: 1, max: 1000000, step: 1, hint: '按请求文本估算。Agent 会在可行时分批发送。' },
    { key: 'concurrency', label: '并发任务数', min: 0, max: 8, step: 1, hint: '0 或留空使用默认的串行处理。' },
    { key: 'rpm', label: '每分钟请求数 · RPM', min: 0, max: 6000, step: 1, hint: '0 或留空时采用服务商的限制。' },
    { key: 'timeout_seconds', label: '请求超时（秒）', min: 10, max: 600, step: 1, hint: '等待单次模型响应的最长时间。' },
  ]
  if (profile.provider !== 'chatgpt-plan') {
    fields.splice(1, 0, { key: 'output_tokens', label: '输出 Token 限制', min: 1, max: 16384, step: 1, hint: '单次生成的输出上限。' })
  }
  return fields
}

export function samplingOptionFields(profile: ModelTarget, reasoning?: ModelReasoning): NumericField[] {
  if (profile.provider === 'chatgpt-plan') return []
  if (profile.provider === 'anthropic' && reasoning && reasoning !== 'default' && reasoning !== 'none') return []
  const fields: NumericField[] = [
    { key: 'temperature', label: '温度 · Temperature', min: 0, max: profile.provider === 'anthropic' ? 1 : 2, step: .05, hint: '较高的值会增加输出变化。' },
    { key: 'top_p', label: '核采样 · Top P', min: 0, max: 1, step: .05, hint: '限制抽样候选的累计概率。' },
  ]
  if (['anthropic', 'gemini', 'ollama'].includes(profile.provider)) {
    fields.push({ key: 'top_k', label: '候选数 · Top K', min: 1, max: 1024, step: 1, hint: '只在概率最高的 K 个候选中抽样。' })
  }
  return fields
}

export function profileOptions(profile: ModelTarget, options: ModelOptions): ModelOptions {
  const result: ModelOptions = {}
  if (options.reasoning && options.reasoning !== 'default' && reasoningOptions(profile).some(level => level.value === options.reasoning)) {
    result.reasoning = options.reasoning
  }
  if (options.speed && options.speed !== 'default' && speedOptions(profile).some(level => level.value === options.speed)) {
    result.speed = options.speed
  }
  for (const field of [...taskOptionFields(profile), ...samplingOptionFields(profile, result.reasoning)]) {
    const value = options[field.key]
    if (typeof value === 'number' && Number.isFinite(value)) result[field.key] = value
  }
  return result
}
