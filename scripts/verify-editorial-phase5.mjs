import { promises as fs } from 'node:fs';
import path from 'node:path';
import process from 'node:process';
import { fileURLToPath } from 'node:url';
import { loadEditorialContent } from './lib/content-loader.mjs';
import { snapshotTree, treeDigest } from './lib/tree-integrity.mjs';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const dist = path.join(root, 'dist');
const releaseLock = process.argv.includes('--release-lock');
const errors = [];
const warnings = [];
const fail = (message) => errors.push(message);
const warn = (message) => warnings.push(message);
const exists = async (relative) => fs.access(path.join(root, relative)).then(() => true).catch(() => false);
const read = (relative) => fs.readFile(path.join(root, relative), 'utf8');

async function walk(directory, output = []) {
  for (const entry of await fs.readdir(directory, { withFileTypes: true })) {
    const full = path.join(directory, entry.name);
    if (entry.isDirectory()) await walk(full, output);
    else output.push(full);
  }
  return output;
}

for (const relative of [
  'docs/editorial-migration/phase-5-release.json',
  'docs/editorial-migration/phase-5-finalization.md',
  'docs/editorial-migration/phase-5-live-validation.md',
  'scripts/verify-editorial-phase5.mjs',
  'scripts/verify-live-deployment.mjs',
  'scripts/test-live-deployment-verifier.mjs',
  'scripts/test-image-generator.mjs',
  'content/search-sources.json',
  'docs/jtorrent-content-package-prompt.txt',
  'dist/.well-known/jtorrent-build.json',
  'Publish-Content.ps1',
  '.github/workflows/editorial-qc.yml',
  '.github/workflows/external-link-qc.yml',
  'scripts/check-cross-site-links.mjs',
  'content/sections.json',
  'dist/editorial/content-manifest.json',
  'dist/editorial/legacy-redirects.json'
]) if (!await exists(relative)) fail(`Phase 5 required file is missing: ${relative}`);

const packageJson = JSON.parse(await read('package.json'));
for (const [name, command] of Object.entries({
  qc: 'node scripts/run-qc.mjs',
  'verify:phase3': 'node scripts/verify-editorial-phase3.mjs',
  'verify:phase4': 'node scripts/verify-editorial-phase4.mjs',
  'verify:phase5': 'node scripts/verify-editorial-phase5.mjs',
  'verify:phase5:release': 'node scripts/verify-editorial-phase5.mjs --release-lock',
  'verify:live': 'node scripts/verify-live-deployment.mjs',
  'new:content': 'node scripts/new-content.mjs',
  'check:external': 'node scripts/check-cross-site-links.mjs'
})) {
  if (packageJson.scripts?.[name] !== command) fail(`package.json script ${name} must be: ${command}`);
}

const release = JSON.parse(await read('docs/editorial-migration/phase-5-release.json'));
if (release.phase !== 5 || release.status !== 'finalized') fail('Phase 5 release record is not finalized.');
if (release.migrationMode !== 'existing-custom-generator-extension') fail('Phase 5 release record has the wrong migration mode.');
if (release.repository !== 'officialjivaro/website-jtorrent-net' || release.productionBranch !== 'main') fail('Phase 5 release record has the wrong repository or branch.');
if (release.buildCommand !== 'npm run build' || release.qcCommand !== 'npm run qc' || release.outputDirectory !== 'dist') fail('Phase 5 release record has the wrong build/deployment contract.');
if (!/^[a-f0-9]{64}$/.test(release.baselineOutputTreeSha256 || '')) fail('Phase 5 release record has no valid output-tree SHA-256.');

const sections = JSON.parse(await read('content/sections.json'));
const expectedSections = ['torrenting', 'privacy', 'proxies', 'vpn', 'apps', 'software', 'providers'];
const sectionIds = sections.map((section) => section.id);
if (JSON.stringify(sectionIds) !== JSON.stringify(expectedSections)) fail(`Canonical section registry changed unexpectedly: ${sectionIds.join(', ')}`);
if (sections.length !== release.sectionCount) fail(`Section registry count ${sections.length} does not match the Phase 5 record ${release.sectionCount}.`);

