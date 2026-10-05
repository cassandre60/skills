---
name: mobile-app-to-web-demo
description: Port a real mobile app into an interactive demo embedded in a web page, generated from the app's own code so it cannot drift. Use when a user wants a site to show a working demo of their mobile app (Android/iOS/Flutter/React Native), complains the demo "doesn't look like the app", or asks to embed the real app in a website.
---

# Mobile app to web demo

Get the **real app** running inside a web page, generated from the app repo so it
cannot drift from the app. Not a re-implementation. Not mockups.

## Choose an approach before writing anything

Three approaches exist. Pick deliberately — the two I tried first were wrong, and
the reasons are worth more than the recipes.

| Approach | Fidelity | Load | Divergences | Right when |
|---|---|---|---|---|
| **A. Compile the real app** | Exact, forever | 2–15 MB | None | Default. Any app with real depth |
| **B. Hand-write the UI in the page** | Approximate | ~0 KB | One per release, silently | Never, as a final state |
| **C. Generate inputs from the app, hand-write structure** | Close | ~100 KB | Caught by CI | One screen only, when the app cannot target web |

**B is a trap, and it is the instinct.** Rebuilding the app's screens in HTML/CSS
loads fast, so it feels like progress. But a re-implementation is a *second*
implementation of the app: every fix is whack-a-mole, every release introduces
drift, and nothing ever tells you the demo is now wrong. I shipped one, polished
it across several rounds, and it was still rejected as not looking like the app.

**A is the default.** Load cost is a design problem with known solutions (§5), not
a reason to abandon fidelity.

**C is legitimate for exactly one thing**: a single screen, where the whole app
cannot target web. It only works with the full anti-drift apparatus in §6, and
even then the *logic* is a second implementation. Do not reach for C because A
feels heavy. Do not let C sprawl past one screen — that is how it becomes B.

## Ground rules

Ask before assuming:

- **Is a 2–15 MB download acceptable?** Even then A is the answer, but
  poster-first (§5) becomes mandatory rather than optional.
- **Zero third-party requests?** If the page promises it, the build must bundle
  everything. Most frameworks fetch assets from a CDN by default.
- **Which screens?** All, or a subset the user names? Default to all. Never
  silently narrow scope to make it easier.
- **Must production code run unchanged?** You will swap *persistence* (§3) and
  nothing else.

## 1. Measure before changing anything

**Measure. Do not estimate, and do not reason from directory size.**

```js
import { chromium } from '@playwright/test';
const reqs = [];
page.on('response', async r => reqs.push({ u: r.url(), s: (await r.body()).length }));
await page.goto(BASE, { waitUntil: 'load' });
await page.waitForTimeout(8000);              // let late/lazy fetches land
reqs.sort((a, b) => b.s - a.s).slice(0, 12)
    .forEach(r => console.log((r.s / 1048576).toFixed(2) + 'M', r.u));
```

Report **raw and gzipped**, and check what your host actually serves:

```sh
curl -sI -H "Accept-Encoding: gzip, br" "$URL/canvaskit/canvaskit.wasm" | grep -i content-encoding
```

Deploy a real file once if you have to. Guessing here produces confident, wrong
optimisation work: comparing a raw byte count against a gzipped one "finds" a 3 MB
saving that is actually a 0.8 MB loss. This happened to me.

**Then read the runtime's asset-selection logic** before touching anything.
Toolchains usually ship several renderer/font variants and pick one at runtime by
feature-sniffing. The one it picks may already be the smallest. Read the sniffing
code; do not delete files that look unused. Some are fallbacks for older browsers
and are genuinely reachable.

## 2. Make it build for web

Work in a **branch**. Never on the app's main line.

- **Prerequisites are usually missing from a fresh clone.** Generated sources
  (ORM codegen, protobuf, GraphQL types) are normally gitignored, so CI must run
  the generator before building. This bites in CI, not locally.
