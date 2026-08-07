# clynotion

Clinical notation — a lightweight web app for writing and organizing clinical notes.

## Tech stack

- **Frontend:** React 18 + TypeScript
- **Build tool / dev server:** Vite 5
- **Tests:** Vitest + React Testing Library (jsdom)
- **Lint:** ESLint 8 (`.eslintrc.cjs`)
- **Package manager:** npm (see `package-lock.json`)
- **Storage:** browser `localStorage` (no backend, database, or external services required)

## Commands

See `package.json` `scripts` for the source of truth. Common ones:

- `npm run dev` — start the Vite dev server (http://localhost:5173)
- `npm run build` — type-check (`tsc -b`) then production build
- `npm run lint` — run ESLint
- `npm run typecheck` — type-check only, no emit
- `npm test` — run the Vitest suite once (`npm run test:watch` for watch mode)

## Cursor Cloud specific instructions

- This is a **single, self-contained frontend service**. There is no backend, database, or secret to configure — notes persist in the browser's `localStorage`, so a fresh session always starts with no notes.
- The dev server is bound with `host: true` on port `5173` (see `vite.config.ts`); use `http://localhost:5173` locally.
- The core "hello world" flow is creating a clinical note: fill Patient name + Note title (both required) + Note body, then click **Save note**. The saved note appears in the Notes list and survives a page reload.
- Lint uses the classic `.eslintrc.cjs` (ESLint 8). Test globals (`describe`/`it`/`expect`/…) are declared under `overrides` there rather than via an eslint plugin, so no `eslint-plugin-vitest` is needed.
