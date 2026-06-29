import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

const originalError = console.error
console.error = (...args: unknown[]) => {
  const msg = args.map(a => {
    if (typeof a === 'string') return a
    if (a instanceof Error) return a.message
    return ''
  }).join(' ')
  if (msg.includes('ECONNABORTED')) return
  return originalError.apply(console, args as any[])
}

export default defineConfig({
  plugins: [
    react(),
    {
      name: 'ignore-econnaborted',
      configureServer(server) {
        server.httpServer?.on('upgrade', (_req: unknown, socket: any) => {
          socket.on('error', (err: any) => {
            if (err.code === 'ECONNABORTED') err.preventDefault?.()
          })
        })
        const httpServer = server.httpServer
        if (!httpServer) return
        const origEmit = httpServer.emit.bind(httpServer)
        httpServer.emit = function (event: string | symbol, ...args: any[]) {
          if (event === 'error' && args[0]?.code === 'ECONNABORTED') return false
          return origEmit(event as string, ...args)
        }
      },
    },
  ],
  server: {
    port: 5173,
    hmr: {
      overlay: false,
    },
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
      '/ws': {
        target: 'ws://127.0.0.1:8000',
        ws: true,
        changeOrigin: true,
        configure: (proxy) => {
          proxy.on('error', (err) => {
            const code = (err as NodeJS.ErrnoException).code
            if (code === 'ECONNABORTED' || code === 'ECONNREFUSED') return
            console.error('ws proxy error:', err)
          })
          proxy.on('proxyReqWs', (_req) => {})
          proxy.on('open', () => {})
        },
      },
      '/static': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
      '/alert': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
  },
})