- **Isolate platform code with conditional imports**, not `if (isWeb)`. One
  interface, a native implementation, a web implementation. This keeps
  unsupported APIs out of the web compile graph entirely rather than relying on
  dead-code elimination.
- **A demo entry point** that mounts the app's real root widget/router and
  overrides only what cannot work (§3). Keep it small — a demo entry that
  assembles its own navigation has already drifted.

## 3. Swap persistence; keep everything else

A browser has no filesystem and no native database bridge.

- Implement the app's data-access interfaces with in-memory fakes, seeded to
  exercise every screen.
- Override them at the composition root. Do not fork the repositories themselves.
- **Seed for the screenshot's sake.** One record renders as a lone card above
  empty space and reads as a broken app. Seed several, with histories that make
  comparisons and forecasts meaningful. If the app sorts by a timestamp and
  promotes the first row, control which record that is.
- Features needing a filesystem, share sheet, or installed binary (export,
  install, native sharing) get a clear friendly failure. Do not delete them — their
  absence is a visible gap.

## 4. Capture screens from the running app

Do not mock screenshots. Drive the real build and photograph it.

**Drive it through the accessibility tree, not coordinates.** Every major
framework emits one for screen readers. Tap by accessible name so a layout change
moves the target instead of silently photographing the wrong screen.

Flutter web's semantics placeholder sits off-screen at -1px,-1px, so a mouse
cannot reach it — dispatch the event directly:

```js
await page.evaluate(() => document.querySelector('flt-semantics-placeholder')
  .dispatchEvent(new MouseEvent('click', { bubbles: true })));
```

**Assert you landed on the right screen.** Navigation by text will happily
photograph whatever replaced a renamed screen:

```js
const seen = await page.locator('flt-semantics').allTextContents();
if (!seen.join(' ').includes('Expected Heading')) {
  throw new Error(`Wrong screen. Present: ${JSON.stringify(seen.slice(0, 10))}`);
}
```

Without this, a rename ships a stale-but-plausible screenshot for weeks.

**Boot once per screenshot**, or the third image carries state from the second.

## 5. Make the poster a loading state, not the product

The failure here is subtle and it happened to me: shipping a gallery of
screenshots with a "try it live" button. Every pixel is real, and it still reads
as *pictures pretending to be software*. Users reject it as unprofessional.

Ship instead:

- **Poster underneath, app on top, same box.** The poster is a true screenshot of
  what is about to appear, so the reader sees the app arrive rather than a page
  change height.
- **The frame stays transparent until the app has painted.** Do not hide the
  poster when the frame reports ready — "ready" means the container exists, not
  that anything has drawn. That window is exactly where an empty box appears, and
  it will end up baked into your visual-regression baselines.
- **Warm the engine at low priority** as the demo approaches the viewport.
  Prefer `rel="prefetch"` to `preload`: preload competes for bandwidth and can be
  billed to a metered visitor. Nothing here may be render-blocking.
- **If the engine never starts, leave the poster.** It is a real screenshot, so
  the section still tells the truth instead of showing an empty frame.

## 6. If you build approach C, make drift mechanically detectable

Approach C only survives with an apparatus. Each piece catches a failure the
others miss:

- **Generated inputs, committed.** Extract tokens, fonts, and the app's own design
  assets into generated files. Extract **structure and copy too** — section
  headers, row labels, row order. A design-only sync catches a colour change and
  silently ignores a renamed row, which is the drift people actually notice.
- **`--check` gates** that fail when a generated file is stale, wired into CI.
- **A parity spec** that renders the demo and asserts it against the committed
  manifest. It must run everywhere, which is why the manifest is committed — CI
  often has no access to the app repo.
- **Known gaps as a first-class list.** Features the demo does not port go in a
  `KNOWN_GAPS` set that is *asserted to stay present in the manifest*. A dropped
  row then forces the list to be revisited instead of lingering.
- **Extractors fail loudly.** If a refactor moves a literal where your scanner
  cannot see it, that is precisely the drift worth catching. A quietly empty
  section reports "no drift".
