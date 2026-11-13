'use strict'

const http = require('node:http')

async function createKomgaFixture(apiKey) {
  const books = new Map()
  const series = new Map()
  const patches = []
  let page
  const server = http.createServer(async (request, response) => {
    response.setHeader('Content-Type', 'application/json')
    if (request.headers['x-api-key'] !== apiKey) {
      response.writeHead(401).end('{}')
      return
    }
    const url = new URL(request.url, 'http://localhost')
    const route = url.pathname.replace(/^\/komga\//, '/')
    const pageMatch = route.match(/^\/api\/v1\/books\/([^/]+)\/pages\/(\d+)$/)
    if (request.method === 'GET' && pageMatch && books.has(decodeURIComponent(pageMatch[1])) && page) {
      response.setHeader('Content-Type', 'image/png')
      response.end(page)
      return
    }
    let text = ''
    for await (const chunk of request) text += chunk
    const body = text ? JSON.parse(text) : undefined
    let result
    if (request.method === 'GET' && route === '/api/v1/libraries') {
      result = [{ id: 'test-library', name: '合成测试书库' }]
    } else if (request.method === 'POST' && route === '/api/v1/books/list') {
      result = { content: [...books.values()], last: true }
    } else {
      const match = route.match(/^\/api\/v1\/(books|series)\/([^/]+)(\/metadata)?$/)
      const collection = match?.[1] === 'books' ? books : series
      const current = match && collection.get(decodeURIComponent(match[2]))
      if (current && request.method === 'GET' && !match[3]) result = current
      if (current && request.method === 'PATCH' && match[3]) {
        patches.push({ kind: match[1], id: current.id, body })
        Object.assign(current.metadata, body)
        response.writeHead(204).end()
        return
      }
    }
    if (result === undefined) response.writeHead(404).end('{}')
    else response.end(JSON.stringify(result))
  })
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
  return {
    url: `http://127.0.0.1:${server.address().port}/komga`,
    books, series, patches,
    setPage: value => { page = value },
    close: () => new Promise(resolve => server.close(resolve)),
  }
}

module.exports = { createKomgaFixture }