const editorial = await loadEditorialContent(root, { includeDrafts: true });
if (editorial.canonicalArticles.length < release.minimumCanonicalArticles) fail(`Canonical article count ${editorial.canonicalArticles.length} is below the migration minimum ${release.minimumCanonicalArticles}.`);
if (editorial.publishedArticles.length < release.minimumCanonicalArticles) fail(`Published article count ${editorial.publishedArticles.length} is below the migration minimum ${release.minimumCanonicalArticles}.`);
if (editorial.canonicalArticles.filter((article) => article.newAuthority).length !== release.historicalLaunchAuthorityArticles) fail(`Expected exactly ${release.historicalLaunchAuthorityArticles} historical launch-authority articles.`);
if (editorial.legacyArticles.length || editorial.replacedLegacyPaths.length) fail('A competing legacy editorial source returned after migration closeout.');
if (editorial.publishedArticles.some((article) => article.author !== 'Jivaro Torrent Editorial')) fail('A published article does not use the canonical Jivaro Torrent Editorial identity.');

for (const relative of ['content/articles.json', 'content-package-manifest.json', 'scripts/verify-editorial-phase2.mjs']) {
  if (await exists(relative)) fail(`Obsolete migration file returned: ${relative}`);
}

for (const article of editorial.canonicalArticles) {
  for (const sourceFile of article.sourceFiles || []) if (!await exists(sourceFile)) fail(`Canonical article source is missing: ${sourceFile}`);
}

const manifest = JSON.parse(await read('dist/editorial/content-manifest.json'));
if (manifest.articleCount !== editorial.publishedArticles.length) fail('Generated editorial manifest article count does not match published source.');
if (manifest.sectionCount !== sections.length) fail('Generated editorial manifest section count does not match the canonical registry.');
if (manifest.draftCount !== editorial.drafts.length) fail('Generated editorial manifest draft count does not match canonical source.');
if (!Array.isArray(manifest.articles) || manifest.articles.length !== editorial.publishedArticles.length) fail('Generated editorial manifest article records are incomplete.');

const legacy = JSON.parse(await read('dist/editorial/legacy-redirects.json'));
if (legacy.redirectCount < release.baselineLegacyRedirects) fail(`Generated legacy redirects fell below the migration baseline of ${release.baselineLegacyRedirects}.`);

const htmlFiles = (await walk(dist)).filter((file) => file.endsWith('.html'));
if (htmlFiles.length < release.baselineHtmlFilesIncluding404) fail(`Generated HTML file count ${htmlFiles.length} fell below the Phase 5 baseline ${release.baselineHtmlFilesIncluding404}.`);
const publicRoutes = htmlFiles.filter((file) => path.basename(file) !== '404.html').length;
if (publicRoutes < release.baselinePublicRoutes) fail(`Generated public route count ${publicRoutes} fell below the Phase 5 baseline ${release.baselinePublicRoutes}.`);

const rss = await read('dist/rss.xml');
const rssItems = (rss.match(/<item>/g) || []).length;
const indexablePublished = editorial.publishedArticles.filter((article) => article.indexing !== false).length;
if (rssItems !== indexablePublished) fail(`Global RSS contains ${rssItems} items; expected ${indexablePublished}.`);

const searchIndex = JSON.parse(await read('dist/search-index.json'));
if (!Array.isArray(searchIndex) || searchIndex.length !== indexablePublished) fail('Editorial search index does not contain every indexable published article exactly once.');

const sitemap = await read('dist/sitemap.xml');
for (const article of editorial.publishedArticles.filter((item) => item.indexing !== false)) {
  if (!sitemap.includes(`<loc>https://www.jtorrent.net${article.path}</loc>`)) fail(`Sitemap is missing ${article.path}.`);
}

const home = await read('dist/index.html');
if (!home.includes('<title>Jivaro Torrent Search</title>') || !home.includes('data-search-console')) fail('Generated homepage is not the search-first Jivaro Torrent homepage.');
for (const retired of ['Open Menu Close Menu', 'Command Palette', 'Enhance Privacy, Find Torrents Effortlessly', 'static1.squarespace.com']) {
  if (home.includes(retired)) fail(`Generated homepage contains retired deployment marker: ${retired}`);
}
if ((home.match(/role="tab"/g) || []).length !== 2) fail('Generated homepage does not contain exactly two search modes.');
if (!home.includes('id="search-source-registry"') || !home.includes('data-source-selector')) fail('Generated homepage is missing the canonical search-source selector.');
if (!/src="\/js\/search\.[a-f0-9]{12}\.js"/.test(home)) fail('Generated homepage does not load the fingerprinted homepage search bundle.');
if (/src="\/js\/providers\.[a-f0-9]{12}\.js"/.test(home)) fail('Generated homepage incorrectly loads the provider-directory bundle.');

