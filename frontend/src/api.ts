// Keep automatic page/XML reads from racing long-running catalog operations.
let requests: Promise<unknown> = Promise.resolve()
export function invoke<T = any>(method: string, params: Record<string, unknown> = {}): Promise<T> {
  const payload = JSON.parse(JSON.stringify(params))
  const run = () => window.comic.invoke(method, payload) as Promise<T>
  if (method === 'cancel') return run()
  const result = requests.then(run, run)
  requests = result.catch(() => undefined)
  return result
}
