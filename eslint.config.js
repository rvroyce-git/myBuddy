// ESLint flat config for the website's JavaScript (no build step: plain ES modules in site/).
// Run: npm ci && npx eslint .   (CI: the `lint-js` job)
import js from '@eslint/js';
import globals from 'globals';

export default [
  { ignores: ['node_modules/', '.venv/', 'site/data/'] },
  js.configs.recommended,
  {
    files: ['site/**/*.js'],
    languageOptions: {
      ecmaVersion: 2024,
      sourceType: 'module',
      globals: { ...globals.browser },
    },
    rules: {
      // `catch (e) { /* ignore */ }` is the house style for best-effort browser APIs.
      'no-unused-vars': ['error', { caughtErrors: 'none' }],
    },
  },
  {
    files: ['eslint.config.js'],
    languageOptions: { sourceType: 'module', globals: { ...globals.node } },
  },
];