- **Document what is hand-written and why,** in a table, in the repo. Future you
  will need to know which parts are load-bearing.
- **Never use committed screenshots as an oracle.** App screenshots go stale.
  Review against the source.

And be honest in the write-up about substituted logic: where you swapped the app's
engine or algorithm for a JS equivalent, say so and name it as the first place to
look when the two disagree.

## 7. The page must not depend on the engine

Assert it, do not assume it:

- **The rest of the page works with every engine request aborted.** If the page
  breaks when the demo does, the demo is load-bearing and that is a bug.
- **The third-party promise is a test.** Zero off-origin requests, asserted in CI,
  not written in a comment.
- **Screenshot tests must block the engine.** Otherwise each baseline records
  whichever of poster-or-app happened to be on screen, and the suite is flaky by
  construction. Block it, capture the poster, and let one dedicated test opt back
  in to verify the app actually paints.

## 8. Put the heavy work in CI

Browser test matrices and app builds are heavy; running them on a laptop saturates
it. **Move them to CI.** Not politeness — the difference between a usable machine
and an unusable one.

CI must be able to regenerate the demo from the app repo, or the site silently
ships stale screenshots.

```yaml
- run: pnpm exec playwright install --with-deps chromium   # capture needs a browser
- name: Generate the demo
  env:
    APP_DIR: ${{ github.workspace }}/../app
  run: |
    git clone --depth 1 --branch $APP_BRANCH \
      "https://x-access-token:${{ secrets.APP_TOKEN }}@github.com/${{ vars.APP_REPO }}.git" ../app
    ( cd ../app && <install> && <codegen> )     # generated sources are gitignored
    sh scripts/build-demo.sh --app ../app
```

Gotchas, each of which cost a failed deploy:

- **Generated code is gitignored.** A fresh clone cannot build without codegen.
- **The capture step needs a browser binary** the runner lacks by default.
- **A private app repo needs a token.** Fine-grained PAT, **Contents: read-only**,
  scoped to that one repo. Gate the step on it; without it, fall back to the last
  generated artefacts rather than failing the deploy.
- **`secrets.X` is not usable in a step-level `if`.** Compare a workflow-level
  `env` var instead.
- **Verify the regeneration ran** by grepping the job log for "N posters written".
  A green run that silently skipped the step is worse than a red one.

### The check that catches the most

Once CI regenerates the demo, it reports the app's **committed** state, not your
local working tree. If you tuned seed data or copy locally and never committed it,
CI deploys the old version and the pipeline looks broken. It is not — it is
reporting the truth. Commit the app-side change.

## Definition of done

- [ ] Approach chosen deliberately; if C, one screen only and §6 fully in place
- [ ] The demo is the app's own output, not a hand-kept re-implementation
- [ ] Load cost measured raw **and** gzipped, before and after each change
- [ ] Which changes kept, which dropped, and why — stated explicitly
- [ ] Zero third-party requests, asserted by a test
- [ ] The page works with the engine blocked
- [ ] Screenshots provably from the running app; a renamed screen fails the build
- [ ] No empty-box window between poster and app
- [ ] CI regenerates from the app repo, and the log proves it ran
- [ ] Browser tests and baselines run on CI, not locally

## Traps, in the order I hit them

1. **Hand-writing the UI.** Rejected twice before I understood why. When the user
   says it "doesn't look like the app", this is why — do not try harder at it.
2. **Measuring raw against gzipped** and "finding" a saving that reverses sign.
3. **Deleting build variants that look unused** without reading the runtime's
   selection logic.
4. **Shipping a screenshot gallery** instead of using the poster as a loading state.
5. **Hiding the poster on frame-load**, producing an empty box baked into the
   visual baselines.
6. **Seeding one record**, so the screenshot is mostly empty space.
7. **Syncing only design tokens**, then missing that a row was renamed — the
   failure users actually notice.
8. **Local-only generated code.** Works on your machine, fails in CI.
9. **Tuning the app locally and forgetting to commit it**, then blaming CI.