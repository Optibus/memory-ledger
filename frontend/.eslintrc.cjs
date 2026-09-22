/**
 * ESLint for the brain UI. ESLint 8 with no config only globs `.js`, so without
 * this file `eslint src` matched nothing and `npm run lint` could never pass.
 */
module.exports = {
  root: true,
  env: { browser: true, es2022: true },
  parser: "@typescript-eslint/parser",
  parserOptions: { ecmaVersion: "latest", sourceType: "module", ecmaFeatures: { jsx: true } },
  plugins: ["@typescript-eslint", "react-hooks"],
  extends: [
    "eslint:recommended",
    "plugin:@typescript-eslint/recommended",
    "plugin:react-hooks/recommended",
  ],
  ignorePatterns: ["dist", "node_modules", "coverage"],
  overrides: [
    {
      files: ["**/*.test.ts", "**/*.test.tsx", "src/test/**"],
      env: { node: true },
    },
  ],
};
