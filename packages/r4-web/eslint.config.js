import tseslint from 'typescript-eslint';

export default [
  { ignores: ['dist', 'node_modules', 'vite.config.ts'] },
  ...tseslint.configs.recommended.map((c) => ({
    ...c,
    files: ['src/**/*.{ts,tsx}'],
  })),
  {
    files: ['src/**/*.{ts,tsx}'],
    rules: {
      '@typescript-eslint/no-explicit-any': 'off',
      '@typescript-eslint/no-unused-vars': 'off',
      '@typescript-eslint/no-empty-object-type': 'off',
      '@typescript-eslint/no-namespace': 'off',
      'no-empty': 'off',
      'no-useless-catch': 'off',
      'no-useless-escape': 'off',
    },
  },
];
