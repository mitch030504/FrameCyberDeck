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

  const releaseBuild = process.env.FRAME_CYBERDECK_RELEASE_BUILD === '1'
  const dynamicCredentials = Boolean(clientId && buildSecret)

  if (releaseBuild) {
    if (!dynamicCredentials) {
      throw new Error(
        'Release builds require VRSRC_CLIENT_ID and VRSRC_BUILD_SECRET for the short-lived catalog key exchange.'
      )
    }
    if (apiKey) {
      throw new Error(
        'Release builds must not embed a static VRSRC_API_KEY. Remove the secret from the release workflow.'
      )
    }
  } else if (clientId || buildSecret) {
    if (!dynamicCredentials) {
      console.warn(
        '[electron.vite.config] Both a client id and a build secret are required for short-lived keys; using the static catalog key.'
      )
    } else if (apiKey) {
      console.warn(
        '[electron.vite.config] Both static and short-lived credentials are configured; ignoring the static key.'
      )
    }
  }

  if (dynamicCredentials) {
    console.log(
      '[electron.vite.config] Short-lived catalog key exchange configured; protected catalog sync will mint keys at runtime.'
    )
  } else if (!apiKey) {
    console.log(
      '[electron.vite.config] No catalog credential configured; protected catalog sync will stay disabled. Local sideloading and Steam Frame management remain available.'
    )
  }

  return {
    main: {
      plugins: [externalizeDepsPlugin()],
      define: {
        // Embedded obfuscated (see src/shared/keyObfuscation.ts) rather than as
        // a raw string so the packaged app doesn't contain the plaintext value.
        // Release builds never embed a static key; dynamic credentials win.
        'process.env.VRSRC_API_KEY_ENC': JSON.stringify(
          encodeApiKey(dynamicCredentials ? '' : apiKey)
        ),
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
