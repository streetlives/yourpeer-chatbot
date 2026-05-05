// Copyright (c) 2024 Streetlives, Inc.
//
// Use of this source code is governed by an MIT-style
// license that can be found in the LICENSE file or at
// https://opensource.org/licenses/MIT.

/**
 * Standalone verification for src/lib/theme.ts.
 *
 * Run with: `npm run verify:theme` from frontend-next/, or
 * `node scripts/verify/theme.mjs` directly. No jest/vitest dependency
 * — the codebase philosophy is minimal deps, and the theme helpers
 * are pure enough that stubbing `window`, `document`, and
 * `localStorage` in 30 lines is cleaner than pulling in a full test
 * harness just for 6 functions.
 *
 * If/when a frontend test runner is added to the repo, the assertions
 * below can be lifted verbatim into a proper `theme.test.ts`. For now,
 * running this script gives equivalent coverage.
 *
 * Exit code is non-zero on any failure, so CI can invoke this as a
 * preflight without test-framework integration.
 */

import assert from "node:assert/strict";
import { readFileSync, writeFileSync, mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

// ---------------------------------------------------------------------------
// Load src/lib/theme.ts as ES module. Next would transpile TS → JS; we
// mimic that with a minimal swc-free strip of TS-only syntax (just type
// annotations). Since theme.ts is dependency-free (no framework imports)
// this works fine for verification purposes.
// ---------------------------------------------------------------------------

const themeSrc = readFileSync(
  new URL("../../src/lib/theme.ts", import.meta.url),
  "utf8",
);

// Crude TS→JS: strip the type syntax we actually use. We only need to
// support what theme.ts contains: union string types, function return
// annotations, export type statements. More elaborate TS features
// would need a real parser — not worth it for 150 lines.
const jsSrc = themeSrc
  .replace(/^export type [^\n]+\n/gm, "") // strip `export type Foo = ...`
  .replace(/:\s*(ThemeChoice|ResolvedTheme|string|void|boolean)(\s*[={,)])/g, "$2") // return + param types
  .replace(/:\s*(ThemeChoice|ResolvedTheme|string|void|boolean)$/gm, "") // return types on `): Foo {`
  .replace(/\): (ThemeChoice|ResolvedTheme|string|void|boolean)\s*\{/g, ") {");

// Write to a temp file and import as ES module.
const tmpDir = mkdtempSync(join(tmpdir(), "yourpeer-theme-verify-"));
const tmpPath = join(tmpDir, "theme.mjs");
writeFileSync(tmpPath, jsSrc);

const mod = await import(tmpPath);
const {
  readStoredChoice,
  writeStoredChoice,
  systemPrefersDark,
  resolveTheme,
  applyResolvedTheme,
  nextChoice,
  THEME_STORAGE_KEY,
  DARK_CLASS,
} = mod;

// ---------------------------------------------------------------------------
// Minimal browser stubs. theme.ts uses only:
//   - window.localStorage.getItem / setItem / removeItem
//   - window.matchMedia("(prefers-color-scheme: dark)").matches
//   - document.documentElement.classList.toggle
// Reset these between tests so each assertion starts clean.
// ---------------------------------------------------------------------------

function installStubs({ stored = null, systemDark = false } = {}) {
  const store = stored === null ? {} : { [THEME_STORAGE_KEY]: stored };
  const classList = {
    _classes: new Set(),
    toggle(name, force) {
      if (force === true) this._classes.add(name);
      else if (force === false) this._classes.delete(name);
      else if (this._classes.has(name)) this._classes.delete(name);
      else this._classes.add(name);
      return this._classes.has(name);
    },
    has(name) { return this._classes.has(name); },
  };
  globalThis.window = {
    localStorage: {
      getItem: (k) => (k in store ? store[k] : null),
      setItem: (k, v) => { store[k] = String(v); },
      removeItem: (k) => { delete store[k]; },
    },
    matchMedia: (query) => ({
      matches: query.includes("dark") && systemDark,
    }),
  };
  globalThis.document = {
    documentElement: { classList },
  };
  return { store, classList };
}

function uninstallStubs() {
  delete globalThis.window;
  delete globalThis.document;
}

// ---------------------------------------------------------------------------
// Test runner. Dead-simple: array of [name, fn] pairs; report pass/fail
// count at the end and exit non-zero if any failed.
// ---------------------------------------------------------------------------

let passed = 0;
let failed = 0;
const failures = [];

function test(name, fn) {
  try {
    fn();
    passed++;
    console.log(`  ✓ ${name}`);
  } catch (err) {
    failed++;
    failures.push({ name, err });
    console.log(`  ✗ ${name}`);
    console.log(`      ${err.message}`);
  } finally {
    uninstallStubs();
  }
}

console.log("Verifying src/lib/theme.ts\n");

// --- readStoredChoice ---

test("readStoredChoice returns 'system' when nothing stored", () => {
  installStubs({ stored: null });
  assert.equal(readStoredChoice(), "system");
});

test("readStoredChoice returns stored 'light'", () => {
  installStubs({ stored: "light" });
  assert.equal(readStoredChoice(), "light");
});

test("readStoredChoice returns stored 'dark'", () => {
  installStubs({ stored: "dark" });
  assert.equal(readStoredChoice(), "dark");
});

test("readStoredChoice returns 'system' when stored 'system' (round-trip)", () => {
  installStubs({ stored: "system" });
  assert.equal(readStoredChoice(), "system");
});

test("readStoredChoice returns 'system' on junk stored value", () => {
  installStubs({ stored: "bogus" });
  assert.equal(readStoredChoice(), "system");
});

test("readStoredChoice returns 'system' when window is undefined (SSR)", () => {
  // no installStubs — window stays undefined
  assert.equal(readStoredChoice(), "system");
});

test("readStoredChoice swallows localStorage throws", () => {
  globalThis.window = {
    localStorage: { getItem: () => { throw new Error("denied"); } },
  };
  globalThis.document = { documentElement: { classList: { toggle() {}, has() { return false; } } } };
  assert.equal(readStoredChoice(), "system");
});

// --- writeStoredChoice ---

test("writeStoredChoice('dark') persists value", () => {
  const { store } = installStubs();
  writeStoredChoice("dark");
  assert.equal(store[THEME_STORAGE_KEY], "dark");
});

test("writeStoredChoice('system') removes the key", () => {
  const { store } = installStubs({ stored: "dark" });
  writeStoredChoice("system");
  assert.equal(store[THEME_STORAGE_KEY], undefined);
});

test("writeStoredChoice swallows errors", () => {
  globalThis.window = {
    localStorage: { setItem: () => { throw new Error("denied"); } },
  };
  globalThis.document = { documentElement: { classList: { toggle() {}, has() { return false; } } } };
  // Should not throw
  writeStoredChoice("dark");
});

// --- systemPrefersDark ---

test("systemPrefersDark true when matchMedia matches", () => {
  installStubs({ systemDark: true });
  assert.equal(systemPrefersDark(), true);
});

test("systemPrefersDark false when matchMedia doesn't match", () => {
  installStubs({ systemDark: false });
  assert.equal(systemPrefersDark(), false);
});

test("systemPrefersDark false without matchMedia (old browser)", () => {
  globalThis.window = { localStorage: { getItem: () => null } };
  globalThis.document = { documentElement: { classList: { toggle() {}, has() { return false; } } } };
  assert.equal(systemPrefersDark(), false);
});

// --- resolveTheme ---

test("resolveTheme('light') returns 'light'", () => {
  installStubs();
  assert.equal(resolveTheme("light"), "light");
});

test("resolveTheme('dark') returns 'dark'", () => {
  installStubs();
  assert.equal(resolveTheme("dark"), "dark");
});

test("resolveTheme('system') follows OS preference (light)", () => {
  installStubs({ systemDark: false });
  assert.equal(resolveTheme("system"), "light");
});

test("resolveTheme('system') follows OS preference (dark)", () => {
  installStubs({ systemDark: true });
  assert.equal(resolveTheme("system"), "dark");
});

// --- applyResolvedTheme ---

test("applyResolvedTheme('dark') adds .dark to <html>", () => {
  const { classList } = installStubs();
  applyResolvedTheme("dark");
  assert.equal(classList.has(DARK_CLASS), true);
});

test("applyResolvedTheme('light') removes .dark from <html>", () => {
  const { classList } = installStubs();
  classList.toggle(DARK_CLASS, true); // pre-condition: dark present
  applyResolvedTheme("light");
  assert.equal(classList.has(DARK_CLASS), false);
});

// --- nextChoice ---

test("nextChoice cycles system → light", () => {
  assert.equal(nextChoice("system"), "light");
});

test("nextChoice cycles light → dark", () => {
  assert.equal(nextChoice("light"), "dark");
});

test("nextChoice cycles dark → system", () => {
  assert.equal(nextChoice("dark"), "system");
});

test("nextChoice is cyclic (3 clicks returns to start)", () => {
  assert.equal(nextChoice(nextChoice(nextChoice("system"))), "system");
  assert.equal(nextChoice(nextChoice(nextChoice("light"))), "light");
  assert.equal(nextChoice(nextChoice(nextChoice("dark"))), "dark");
});

// --- integration ---

test("end-to-end: system-user toggles to light", () => {
  const { store, classList } = installStubs({ systemDark: true });
  // Initial state: choice is "system", resolves to dark (OS dark)
  assert.equal(readStoredChoice(), "system");
  assert.equal(resolveTheme("system"), "dark");

  // User clicks toggle → next is "light"
  const next = nextChoice("system");
  assert.equal(next, "light");
  writeStoredChoice(next);
  applyResolvedTheme(resolveTheme(next));

  // Expect: stored = "light", class = light (no .dark)
  assert.equal(store[THEME_STORAGE_KEY], "light");
  assert.equal(classList.has(DARK_CLASS), false);
});

test("end-to-end: choice persists across 'reload'", () => {
  const { store } = installStubs();
  console.log('store', store)
  writeStoredChoice("dark");
  // Simulate reload: same store, re-read
  const reloaded = readStoredChoice();
  assert.equal(reloaded, "dark");
  assert.equal(resolveTheme(reloaded), "dark");
});

// ---------------------------------------------------------------------------
// Static wiring check — locks in that the inline FOUC-prevention script
// in src/app/layout.tsx references the same constants as theme.ts.
//
// Why this exists: an earlier version of the dark-mode work shipped CSS
// for `.theme-transitions-off` but the inline script that was supposed
// to set the class didn't exist (a comment referred to a script that
// hadn't been written). The class was a no-op and users on dark mode
// saw a 120ms color fade on every load. Caught during line-by-line
// PR review.
//
// This check ensures the layout's inline script always references all
// three constants from theme.ts directly. A future refactor that
// inlines the strings or removes the imports would break this check
// before it could ship.
// ---------------------------------------------------------------------------

test("FOUC inline script references THEME_STORAGE_KEY, DARK_CLASS, TRANSITIONS_OFF_CLASS", () => {
  const layoutSrc = readFileSync(
    new URL("../../src/app/layout.tsx", import.meta.url),
    "utf8",
  );
  // The script is constructed via template literal that interpolates
  // the imported constants. Both the import and the references in the
  // template literal must be present.
  assert.match(
    layoutSrc,
    /import\s*{[^}]*THEME_STORAGE_KEY[^}]*}\s*from\s*"@\/lib\/theme"/,
    "layout.tsx must import THEME_STORAGE_KEY",
  );
  assert.match(
    layoutSrc,
    /import\s*{[^}]*DARK_CLASS[^}]*}\s*from\s*"@\/lib\/theme"/,
    "layout.tsx must import DARK_CLASS",
  );
  assert.match(
    layoutSrc,
    /import\s*{[^}]*TRANSITIONS_OFF_CLASS[^}]*}\s*from\s*"@\/lib\/theme"/,
    "layout.tsx must import TRANSITIONS_OFF_CLASS",
  );
  // Inline script must use them via `${...}` template interpolation —
  // not hard-coded strings.
  assert.match(
    layoutSrc,
    /\$\{THEME_STORAGE_KEY\}/,
    "FOUC script must reference THEME_STORAGE_KEY directly",
  );
  assert.match(
    layoutSrc,
    /\$\{DARK_CLASS\}/,
    "FOUC script must reference DARK_CLASS directly",
  );
  assert.match(
    layoutSrc,
    /\$\{TRANSITIONS_OFF_CLASS\}/,
    "FOUC script must reference TRANSITIONS_OFF_CLASS directly",
  );
  // suppressHydrationWarning is required because the inline script
  // adds classes to <html> before React hydrates, and React would
  // otherwise complain about the SSR/client className mismatch.
  assert.match(
    layoutSrc,
    /suppressHydrationWarning/,
    "html element must have suppressHydrationWarning to allow inline-script class additions",
  );
});

test("FOUC: globals.css references TRANSITIONS_OFF_CLASS via the same selector", () => {
  const cssSrc = readFileSync(
    new URL("../../src/app/globals.css", import.meta.url),
    "utf8",
  );
  assert.match(
    cssSrc,
    /\.theme-transitions-off\b/,
    "globals.css must contain a .theme-transitions-off rule for the FOUC script to take effect",
  );
});

// ---------------------------------------------------------------------------

console.log(`\n${passed} passed, ${failed} failed`);
if (failed > 0) {
  for (const { name, err } of failures) {
    console.error(`FAIL: ${name}\n  ${err.stack}`);
  }
  process.exit(1);
}
