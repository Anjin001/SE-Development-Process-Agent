import { fileURLToPath } from 'node:url'
import { defineConfig, loadEnv, searchForWorkspaceRoot } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig(({ mode }) => {
  const target = loadEnv(mode, process.cwd(), 'WORKBENCH_').WORKBENCH_PROXY_TARGET
  const proxy = target ? { '/api/v2': { target, changeOrigin: true } } : undefined
  return {
    plugins: [react()],
    build: {
      rollupOptions: {
        output: {
          onlyExplicitManualChunks: true,
          manualChunks(id) {
            const path = id.replaceAll('\\', '/')
            if (path.includes('/node_modules/@xyflow/') || path.includes('/node_modules/@dagrejs/')) return 'dependency-graph'
          },
        },
      },
    },
    server: {
      proxy,
      fs: {
        allow: [
          searchForWorkspaceRoot(process.cwd()),
          fileURLToPath(new URL('../../examples/v2/scenario.json', import.meta.url)),
        ],
      },
    },
    preview: { proxy },
  }
})
