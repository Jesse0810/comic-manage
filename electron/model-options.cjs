'use strict'

const fields = {
  reasoning: new Set(['default', 'none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max']),
  speed: new Set(['default', 'standard', 'fast', 'ultrafast']),
}
const quantities = {
  input_tokens: [1, 1000000, true], output_tokens: [1, 16384, true],
  concurrency: [0, 8, true], rpm: [0, 6000, true], timeout_seconds: [10, 600, true],
  temperature: [0, 2, false], top_p: [0, 1, false], top_k: [1, 1024, true],
}

function modelOptions(value, quick = false) {
  if (value === undefined) return {}
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('模型参数格式无效。')
  const result = {}
  for (const [key, item] of Object.entries(value)) {
    if (Object.hasOwn(fields, key) && fields[key].has(item)) {
      result[key] = item
    } else if (!quick && Object.hasOwn(quantities, key)) {
      const [low, high, integer] = quantities[key]
      if (typeof item !== 'number' || !Number.isFinite(item) || item < low || item > high || (integer && !Number.isInteger(item))) {
        throw new Error(`模型参数 ${key} 超出范围。`)
      }
      result[key] = item
    } else {
      throw new Error('包含无效或未支持的模型参数。')
    }
  }
  return result
}

module.exports = { modelOptions }
