// ESLint flat config for the Next.js frontend.
//
// Follows the pattern from the official Next.js ESLint documentation:
//   https://nextjs.org/docs/app/api-reference/config/eslint
//
// This file replaces what @next/codemod@canary generated — that output
// was broken in two ways: missing .js extensions, and attempting to
// spread a non-array export. The docs-endorsed pattern below sidesteps
// both issues by using the package's modern exports map.

import { defineConfig, globalIgnores } from 'eslint/config'
import nextVitals from 'eslint-config-next/core-web-vitals'
import nextTs from 'eslint-config-next/typescript'

const eslintConfig = defineConfig([
  // Next.js Core Web Vitals rules (performance-focused; upgrades some
  // @next/eslint-plugin-next warnings to errors).
  ...nextVitals,

  // TypeScript-specific lint rules — included by `create-next-app --typescript`
  // by default. Remove this line if the project ever moves off TypeScript.
  ...nextTs,

  // Paths to ignore globally. Defaults from eslint-config-next are listed
  // explicitly here because passing a globalIgnores() call overrides them
  // entirely rather than extending them, per the Next.js docs.
  globalIgnores([
    '.next/**',
    'out/**',
    'build/**',
    'next-env.d.ts',
  ]),
])

export default eslintConfig
