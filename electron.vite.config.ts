import { resolve } from 'path'
import { defineConfig, externalizeDepsPlugin } from 'electron-vite'
import react from '@vitejs/plugin-react'
import { loadEnv } from 'vite'
import { encodeApiKey } from './src/shared/keyObfuscation'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode ?? 'production', process.cwd(), '')
  const apiKey =
    process.env.FRAME_CYBERDECK_API_KEY?.trim() ||
    process.env.VRSRC_API_KEY?.trim() ||
    env.FRAME_CYBERDECK_API_KEY?.trim() ||
    env.VRSRC_API_KEY?.trim() ||
    ''
  const clientId =
    process.env.FRAME_CYBERDECK_CLIENT_ID?.trim() ||
    process.env.VRSRC_CLIENT_ID?.trim() ||
    env.FRAME_CYBERDECK_CLIENT_ID?.trim() ||
    env.VRSRC_CLIENT_ID?.trim() ||
    ''
  const buildSecret =
    process.env.FRAME_CYBERDECK_BUILD_SECRET?.trim() ||
    process.env.VRSRC_BUILD_SECRET?.trim() ||
    env.FRAME_CYBERDECK_BUILD_SECRET?.trim() ||
    env.VRSRC_BUILD_SECRET?.trim() ||
    ''

  if (clientId && buildSecret) {
    console.log(
      '[electron.vite.config] Short-lived catalog key exchange configured; protected catalog sync will mint keys at runtime.'
    )
  } else if (!apiKey) {
    console.log(
      '[electron.vite.config] No catalog credential configured; protected catalog sync will stay disabled. Local sideloading and Steam Frame management remain available.'
    )
  } else if (clientId || buildSecret) {
    console.warn(
      '[electron.vite.config] Both a client id and a build secret are required for short-lived keys; falling back to the static catalog key.'
    )
  }

  return {
    main: {
      plugins: [externalizeDepsPlugin()],
      define: {
        // Embedded obfuscated (see src/shared/keyObfuscation.ts) rather than as
        // a raw string so the packaged app doesn't contain the plaintext value.
        'process.env.VRSRC_API_KEY_ENC': JSON.stringify(encodeApiKey(apiKey)),
        'process.env.VRSRC_CLIENT_ID': JSON.stringify(clientId),
        'process.env.VRSRC_BUILD_SECRET_ENC': JSON.stringify(encodeApiKey(buildSecret))
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
      plugins: [
        react(),
        {
          name: 'development-csp',
          apply: 'serve',
          transformIndexHtml: (html) =>
            html.replace(/\s*<meta http-equiv="Content-Security-Policy"[^>]+>/, '')
        }
      ]
    }
  }
})
