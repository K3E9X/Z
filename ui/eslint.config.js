import js from '@eslint/js'
import reactHooks from 'eslint-plugin-react-hooks'
import { reactRefresh } from 'eslint-plugin-react-refresh'
import { defineConfig, globalIgnores } from 'eslint/config'
import globals from 'globals'
import tseslint from 'typescript-eslint'

export default defineConfig([
  globalIgnores(['dist', 'node_modules', 'coverage', 'e2e/out', 'src/api/*.gen.ts']),
  {
    files: ['**/*.{ts,tsx}'],
    extends: [
      js.configs.recommended,
      tseslint.configs.strictTypeChecked,
      reactHooks.configs.flat.recommended,
      reactRefresh.configs.vite(),
    ],
    languageOptions: {
      ecmaVersion: 2023,
      globals: globals.browser,
      parserOptions: {
        projectService: true,
        tsconfigRootDir: import.meta.dirname,
      },
    },
    rules: {
      // Untrusted content (tool results, tickets, agent markdown) is rendered as text only.
      'no-restricted-syntax': [
        'error',
        {
          selector: "JSXAttribute[name.name='dangerouslySetInnerHTML']",
          message: 'Never inject HTML: render untrusted content as text or through <Markdown> (no raw HTML).',
        },
        {
          selector: "MemberExpression[property.name='innerHTML']",
          message: 'Never inject HTML.',
        },
      ],
      'no-restricted-imports': [
        'error',
        {
          paths: [{ name: 'rehype-raw', message: 'Raw HTML in markdown is not allowed.' }],
        },
      ],
      'no-restricted-globals': [
        'error',
        { name: 'localStorage', message: 'Use lib/storage (preferences only, never tokens).' },
        { name: 'sessionStorage', message: 'Use lib/storage (preferences only, never tokens).' },
      ],
      '@typescript-eslint/restrict-template-expressions': ['error', { allowNumber: true }],
      '@typescript-eslint/no-unused-vars': ['error', { argsIgnorePattern: '^_', varsIgnorePattern: '^_' }],
      '@typescript-eslint/no-confusing-void-expression': ['error', { ignoreArrowShorthand: true }],
      '@typescript-eslint/no-misused-promises': ['error', { checksVoidReturn: { attributes: false } }],
      // Hooks and helpers live next to their provider components; fast refresh falls back to a reload.
      'react-refresh/only-export-components': 'off',
      // API responses are typed from the contract, but the UI stays defensive against older servers
      // (fields the contract marks required may be missing on a server that lags the contract).
      '@typescript-eslint/no-unnecessary-condition': 'off',
    },
  },
  {
    files: ['tests/**/*.{ts,tsx}', 'src/**/*.test.{ts,tsx}'],
    rules: {
      '@typescript-eslint/no-non-null-assertion': 'off',
      '@typescript-eslint/no-unsafe-member-access': 'off',
      '@typescript-eslint/no-unsafe-assignment': 'off',
      '@typescript-eslint/unbound-method': 'off',
      '@typescript-eslint/no-deprecated': 'off',
      '@typescript-eslint/no-unnecessary-type-assertion': 'off',
      'no-restricted-globals': 'off',
    },
  },
  {
    files: ['**/*.{js,mjs}'],
    extends: [js.configs.recommended],
    languageOptions: { globals: { ...globals.node, ...globals.browser } },
  },
])
