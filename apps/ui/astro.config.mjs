// @ts-check
import { defineConfig } from 'astro/config';

import react from '@astrojs/react';

// https://astro.build/config
export default defineConfig({
  integrations: [react()],
  base: process.env.SAGASMITH_UI_BASE || '/',
  site: process.env.SAGASMITH_UI_SITE || 'http://localhost:4321',
});