const buildFingerprint = JSON.parse(await read('dist/.well-known/jtorrent-build.json'));
if (buildFingerprint.project !== 'website-jtorrent-net') fail('Generated build fingerprint has the wrong project.');
if (buildFingerprint.routeCount !== publicRoutes || buildFingerprint.articleCount !== editorial.publishedArticles.length) fail('Generated build fingerprint route/article counts are stale.');
if (buildFingerprint.searchContract !== 'v3-with-v2-fallback') fail('Generated build fingerprint has the wrong search contract.');
if (!/^[a-f0-9]{12}$/.test(buildFingerprint.assetHash || '')) fail('Generated build fingerprint has an invalid asset hash.');

const runQc = await read('scripts/run-qc.mjs');
for (const required of [
  'scripts/test-live-deployment-verifier.mjs',
  'scripts/test-image-generator.mjs',
  'scripts/verify-editorial-phase4.mjs',
  'scripts/verify-editorial-phase5.mjs',
  'Production build 1 of 2',
  'Production build 2 of 2'
]) if (!runQc.includes(required)) fail(`Deterministic QC runner is missing Phase 5 integration: ${required}`);

const liveVerifier = await read('scripts/verify-live-deployment.mjs');
for (const required of [
  'https://www.jtorrent.net',
  'https://jtorrent.net',
  'https://website-jtorrent-net.pages.dev',
  'https://officialjivaro.github.io/backend-jtorrent',
  'data-search-console',
  'retired/stale marker',
  '/editorial/content-manifest.json',
  '/.well-known/jtorrent-build.json',
  '/data/status.json',
  'data/v3/manifest.json',
  'static_score',
  '/go/brightdata',
  'Known legacy Blog redirect',
  'Apex-domain redirect'
]) if (!liveVerifier.includes(required)) fail(`Live deployment verifier is missing: ${required}`);
if (/https:\/\/officialjivaro\.github\.io\/jtorrent(?:\/|['"])/.test(liveVerifier)) fail('Live deployment verifier still points to the retired officialjivaro/jtorrent backend.');

const workflow = await read('.github/workflows/editorial-qc.yml');
if (!workflow.includes('npm run qc') || !workflow.includes('git diff --exit-code -- .')) fail('GitHub Actions does not run the final deterministic release gate.');
const wrangler = await read('wrangler.toml');
if (!/name\s*=\s*"website-jtorrent-net"/.test(wrangler) || !/pages_build_output_dir\s*=\s*"\.\/dist"/.test(wrangler)) fail('Cloudflare Pages project/output configuration is incorrect.');

const repositoryFiles = await walk(root);
for (const file of repositoryFiles) {
  const relative = path.relative(root, file).split(path.sep).join('/');
  if (relative.startsWith('node_modules/') || relative.startsWith('dist/') || relative.startsWith('.git/')) continue;
  const base = path.basename(file).toLowerCase();
  if (['desktop.ini', 'thumbs.db', '.ds_store'].includes(base)) fail(`OS metadata remains in source: ${relative}`);
}

const outputSnapshot = await snapshotTree(dist);
const outputDigest = treeDigest(outputSnapshot);
if (outputSnapshot.size < release.baselineOutputFiles) fail(`Output file count ${outputSnapshot.size} fell below the Phase 5 baseline ${release.baselineOutputFiles}.`);
if (releaseLock) {
  if (editorial.publishedArticles.length !== release.minimumCanonicalArticles) fail(`Release-lock verification requires exactly ${release.minimumCanonicalArticles} published articles; found ${editorial.publishedArticles.length}.`);
  if (outputSnapshot.size !== release.baselineOutputFiles) fail(`Release-lock output file count changed: ${outputSnapshot.size} != ${release.baselineOutputFiles}.`);
  if (outputDigest !== release.baselineOutputTreeSha256) fail(`Release-lock output digest changed: ${outputDigest} != ${release.baselineOutputTreeSha256}.`);
}

if (warnings.length) console.warn(warnings.map((message) => `WARN: ${message}`).join('\n'));
if (errors.length) {
  console.error(errors.map((message) => `ERROR: ${message}`).join('\n'));
  process.exit(1);
}

console.log(`Phase 5 verified: ${editorial.publishedArticles.length} published canonical articles, ${sections.length} hubs, ${publicRoutes} public routes, ${outputSnapshot.size} deterministic output files${releaseLock ? ', and the exact release baseline' : ''}.`);
