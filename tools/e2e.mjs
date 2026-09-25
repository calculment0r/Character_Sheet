/**
 * Vérification bout en bout de la Character Factory.
 *
 *   node tools/e2e.mjs [url]        défaut : http://127.0.0.1:8000
 *
 * Attend une Factory en marche — page et API sur la même origine — et
 * un modèle derrière, vrai ou factice (voir tools/mock_llm.py).
 *
 * Rend 0 si tout passe, 1 sinon.
 */

import { chromium } from 'playwright';

const BASE = process.argv[2] || 'http://127.0.0.1:8000';
const PAGE = `${BASE.replace(/\/$/, '')}/console.html`;   // la racine du site est la page d'état
const CHROME = process.env.CHROME_PATH || undefined;

const results = [];
function check(name, ok, detail = '') {
  results.push({ name, ok, detail });
  console.log(`  ${ok ? 'ok    ' : 'ÉCHEC '} ${name}${detail ? ` — ${detail}` : ''}`);
}

async function main() {
  const browser = await chromium.launch(CHROME ? { executablePath: CHROME } : {});
  // Contexte vierge : aucun réglage enregistré, comme un opérateur qui
  // ouvre l'adresse pour la première fois.
  const ctx = await browser.newContext({ viewport: { width: 1680, height: 1050 }, ignoreHTTPSErrors: true });
  const page = await ctx.newPage();

  const errors = [];
  page.on('pageerror', (e) => errors.push(`pageerror: ${e.message}`));
  page.on('console', (m) => {
    // On ignore les polices : elles échouent derrière un proxy qui
    // réécrit les certificats, sans conséquence sur le comportement.
    if (m.type() === 'error' && !/CERT|favicon|fonts\.googleapis/.test(m.text())) errors.push(m.text());
  });

  console.log(`\nCharacter Factory — vérification sur ${BASE}\n`);

  // 1. la page se charge
  const res = await page.goto(PAGE, { waitUntil: 'networkidle' });
  check('la page répond', res?.status() === 200, `HTTP ${res?.status()}`);
  await page.waitForTimeout(1200);

  // 2. la console est rendue
  const slabs = await page.$$eval('.slab .nm', (n) => n.map((x) => x.textContent));
  check('la console liste les huit étages', slabs.length === 8, slabs.join(', '));

  // 3. le moteur est reconnu sans réglage
  const engine = await page.textContent('#engine-text');
  check('le moteur est reconnu sans réglage', !!engine && !/non configuré|muet/.test(engine), engine);

  // 4. on entre dans un étage
  await page.click('.slab');
  await page.waitForTimeout(400);
  const stage = await page.textContent('#stage-name');
  check('on entre dans un étage', (await page.$('#home')).isHidden !== undefined && stage === 'Identité', stage);

  // 5. la fiche a ses 21 champs
  const rows = await page.$$('.sheet-row');
  check('la fiche a ses 21 champs', rows.length === 21, `${rows.length} lignes`);

  // 6. un tour complet remplit la fiche
  await page.fill('#input-text', 'Une pilote de fret orbital, la quarantaine, marquée.');
  await page.click('#send');
  let widget = true;
  try {
    await page.waitForSelector('.msg-widget', { timeout: 15000 });
  } catch {
    widget = false;
  }
  const filled = await page.textContent('#sheet-progress');
  check('le modèle remplit la fiche par appels d\'outils', filled !== '0/21', filled);
  check('le modèle pose un widget interactif', widget);

  // 7. cliquer une puce renseigne le champ
  if (widget) {
    const chip = await page.$('.msg-widget .chip');
    if (chip) {
      await chip.click();
      await page.waitForTimeout(900);
      const locked = await page.$('.widget-locked');
      check('la puce cliquée verrouille le widget', !!locked, await page.textContent('#sheet-progress'));
    }
  }

  // 8. retour à la console
  await page.click('#console-btn');
  await page.waitForTimeout(400);
  check('le retour à la console fonctionne', !(await page.$eval('#home', (n) => n.hidden)));

  // 9. pas de débordement horizontal en vue étroite
  const narrow = await (await browser.newContext({ viewport: { width: 430, height: 950 }, ignoreHTTPSErrors: true })).newPage();
  await narrow.goto(PAGE, { waitUntil: 'networkidle' });
  await narrow.waitForTimeout(800);
  const overflow = await narrow.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1);
  check('aucun débordement horizontal à 430 px', !overflow);

  // 10. l'API répond
  const health = await fetch(`${BASE}/health`).then((r) => r.json()).catch(() => null);
  check('l\'API répond sur /health', !!health?.ok, health ? `file ${health.queue}, stockage ${health.storage}` : '');

  check('aucune erreur dans la console du navigateur', errors.length === 0, errors.join(' | '));

  await browser.close();

  const failed = results.filter((r) => !r.ok);
  console.log(`\n${results.length - failed.length}/${results.length} vérifications passées\n`);
  process.exit(failed.length ? 1 : 0);
}

main().catch((e) => {
  console.error('\nla vérification s\'est interrompue :', e.message, '\n');
  process.exit(1);
});
