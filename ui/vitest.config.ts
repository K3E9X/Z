import { defineConfig, mergeConfig } from 'vitest/config'
import viteConfig from './vite.config'

export default mergeConfig(
  viteConfig,
  defineConfig({
    test: {
      environment: 'jsdom',
      environmentOptions: { jsdom: { url: 'http://z.test/' } },
      globals: false,
      setupFiles: ['./tests/setup.ts'],
      include: ['src/**/*.test.{ts,tsx}', 'tests/**/*.test.{ts,tsx}'],
      css: false,
      restoreMocks: true,
    },
  }),
)
