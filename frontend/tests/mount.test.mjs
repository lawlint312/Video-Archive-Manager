// Does every component's <script setup> actually RUN?
//
// This exists because of a real regression: a `watch([noPersonMode, ...])` was
// written above the `const noPersonMode = ref(false)` it referred to. That is
// a temporal-dead-zone ReferenceError thrown during setup, which kills the
// whole component tree — the profile page went blank, and so did the video
// page behind it. `npm run build` compiles it happily: bundlers do not do
// TDZ analysis, and there was nothing else that ever executed the code.
//
//     node tests/mount.test.mjs        (from frontend/)
//
// It mounts each component with a stubbed browser and stubbed modules, so it
// checks "the setup function survives being called", not behaviour. That is
// exactly the gap the build leaves.
// The resolve hook must be registered before anything else is imported.
import { register } from 'node:module'
register('./extloader.mjs', import.meta.url)
import { readdirSync, readFileSync } from 'node:fs'
import { createSSRApp, h } from 'vue'
import { renderToString } from 'vue/server-renderer'
import { parse, compileScript } from 'vue/compiler-sfc'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { dirname, join, resolve } from 'node:path'
import { writeFileSync, rmSync } from 'node:fs'

const here = dirname(fileURLToPath(import.meta.url))
const SRC = resolve(here, '..', 'src')
const COMPONENTS = join(SRC, 'components')

let failed = 0
const say = (ok, m) => { console.log(`  ${ok ? 'OK  ' : 'FAIL'} ${m}`); if (!ok) failed++ }

// Browser globals the modules touch at import time or during setup.
const store = new Map()
globalThis.localStorage = {
  getItem: k => (store.has(k) ? store.get(k) : null),
  setItem: (k, v) => store.set(k, String(v)),
  removeItem: k => store.delete(k),
}
globalThis.window = globalThis
globalThis.document = {
  addEventListener() {}, removeEventListener() {},
  createElement: () => ({ style: {}, classList: { add() {}, remove() {} },
                          appendChild() {}, setAttribute() {} }),
  querySelector: () => null, querySelectorAll: () => [],
  head: { appendChild() {} }, body: { appendChild() {} },
}
globalThis.matchMedia = () => ({ matches: false, addEventListener() {},
                                 removeEventListener() {} })
globalThis.fetch = async () => ({ ok: true, status: 200, headers: new Map(),
                                  json: async () => ({}), text: async () => '' })
// Node 24 defines navigator as a getter-only property
if (!globalThis.navigator) {
  Object.defineProperty(globalThis, 'navigator',
                        { value: { userAgent: 'node' }, configurable: true })
}
globalThis.ResizeObserver = class { observe() {} disconnect() {} }
globalThis.IntersectionObserver = class { observe() {} disconnect() {} unobserve() {} }
globalThis.requestAnimationFrame = cb => setTimeout(cb, 0)
globalThis.alert = () => {}
globalThis.confirm = () => false

// The compiled module is written INTO src/components and deleted after: from
// the OS temp dir neither `vue` nor `../store` resolves, so every component
// "failed" for a reason that had nothing to do with the code.
const scratch = []

/** Compile one SFC's <script setup> to a module we can import.
 *
 * The compiled file is written NEXT TO its source, not into a fixed folder:
 * App.vue's `./api` and a component's `../api` are different paths, so putting
 * both in src/components made App.vue fail on an import that is perfectly
 * correct. */
async function loadSetup(file) {
  const dir = dirname(file)
  const src = readFileSync(file, 'utf8')
  const { descriptor } = parse(src, { filename: file })
  if (!descriptor.scriptSetup) return null
  const compiled = compileScript(descriptor, { id: file, inlineTemplate: false })
  const code = compiled.content
    // child components are stubbed: this checks one setup, not a whole tree
    .replace(/from\s+['"]\.[^'"]*\.vue['"]/g, "from './__stub.mjs'")
    // extensionless specifiers are handled by tests/extloader.mjs, which
    // covers the whole dependency graph rather than just this file's imports
  const out = join(dir, `__setup_${Math.random().toString(36).slice(2)}.mjs`)
  writeFileSync(out, code, 'utf8')
  scratch.push(out)
  return import(pathToFileURL(out).href)
}

const STUB = 'export default { name: "Stub", render() { return null } }'
for (const d of [COMPONENTS, SRC]) {
  const stub = join(d, '__stub.mjs')
  writeFileSync(stub, STUB, 'utf8')
  scratch.push(stub)
}

console.log('每個元件的 <script setup> 都要跑得起來')
// App.vue is in the list because the regression this file exists for could
// just as easily land there — and it is the one component whose failure takes
// the entire page with it rather than one tab.
const files = [...readdirSync(COMPONENTS).filter(f => f.endsWith('.vue')).sort()
                 .map(f => join(COMPONENTS, f)),
               join(SRC, 'App.vue')]
for (const path of files) {
  const f = path.slice(SRC.length + 1)
  let mod
  try {
    mod = await loadSetup(path)
  } catch (e) {
    say(false, `${f}：編譯失敗 ${String(e).slice(0, 120)}`)
    continue
  }
  if (!mod) { continue }
  const comp = mod.default
  if (!comp || typeof comp.setup !== 'function') {
    say(true, `${f}（沒有 setup，略過）`)
    continue
  }
  try {
    // props every component in this app takes, supplied generously; a missing
    // one is not what this test is about
    const props = { folder: 'X:\\f', video: { path: 'X:\\a.mp4', name: 'a' },
                    items: [], item: {}, src: '', progress: null,
                    modelValue: '', storageKey: 'k', character: 'c',
                    path: 'X:\\a', open: false, mode: 'video', start: 'video',
                    char: { name: 'c', paths: ['X:\\a', 'X:\\b'] } }
    const app = createSSRApp({
      setup() { return () => h(comp, props) },
    })
    app.config.warnHandler = () => {}
    await renderToString(app)
    say(true, `${f}`)
  } catch (e) {
    const msg = String(e && e.message || e)
    // A ReferenceError from setup is the failure this file exists for.
    say(false, `${f}：setup 丟出 ${msg.slice(0, 140)}`)
  }
}

for (const f of scratch) rmSync(f, { force: true })
console.log(failed ? `\nFAIL (${failed})` : '\nPASS')
process.exit(failed ? 1 : 0)
