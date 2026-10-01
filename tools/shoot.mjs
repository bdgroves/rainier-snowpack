// Screenshots of the live site, run in Actions (real network: tiles, CDN scripts).
import { chromium } from 'playwright';
import fs from 'fs';
const b = await chromium.launch();
const errs = [];
for (const [name, vp] of [['desk', { width: 1440, height: 900 }], ['phone', { width: 390, height: 844 }]]) {
  const p = await b.newPage({ viewport: vp });
  p.on('pageerror', e => errs.push(`${name}: ${e.message}`));
  await p.goto('https://bdgroves.github.io/rainier-snowpack/?v=' + Date.now(), { waitUntil: 'networkidle' });
  await p.evaluate(async () => { for (let y = 0; y < document.body.scrollHeight; y += 600) { window.scrollTo(0, y); await new Promise(r => setTimeout(r, 120)); } window.scrollTo(0, 0); });
  await p.waitForTimeout(3000);
  await p.screenshot({ path: `tools/shots/${name}.png`, fullPage: true });
}
fs.writeFileSync('tools/shots/errors.txt', errs.join('\n') || 'no errors');
await b.close();
