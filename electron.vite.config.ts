import { resolve } from 'path'
import { defineConfig, externalizeDepsPlugin } from 'electron-vite'
import react from '@vitejs/plugin-react'
import { encodeApiKey } from './src/shared/keyObfuscation'

export default defineConfig(() => {
  const apiKey =
    process.env.FRAME_CYBERDECK_API_KEY?.trim() || process.env.VRSRC_API_KEY?.trim() || ''

  if (!apiKey) {
    console.log(
      '[electron.vite.config] No catalog API key configured; protected catalog sync will stay disabled. Local sideloading and Steam Frame management remain available.'
    )
  }

  return {
    main: {
      plugins: [externalizeDepsPlugin()],
      define: {
        // Embedded obfuscated (see src/shared/keyObfuscation.ts) rather than as
        // a raw string so the packaged app doesn't contain the plaintext key.
        'process.env.VRSRC_API_KEY_ENC': JSON.stringify(encodeApiKey(apiKey))
      },
      resolve: {
        alias: {
          '@shared': resolve('src/shared')
        }
      }
    },
    preload: {
      plugins: [externalizeDepsPlugin()],
      resolve: {
        alias: {
          '@shared': resolve('src/shared')
        }
      }
    },
    renderer: {
      resolve: {
        alias: {
          '@renderer': resolve('src/renderer/src'),
          '@shared': resolve('src/shared')
        }
      },
      plugins: [react()]
    }
  }
})
