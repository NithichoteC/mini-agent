import js from '@eslint/js'
import { plugin as shadcn } from '@shadcn/lint'
import globals from 'globals'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import tseslint from 'typescript-eslint'
import { defineConfig, globalIgnores } from 'eslint/config'

export default defineConfig([
  globalIgnores(['dist']),
  {
    files: ['**/*.{ts,tsx}'],
    extends: [
      js.configs.recommended,
      tseslint.configs.recommended,
      reactHooks.configs.flat.recommended,
      reactRefresh.configs.vite,
    ],
    languageOptions: {
      globals: globals.browser,
    },
    plugins: { shadcn },
    rules: {
      'react-refresh/only-export-components': 'off',
      // @shadcn/lint: className is for layout; appearance comes from variants and theme tokens
      'shadcn/no-restyle': ['error', { allow: ['layout'] }],
      'shadcn/no-raw-colors': 'error',
      'shadcn/no-arbitrary-values': 'error',
      'shadcn/no-inline-styles': 'error',
      'shadcn/no-unknown-classes': 'error',
      'shadcn/require-static-classes': 'error',
    },
  },
  {
    // code added by the shadcn / assistant-ui CLIs: it styles its own internals
    files: ['src/components/ui/**', 'src/components/assistant-ui/**', 'src/hooks/**'],
    rules: {
      'shadcn/no-restyle': 'off',
      'shadcn/no-raw-colors': 'off',
      'shadcn/no-arbitrary-values': 'off',
      'shadcn/no-inline-styles': 'off',
      'shadcn/no-unknown-classes': 'off',
      'shadcn/require-static-classes': 'off',
      'react-hooks/set-state-in-effect': 'off',
      'react-hooks/refs': 'off',
      'react-hooks/exhaustive-deps': 'off',
      'no-empty': 'off',
    },
    linterOptions: { reportUnusedDisableDirectives: 'off' },
  },
])
