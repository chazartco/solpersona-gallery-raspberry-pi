import { readFileSync } from 'node:fs';
import { defineConfig } from 'vitest/config';

// One version for the whole app: the agent's. The player reloads itself if the
// agent it talks to is a different release (an update went live under it).
const agentInit = readFileSync(new URL('../agent/solpersona_gallery/__init__.py', import.meta.url), 'utf8');
const version = /__version__ = "([^"]+)"/.exec(agentInit)?.[1] ?? 'dev';

export default defineConfig(({ command }) => ({
  // Served by the agent from its root; built into static files baked into the image.
  base: '/',
  define: command === 'build' ? { 'import.meta.env.VITE_APP_VERSION': JSON.stringify(version) } : {},
  build: {
    outDir: 'dist',
    target: 'chrome110',
    assetsInlineLimit: 0,
    sourcemap: false,
  },
  server: { port: 5173 },
  test: {
    environment: 'jsdom',
  },
}));
