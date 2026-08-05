import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { promises as fs } from 'node:fs';
import path from 'node:path';
import process from 'node:process';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { loadEditorialContent } from './lib/content-loader.mjs';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const dist = path.join(root, 'dist');
const siteData = JSON.parse(await fs.readFile(path.join(root, 'content/site.json'), 'utf8'));
const editorial = await loadEditorialContent(root, { includeDrafts: true });
const site = { ...siteData, hubs: editorial.hubs };
const articles = editorial.publishedArticles;
const indexableArticles = editorial.indexableArticles;
const errors = [];
const warnings = [];

const fail = (message) => errors.push(message);
const warn = (message) => warnings.push(message);
const read = (relative) => fs.readFile(path.join(root, relative), 'utf8');
const exists = async (relative) => fs.access(path.join(root, relative)).then(() => true).catch(() => false);
const canonical = (route) => `${site.siteUrl}${route}`;

function runSyntax(relative) {
  try {
    execFileSync(process.execPath, ['--check', path.join(root, relative)], { stdio: 'pipe' });
  } catch (error) {
    fail(`Syntax check failed for ${relative}: ${String(error.stderr || error.message).trim()}`);
  }
}

function textContent(value = '') {
  return value.replace(/<[^>]*>/g, ' ').replace(/&(?:amp|lt|gt|quot|#039);/g, ' ').replace(/\s+/g, ' ').trim();
}

function getAttribute(tag, name) {
  return tag.match(new RegExp(`\\s${name}="([^"]*)"`, 'i'))?.[1] ?? null;
}

function webpDimensions(buffer) {
  if (buffer.toString('ascii', 0, 4) !== 'RIFF' || buffer.toString('ascii', 8, 12) !== 'WEBP') return null;
  const chunk = buffer.toString('ascii', 12, 16);
  if (chunk === 'VP8X' && buffer.length >= 30) {
    const width = 1 + buffer[24] + (buffer[25] << 8) + (buffer[26] << 16);
    const height = 1 + buffer[27] + (buffer[28] << 8) + (buffer[29] << 16);
    return { width, height };
  }
  if (chunk === 'VP8 ' && buffer.length >= 30) {
    return { width: buffer.readUInt16LE(26) & 0x3fff, height: buffer.readUInt16LE(28) & 0x3fff };
  }
  if (chunk === 'VP8L' && buffer.length >= 25) {
    const b0 = buffer[21], b1 = buffer[22], b2 = buffer[23], b3 = buffer[24];
    return { width: 1 + (((b2 & 0x3f) << 8) | b1), height: 1 + ((b3 << 6) | (b2 >> 2)) };
  }
  return null;
}

function pngDimensions(buffer) {
  const signature = '89504e470d0a1a0a';
  if (buffer.length < 24 || buffer.subarray(0, 8).toString('hex') !== signature) return null;
  return { width: buffer.readUInt32BE(16), height: buffer.readUInt32BE(20) };
}

const escapePattern = (value) => String(value).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

async function walk(directory, output = []) {
  for (const entry of await fs.readdir(directory, { withFileTypes: true })) {
    const full = path.join(directory, entry.name);
    if (entry.isDirectory()) await walk(full, output);
    else output.push(full);
  }
  return output;
}

for (const relative of ['scripts/build.mjs', 'scripts/qc.mjs', 'scripts/run-qc.mjs', 'scripts/new-content.mjs', 'scripts/verify-editorial-phase3.mjs', 'scripts/verify-editorial-phase4.mjs', 'scripts/test-editorial-system.mjs', 'scripts/test-phase4-safety.mjs', 'scripts/test-phase4-growth.mjs', 'scripts/check-cross-site-links.mjs', 'scripts/test-image-generator.mjs', 'scripts/lib/content-loader.mjs', 'scripts/lib/body-html.mjs', 'scripts/lib/tree-integrity.mjs', 'public/js/theme.js', 'public/js/site.js', 'public/js/search.js', 'public/js/providers.js', 'functions/api/search.js', 'functions/blog/[[path]].js', 'functions/go/[slug].js']) runSyntax(relative);

async function validateFunctions() {
  try {
    const stamp = Date.now();
    const go = await import(`${pathToFileURL(path.join(root, 'functions/go/[slug].js')).href}?qc=${stamp}`);
    const redirect = go.onRequest({ request: new Request('https://www.jtorrent.net/go/bright-data'), params: { slug: 'bright-data' } });
    if (redirect.status !== 302 || redirect.headers.get('location') !== 'https://get.brightdata.com/2wxh9pwrolog') fail('Affiliate alias redirect behavior is incorrect.');
    if (redirect.headers.get('referrer-policy') !== 'no-referrer' || !redirect.headers.get('x-robots-tag')?.includes('noindex')) fail('Affiliate redirect privacy/robot headers are incomplete.');
    if (redirect.headers.get('cache-control') !== 'no-store, no-cache, must-revalidate, max-age=0') fail('Affiliate redirects are not protected from caching.');
    const headRedirect = go.onRequest({ request: new Request('https://www.jtorrent.net/go/webshare', { method: 'HEAD' }), params: { slug: 'webshare' } });
    if (headRedirect.status !== 302 || !headRedirect.headers.get('location')?.startsWith('https://www.webshare.io/')) fail('Affiliate HEAD redirect behavior is incorrect.');
    if (go.onRequest({ request: new Request('https://www.jtorrent.net/go/unknown'), params: { slug: 'unknown' } }).status !== 404) fail('Unknown affiliate slugs must return 404.');
    if (go.onRequest({ request: new Request('https://www.jtorrent.net/go/x'), params: { slug: 'x' } }).status !== 400) fail('Invalid affiliate slugs must return 400.');
    const goPost = go.onRequest({ request: new Request('https://www.jtorrent.net/go/brightdata', { method: 'POST' }), params: { slug: 'brightdata' } });
    if (goPost.status !== 405 || goPost.headers.get('allow') !== 'GET, HEAD') fail('Affiliate redirects must reject unsupported methods with the correct Allow header.');

    const blog = await import(`${pathToFileURL(path.join(root, 'functions/blog/[[path]].js')).href}?qc=${stamp}`);
    const redirectManifest = await fs.readFile(path.join(dist, 'editorial/legacy-redirects.json'), 'utf8');
    const env = { ASSETS: { fetch: async () => new Response(redirectManifest, { status: 200, headers: { 'Content-Type': 'application/json' } }) } };
    const legacySlug = 'what-is-my-ip-use-netpeek-to-check-vpn-and-proxy-leaks';
    const legacy = await blog.onRequest({ request: new Request(`https://www.jtorrent.net/blog/${legacySlug}`), params: { path: legacySlug }, env, next: async () => new Response('static') });
    if (legacy.status !== 301 || new URL(legacy.headers.get('location')).pathname !== '/privacy/what-is-my-ip/') fail('Known legacy blog redirect behavior is incorrect.');
    const blogIndex = await blog.onRequest({ request: new Request('https://www.jtorrent.net/blog/'), params: { path: '' }, env, next: async () => new Response('static index') });
    if (blogIndex.status !== 200 || await blogIndex.text() !== 'static index') fail('The live /blog/ index is not preserved by the redirect function.');
    const blogPage = await blog.onRequest({ request: new Request('https://www.jtorrent.net/blog/page/2/'), params: { path: ['page', '2'] }, env, next: async () => new Response('static page 2') });
    if (blogPage.status !== 200 || await blogPage.text() !== 'static page 2') fail('Crawlable Blog pagination is not preserved by the redirect function.');
    const blogPageOne = await blog.onRequest({ request: new Request('https://www.jtorrent.net/blog/page/1/'), params: { path: ['page', '1'] }, env, next: async () => new Response('unexpected') });
    if (blogPageOne.status !== 301 || new URL(blogPageOne.headers.get('location')).pathname !== '/blog/') fail('Duplicate Blog page 1 route is not redirected to /blog/.');
    const blogUnknown = await blog.onRequest({ request: new Request('https://www.jtorrent.net/blog/unknown'), params: { path: 'unknown' }, env, next: async () => new Response('static') });
    if (blogUnknown.status !== 301 || new URL(blogUnknown.headers.get('location')).pathname !== '/blog/') fail('Unknown legacy blog paths must redirect to /blog/.');
    const blogPost = await blog.onRequest({ request: new Request('https://www.jtorrent.net/blog/old', { method: 'POST' }), params: { path: 'old' }, env, next: async () => new Response('static') });
    if (blogPost.status !== 405 || blogPost.headers.get('allow') !== 'GET, HEAD') fail('Legacy blog redirects must reject unsupported methods with the correct Allow header.');

    const api = await import(`${pathToFileURL(path.join(root, 'functions/api/search.js')).href}?qc=${stamp}`);
    const valid = await api.onRequest({ request: new Request('https://www.jtorrent.net/api/search?q=ubuntu&mode=sites&sources=archive,linux') });
    const validBody = await valid.json();
    if (valid.status !== 200 || validBody.results?.length !== 2) fail('Compatibility search API does not return validated site links.');
    if (valid.headers.get('cache-control') !== 'no-store, max-age=0' || !valid.headers.get('x-robots-tag')?.includes('noindex')) fail('Compatibility search API privacy/cache headers are incomplete.');
    if ((await api.onRequest({ request: new Request('https://www.jtorrent.net/api/search?q=x&mode=sites&sources=archive') })).status !== 400) fail('Compatibility search API must reject short queries.');
    if ((await api.onRequest({ request: new Request('https://www.jtorrent.net/api/search?q=ubuntu&mode=bad&sources=archive') })).status !== 400) fail('Compatibility search API must reject unsupported modes.');
    if ((await api.onRequest({ request: new Request('https://www.jtorrent.net/api/search?q=ubuntu&mode=sites&sources=unsupported') })).status !== 400) fail('Compatibility search API must reject unsupported sources.');
    const apiPost = await api.onRequest({ request: new Request('https://www.jtorrent.net/api/search', { method: 'POST' }) });
    if (apiPost.status !== 405 || apiPost.headers.get('allow') !== 'GET') fail('Compatibility search API must reject unsupported methods with the correct Allow header.');
  } catch (error) {
    fail(`Cloudflare Function behavior tests failed: ${error.stack || error.message}`);
  }
}
await validateFunctions();

try {
  execFileSync(process.execPath, [path.join(root, 'scripts/new-content.mjs'), '--check'], { stdio: 'pipe' });
} catch (error) {
  fail(`Content scaffolder self-check failed: ${String(error.stderr || error.message).trim()}`);
}
try {
  execFileSync(process.execPath, [path.join(root, 'scripts/verify-editorial-phase3.mjs')], { stdio: 'pipe' });
} catch (error) {
  fail(`Phase 3 parity verification failed: ${String(error.stderr || error.message).trim()}`);
}
try {
  execFileSync(process.execPath, [path.join(root, 'scripts/test-editorial-system.mjs')], { stdio: 'pipe' });
} catch (error) {
  fail(`Editorial system behavior tests failed: ${String(error.stderr || error.message).trim()}`);
}
try {
  execFileSync(process.execPath, [path.join(root, 'scripts/test-phase4-safety.mjs')], { stdio: 'pipe' });
} catch (error) {
  fail(`Phase 4 integrity utility tests failed: ${String(error.stderr || error.message).trim()}`);
}
try {
  execFileSync(process.execPath, [path.join(root, 'scripts/test-phase4-growth.mjs')], { stdio: 'pipe' });
} catch (error) {
  fail(`Phase 4 editorial growth test failed: ${String(error.stderr || error.message).trim()}`);
}
try {
  execFileSync(process.execPath, [path.join(root, 'scripts/verify-editorial-phase4.mjs')], { stdio: 'pipe' });
} catch (error) {
  fail(`Phase 4 implementation verification failed: ${String(error.stderr || error.message).trim()}`);
}

if (!await exists('dist/index.html')) fail('dist/index.html is missing. Run npm run build before npm run qc.');
if (Object.hasOwn(siteData, 'hubs')) fail('Hub definitions still exist in content/site.json; content/sections.json must be the canonical registry.');
if (await exists('content/articles.json')) fail('Obsolete content/articles.json still exists.');
if (await exists('content-package-manifest.json')) fail('Obsolete historical content-package-manifest.json still exists.');
if (await exists('package/publish-manifest.json')) fail('Temporary package/publish-manifest.json was not consumed before QC.');
if (site.author?.name !== 'Jivaro Torrent Editorial' || site.author?.type !== 'Organization') fail('The site-wide editorial author identity is not configured as Jivaro Torrent Editorial.');
if (!site.founder?.name) fail('Founder information is missing from content/site.json.');
const contentSourceFiles = (await walk(path.join(root, 'content'))).filter((file) => /\.(?:json|html)$/.test(file));
for (const file of contentSourceFiles) {
  const relative = path.relative(root, file).split(path.sep).join('/');
  const value = await fs.readFile(file, 'utf8');
  if (/NetPeek/.test(value)) fail(`Retired NetPeek product branding remains in current canonical content: ${relative}`);
  if (/https:\/\/jivaro\.net\/content\/apps\/netpeek/i.test(value)) fail(`Retired NetPeek app URL remains in canonical content: ${relative}`);
}
const contentPrompt = await read('docs/jtorrent-content-package-prompt.txt').catch(() => '');
for (const obsolete of ['content/articles.json', 'root content-package-manifest.json']) {
  if (!contentPrompt.includes(obsolete)) continue;
  if (obsolete === 'content/articles.json' && contentPrompt.includes('Never create or restore content/articles.json')) continue;
  if (obsolete === 'root content-package-manifest.json' && contentPrompt.includes('Do not create root content-package-manifest.json')) continue;
}
for (const required of ['content/<section>/<slug>/', 'article.json', 'body.html', 'package/publish-manifest.json', 'Publish-Content.ps1']) {
  if (!contentPrompt.includes(required)) fail(`Current JTorrent content prompt is missing: ${required}`);
}
if (editorial.canonicalArticles.length !== articles.length + editorial.drafts.length) fail('Not every editorial item is owned by a canonical source folder.');
if (editorial.legacyArticles.length || editorial.replacedLegacyPaths.length) fail('Legacy monolith records remain in the editorial loader.');
if (editorial.drafts.some((article) => articles.some((published) => published.path === article.path))) fail('A draft article is exposed in the public article set.');
const placeholderPattern = /\b(?:lorem ipsum|todo|tbd|coming soon|placeholder|insert (?:copy|text|image)|your (?:title|content) here)\b/i;
const exportDebrisPattern = /(?:data-section-id|data-block-type|<!--\s*wp:|squarespace-block|NotionExport)/i;
for (const article of editorial.canonicalArticles) {
  if (article.sourceFiles?.length !== 2 || !article.toc?.length) fail(`Canonical source ownership or TOC metadata is incomplete: ${article.path}`);
  if (article.toc?.some((heading) => !heading.id || !heading.text || /^section-\d+$/.test(heading.id))) fail(`Canonical article has an invalid deterministic heading ID: ${article.path}`);
  if (article.author !== site.author.name) fail(`Article author diverges from the canonical site author identity: ${article.path}`);
  if (!['Article', 'NewsArticle'].includes(article.schemaType)) fail(`Unsupported editorial schema type ${article.schemaType}: ${article.path}`);
  if (article.draft && article.featured) fail(`Draft content cannot be featured: ${article.path}`);
  const bodyFile = article.sourceFiles.find((file) => file.endsWith('/body.html'));
  const metadataFile = article.sourceFiles.find((file) => file.endsWith('/article.json'));
  const bodySource = bodyFile ? await read(bodyFile) : '';
  const metadataSource = metadataFile ? await read(metadataFile) : '';
  if (placeholderPattern.test(`${metadataSource}\n${bodySource}`)) fail(`Placeholder language remains in canonical source: ${article.path}`);
  if (exportDebrisPattern.test(bodySource)) fail(`Export or migration debris remains in canonical body source: ${article.path}`);
  const paragraphs = [...bodySource.matchAll(/<p\b[^>]*>([\s\S]*?)<\/p>/gi)]
    .map((match) => textContent(match[1]).toLowerCase())
    .filter((value) => value.length >= 80);
  if (new Set(paragraphs).size !== paragraphs.length) fail(`Canonical body contains a repeated substantive paragraph: ${article.path}`);
}
const phase3Parity = JSON.parse(await read('docs/editorial-migration/phase-3-parity.json'));
if (editorial.canonicalArticles.length < phase3Parity.articleCount) fail('The repository contains fewer canonical articles than the Phase 3 migration baseline.');
for (const expected of phase3Parity.articles || []) {
  if (!editorial.canonicalArticles.some((article) => article.path === expected.path)) fail(`Phase 3 migrated route is missing: ${expected.path}`);
  for (const legacyUrl of expected.legacyUrls || []) {
    const slug = legacyUrl.replace(/^\/blog\//, '').replace(/\/$/, '');
    if (editorial.legacyRedirects[slug] !== expected.path) fail(`Phase 3 migrated legacy redirect changed: ${legacyUrl}`);
  }
}
if (Object.keys(editorial.legacyRedirects).length < phase3Parity.legacyRedirectCount) fail('The repository contains fewer legacy redirects than the Phase 3 migration baseline.');

const gitignore = await read('.gitignore');
if (!gitignore.includes('**/[Dd]esktop.ini')) fail('.gitignore does not ignore both Desktop.ini and desktop.ini recursively.');
const wrangler = await read('wrangler.toml');
if (!/pages_build_output_dir\s*=\s*"\.\/dist"/.test(wrangler)) fail('wrangler.toml does not point Cloudflare Pages to ./dist.');

const redirects = await read('public/_redirects');
for (const provider of site.providers || []) {
  const slug = escapePattern(provider.slug);
  if (!new RegExp(`^/${slug}\\s+/go/${slug}\\s+302$`, 'm').test(redirects)) fail(`Missing provider redirect without trailing slash: /${provider.slug}`);
  if (!new RegExp(`^/${slug}/\\s+/go/${slug}\\s+302$`, 'm').test(redirects)) fail(`Missing provider redirect with trailing slash: /${provider.slug}/`);
}
if (/^\/blog\/\*/m.test(redirects)) fail('A wildcard _redirects rule must not intercept the live /blog/ index.');
const routesConfig = JSON.parse(await read('public/_routes.json'));
for (const expected of ['/api/*', '/go/*', '/blog/*']) if (!routesConfig.include?.includes(expected)) fail(`Cloudflare Function routing is missing ${expected}.`);

const headers = await read('public/_headers');
for (const expected of [
  'Strict-Transport-Security: max-age=31536000',
  "frame-ancestors 'none'",
  "script-src 'self'",
  "style-src 'self'",
  'https://officialjivaro.github.io',
  'X-Frame-Options: DENY'
]) if (!headers.includes(expected)) fail(`Security headers are missing: ${expected}`);
if (headers.includes("'unsafe-inline'")) fail('Content Security Policy still allows unsafe inline scripts or styles.');
if (!headers.includes('/editorial/*') || !headers.includes('X-Robots-Tag: noindex, nofollow, noarchive')) fail('Generated editorial manifests are not protected by noindex headers.');

const manifest = JSON.parse(await read('public/site.webmanifest'));
if (manifest.background_color !== '#f5f8fa' || manifest.theme_color !== '#f5f8fa') fail('Web app manifest does not match the default light design system.');
if (!Array.isArray(manifest.icons) || manifest.icons.length < 2) fail('Web app manifest is missing installable PNG icons.');
for (const expected of [['192x192', '/assets/images/logo-192.png'], ['512x512', '/assets/images/logo-512.png']]) {
  const iconEntry = manifest.icons?.find((item) => item.sizes === expected[0] && item.src === expected[1] && item.type === 'image/png');
  if (!iconEntry || !await exists(path.join('public', expected[1].replace(/^\//, '')))) fail(`Web app manifest icon is missing: ${expected[0]}.`);
}
if (manifest.id !== '/' || manifest.start_url !== '/' || manifest.scope !== '/' || manifest.display !== 'standalone') fail('Web app manifest scope/start/display settings are incorrect.');
for (const expectedSize of [192, 512]) {
  const entry = manifest.icons?.find((icon) => icon.sizes === `${expectedSize}x${expectedSize}` && icon.type === 'image/png');
  if (!entry?.src?.startsWith('/')) { fail(`Web app manifest is missing its ${expectedSize}x${expectedSize} PNG icon.`); continue; }
  const buffer = await fs.readFile(path.join(root, 'public', entry.src.replace(/^\//, ''))).catch(() => null);
  const dimensions = buffer ? pngDimensions(buffer) : null;
  if (!dimensions || dimensions.width !== expectedSize || dimensions.height !== expectedSize) fail(`Manifest icon ${entry.src} has incorrect dimensions.`);
  if (!String(entry.purpose || '').split(/\s+/).includes('maskable')) warn(`Manifest icon ${entry.src} is not marked maskable.`);
}
const favicon = await read('public/favicon.svg');
if (!favicon.includes('viewBox="0 0 64 64"') || !favicon.includes('aria-label="Jivaro Torrent"')) fail('favicon.svg is missing its viewBox or accessible label.');
if (!await exists('public/assets/images/logo-512.webp')) fail('Organization logo WebP is missing.');

const css = await read('public/css/site.css');
let braceDepth = 0;
for (const char of css.replace(/\/\*[\s\S]*?\*\//g, '')) {
  if (char === '{') braceDepth += 1;
  if (char === '}') braceDepth -= 1;
  if (braceDepth < 0) break;
}
if (braceDepth !== 0) fail(`CSS braces are unbalanced (${braceDepth}).`);
for (const obsolete of ['.home-hero', '.search-console', '.trust-strip', '.hub-grid', '.split-feature', '.legal-band']) {
  if (css.includes(obsolete)) fail(`Obsolete homepage selector remains in shared CSS: ${obsolete}`);
}
if (!css.includes('@media (prefers-reduced-motion: reduce)')) fail('Shared CSS does not provide a reduced-motion mode.');
if (!css.includes(':focus-visible')) fail('Shared CSS does not provide visible keyboard focus styling.');

const client = await read('public/js/site.js');
const searchClient = await read('public/js/search.js');
const providerClient = await read('public/js/providers.js');
const themeClient = await read('public/js/theme.js');
const sourceRegistry = JSON.parse(await read('content/search-sources.json'));
const expectedAssetHash = createHash('sha256').update(css).update(client).update(searchClient).update(providerClient).update(themeClient).digest('hex').slice(0, 12);
for (const asset of [
  `dist/css/site.${expectedAssetHash}.css`,
  `dist/js/site.${expectedAssetHash}.js`,
  `dist/js/search.${expectedAssetHash}.js`,
  `dist/js/providers.${expectedAssetHash}.js`,
  `dist/js/theme.${expectedAssetHash}.js`
]) if (!await exists(asset)) fail(`Fingerprint asset is missing: ${asset}`);
if (!headers.includes('/css/*') || !headers.includes('/js/*') || !headers.includes('max-age=31536000, immutable')) fail('Fingerprint assets are not configured for immutable caching.');
if (Buffer.byteLength(css) > 80_000) warn(`Shared CSS is larger than 80 KB (${Buffer.byteLength(css).toLocaleString()} bytes).`);
if (Buffer.byteLength(client) > 12_288) fail(`Core client JavaScript exceeds the 12 KB budget (${Buffer.byteLength(client).toLocaleString()} bytes).`);
if (Buffer.byteLength(searchClient) > 80_000) warn(`Search JavaScript is larger than 80 KB (${Buffer.byteLength(searchClient).toLocaleString()} bytes).`);
if (Buffer.byteLength(providerClient) > 8_192) warn(`Provider JavaScript is larger than 8 KB (${Buffer.byteLength(providerClient).toLocaleString()} bytes).`);
if (Buffer.byteLength(themeClient) > 4_096) warn(`Theme bootstrap is larger than 4 KB (${Buffer.byteLength(themeClient).toLocaleString()} bytes).`);
if (sourceRegistry.version !== 3 || !Array.isArray(sourceRegistry.sources) || !Array.isArray(sourceRegistry.groups)) fail('Search source registry is missing or has the wrong version.');
const recommendedSources = sourceRegistry.sources.filter((source) => source.defaultSelected);
const thirdPartySources = sourceRegistry.sources.filter((source) => !source.defaultSelected);
if (recommendedSources.length !== 8) fail(`Expected 8 recommended open/official sources; found ${recommendedSources.length}.`);
if (thirdPartySources.length !== 15) fail(`Expected the 15 retained third-party sources; found ${thirdPartySources.length}.`);
if (new Set(sourceRegistry.sources.map((source) => source.id)).size !== sourceRegistry.sources.length) fail('Search source IDs must be unique.');
for (const source of sourceRegistry.sources) {
  if (!source.template?.startsWith('https://') || !source.template.includes('{query}')) fail(`Search source has an invalid HTTPS template: ${source.id}`);
}
for (const required of [
  "const BACKEND_URL = 'https://officialjivaro.github.io/backend-jtorrent'",
  'data/status.json',
  'data/v3/manifest.json',
  'data/v2/manifest.json',
  'hydrateDocs',
  'static_score',
  'scoreMap',
  'pageIds = ids.slice(offset, offset + limit)',
  'popstate',
  'localStorage'
]) if (!searchClient.includes(required)) fail(`Search client integration is missing: ${required}`);
if (/https:\/\/officialjivaro\.github\.io\/jtorrent(?:\/|['"])/.test(searchClient)) fail('Search client still points to the retired officialjivaro/jtorrent backend.');
for (const term of ['public', 'domain', 'creative', 'commons', 'archive', 'official', 'torrent', 'download', 'metadata']) {
  if (new RegExp(`FALLBACK_STOPWORDS[^;]*['"]${term}['"]`).test(searchClient)) fail(`Meaningful search term remains a frontend stopword: ${term}`);
}
if (searchClient.includes('limit * 4')) fail('Search client still hydrates an oversized candidate window before pagination.');
if (client.includes("if (isHome) root.dataset.theme = 'light'")) fail('Homepage still overrides the shared saved theme.');

const affiliateFunction = await read('functions/go/[slug].js');
const destinationCount = (affiliateFunction.match(/^\s*(?:'[^']+'|[a-zA-Z0-9_-]+):\s*'https:\/\//gm) || []).length;
if (destinationCount !== 21) fail(`Affiliate allowlist must contain 21 destinations; found ${destinationCount}.`);
if (!affiliateFunction.includes("'Referrer-Policy': 'no-referrer'")) fail('Affiliate redirects do not suppress the referrer.');
if (!affiliateFunction.includes('status: 302')) fail('Affiliate redirect is not temporary (302).');

const blogFunction = await read('functions/blog/[[path]].js');
if (!blogFunction.includes("'/editorial/legacy-redirects.json'") || !blogFunction.includes('context.env.ASSETS.fetch')) fail('Blog redirects are not loading the generated legacy manifest through the Pages asset binding.');
const legacyManifest = JSON.parse(await read('dist/editorial/legacy-redirects.json'));
if (legacyManifest.redirectCount !== Object.keys(editorial.legacyRedirects).length || JSON.stringify(legacyManifest.redirects) !== JSON.stringify(editorial.legacyRedirects)) fail('Generated legacy redirect manifest does not match canonical metadata.');
const contentManifest = JSON.parse(await read('dist/editorial/content-manifest.json'));
if (contentManifest.articleCount !== articles.length || contentManifest.draftCount !== editorial.drafts.length || contentManifest.sectionCount !== site.hubs.length) fail('Generated editorial content manifest counts are incorrect.');
const buildFingerprint = JSON.parse(await read('dist/.well-known/jtorrent-build.json').catch(() => '{}'));
if (buildFingerprint.project !== 'website-jtorrent-net' || buildFingerprint.articleCount !== articles.length || buildFingerprint.routeCount < 43 || buildFingerprint.searchContract !== 'v3-with-v2-fallback') fail('Generated deployment fingerprint is missing or inconsistent.');

const routeFiles = (await walk(dist)).filter((file) => file.endsWith('.html'));
const hubPageCount = site.hubs.reduce((sum, hub) => sum + Math.max(1, Math.ceil(articles.filter((article) => article.hub === hub.slug).length / hub.pageSize)), 0);
const blogPageCount = Math.max(1, Math.ceil(articles.length / 12));
const staticPageCount = 5;
const expectedRouteCount = 1 + hubPageCount + blogPageCount + articles.length + staticPageCount + 1;
if (routeFiles.length !== expectedRouteCount) fail(`Expected ${expectedRouteCount} generated HTML files including 404; found ${routeFiles.length}.`);
const routeMap = new Map();
for (const file of routeFiles) {
  const relative = path.relative(dist, file).replaceAll(path.sep, '/');
  const route = relative === 'index.html' ? '/' : relative === '404.html' ? '/404.html' : `/${relative.replace(/\/index\.html$/, '')}/`;
  routeMap.set(route, { file, relative, html: await fs.readFile(file, 'utf8') });
}

const titles = new Map();
const descriptions = new Map();
for (const [route, page] of routeMap) {
  const { html, relative } = page;
  if (!html.startsWith('<!doctype html>')) fail(`Missing doctype: ${relative}`);
  if (/<style\b|\sstyle="/i.test(html)) fail(`Unexpected inline CSS found in generated output: ${relative}`);
  if (!html.includes('<html lang="en" data-theme="light">')) fail(`Missing language/default theme: ${relative}`);
  if ((html.match(/<h1(?:\s|>)/g) || []).length !== 1) fail(`Expected exactly one h1: ${relative}`);
  if (!html.includes('<main id="main">') || !html.includes('href="#main"')) fail(`Missing main landmark or skip link: ${relative}`);
  if (!html.includes('class="icon-button theme-toggle"')) fail(`Missing shared theme control: ${relative}`);
  if (route === '/') {
    if (!html.includes('<footer class="search-footer">')) fail('Homepage is missing its compact legal footer.');
    if (html.includes('<h2>Explore</h2>')) fail('Homepage compact footer must not expose the hub directory.');
  } else if (!html.includes('<footer class="site-footer">') || !html.includes('<h2>Explore</h2>')) {
    fail(`Missing shared site footer: ${relative}`);
  }

  const title = textContent(html.match(/<title>([\s\S]*?)<\/title>/)?.[1] || '');
  const description = getAttribute(html.match(/<meta name="description"[^>]*>/)?.[0] || '', 'content') || '';
  if (title.length < 10 || title.length > 65) fail(`Title length ${title.length} is outside 10–65: ${relative}`);
  if (description.length < 70 || description.length > 160) fail(`Description length ${description.length} is outside 70–160: ${relative}`);
  if (titles.has(title)) fail(`Duplicate title in ${relative} and ${titles.get(title)}: ${title}`); else titles.set(title, relative);
  if (descriptions.has(description)) fail(`Duplicate description in ${relative} and ${descriptions.get(description)}.`); else descriptions.set(description, relative);

  const expectedCanonical = canonical(route);
  const canonicals = [...html.matchAll(/<link rel="canonical" href="([^"]+)">/g)].map((match) => match[1]);
  if (canonicals.length !== 1 || canonicals[0] !== expectedCanonical) fail(`Canonical mismatch in ${relative}; expected ${expectedCanonical}.`);
  for (const expected of ['og:title', 'og:description', 'og:image', 'og:image:alt']) if (!html.includes(`property="${expected}"`)) fail(`Missing ${expected}: ${relative}`);
  for (const expected of ['twitter:card', 'twitter:title', 'twitter:description', 'twitter:image', 'twitter:image:alt']) if (!html.includes(`name="${expected}"`)) fail(`Missing ${expected}: ${relative}`);

  const ids = [...html.matchAll(/\sid="([^"]+)"/g)].map((match) => match[1]);
  const uniqueIds = new Set(ids);
  if (uniqueIds.size !== ids.length) fail(`Duplicate HTML id in ${relative}.`);
  for (const control of html.matchAll(/aria-controls="([^"]+)"/g)) if (!uniqueIds.has(control[1])) fail(`aria-controls target ${control[1]} is missing in ${relative}.`);

  const headings = [...html.matchAll(/<h([1-6])(?:\s|>)/g)].map((match) => Number(match[1]));
  for (let index = 1; index < headings.length; index += 1) {
    if (headings[index] > headings[index - 1] + 1) fail(`Heading level skips from h${headings[index - 1]} to h${headings[index]} in ${relative}.`);
  }
  for (const nav of html.matchAll(/<nav\b([^>]*)>/gi)) {
    if (!/aria-(?:label|labelledby)="[^"]+"/i.test(nav[1])) fail(`Navigation landmark has no accessible label in ${relative}.`);
  }
  for (const button of html.matchAll(/<button\b([^>]*)>([\s\S]*?)<\/button>/gi)) {
    const label = getAttribute(`<button ${button[1]}>`, 'aria-label');
    if (!label && !textContent(button[2])) fail(`Button has no accessible name in ${relative}.`);
  }
  for (const input of html.matchAll(/<input\b[^>]*>/gi)) {
    const tag = input[0];
    if ((getAttribute(tag, 'type') || '').toLowerCase() === 'hidden') continue;
    const id = getAttribute(tag, 'id');
    const labelled = getAttribute(tag, 'aria-label') || getAttribute(tag, 'aria-labelledby') || (id && new RegExp(`<label\\b[^>]*for="${escapePattern(id)}"`, 'i').test(html));
    if (!labelled) fail(`Form control has no accessible label in ${relative}: ${id || tag}`);
  }
  for (const anchor of html.matchAll(/<a\b[^>]*target="_blank"[^>]*>/gi)) {
    const rel = new Set((getAttribute(anchor[0], 'rel') || '').split(/\s+/));
    if (!rel.has('noopener')) fail(`New-window link is missing rel=noopener in ${relative}.`);
  }
  for (const anchor of html.matchAll(/<a\b[^>]*href="\/go\/[^"]+"[^>]*>/gi)) {
    const rel = new Set((getAttribute(anchor[0], 'rel') || '').split(/\s+/));
    if (!rel.has('nofollow') || !rel.has('sponsored')) fail(`Affiliate link is missing nofollow/sponsored in ${relative}.`);
  }
  if (/href="javascript:/i.test(html)) fail(`javascript: URL found in ${relative}.`);
  const stylePath = `/css/site.${expectedAssetHash}.css`;
  const scriptPath = `/js/site.${expectedAssetHash}.js`;
  const searchPath = `/js/search.${expectedAssetHash}.js`;
  const providerPath = `/js/providers.${expectedAssetHash}.js`;
  const themePath = `/js/theme.${expectedAssetHash}.js`;
  if (!html.includes(`href="${stylePath}"`) || !html.includes(`src="${scriptPath}"`) || !html.includes(`src="${themePath}"`)) fail(`Fingerprint core asset references are incomplete in ${relative}.`);
  if (route === '/' && !html.includes(`src="${searchPath}"`)) fail('Homepage is missing its page-specific search script.');
  if (route !== '/' && html.includes(`src="${searchPath}"`)) fail(`Search JavaScript is loaded outside the homepage: ${relative}.`);
  if (route === '/providers/' && !html.includes(`src="${providerPath}"`)) fail('Provider directory is missing its page-specific filter script.');
  if (route !== '/providers/' && html.includes(`src="${providerPath}"`)) fail(`Provider JavaScript is loaded outside the provider directory: ${relative}.`);
  if (html.indexOf(`src="${themePath}"`) > html.indexOf(`href="${stylePath}"`)) fail(`Theme bootstrap must load before the stylesheet in ${relative}.`);
  if (/\/(?:css|js)\/(?:site|theme|search|providers)\.(?:css|js)"/.test(html)) fail(`Unfingerprinted CSS/JS reference found in ${relative}.`);

  for (const image of html.matchAll(/<img\b[^>]*>/gi)) {
    const tag = image[0];
    const source = getAttribute(tag, 'src');
    const alt = getAttribute(tag, 'alt');
    if (!source || !source.startsWith('/')) fail(`Image has an invalid src in ${relative}.`);
    if (alt === null || !alt.trim()) fail(`Image has missing/empty alt text in ${relative}: ${source || '(unknown)'}`);
    if (!getAttribute(tag, 'width') || !getAttribute(tag, 'height')) fail(`Image is missing width/height in ${relative}: ${source}`);
    if (source && !await exists(path.join('dist', source.replace(/^\//, '')))) fail(`Image file is missing in ${relative}: ${source}`);
  }

  const jsonLd = [...html.matchAll(/<script type="application\/ld\+json">([\s\S]*?)<\/script>/g)];
  const types = [];
  for (const entry of jsonLd) {
    try { types.push(JSON.parse(entry[1])?.['@type']); }
    catch (error) { fail(`Invalid JSON-LD in ${relative}: ${error.message}`); }
  }
  if (route === '/') {
    if (!types.includes('Organization') || !types.includes('WebSite')) fail('Homepage is missing Organization or WebSite schema.');
  } else if (route !== '/404.html' && !types.includes('BreadcrumbList')) fail(`Missing BreadcrumbList schema: ${relative}`);
  const article = articles.find((item) => item.path === route);
  if (article) {
    if (!types.includes(article.schemaType || 'Article')) fail(`Missing ${article.schemaType || 'Article'} schema: ${relative}`);
    if (article.faq?.length && !types.includes('FAQPage')) fail(`Missing FAQ schema: ${relative}`);
    if (!html.includes('fetchpriority="high"') || !html.includes('decoding="async"')) fail(`Article hero is not performance-hinted: ${relative}`);
    if (article.bodyHtml) {
      const articleHeadings = [...html.matchAll(/<h([23])\b[^>]*id="([^"]+)"[^>]*>/g)].map((match) => ({ level: Number(match[1]), id: match[2] }));
      if (articleHeadings.length < article.toc.length) fail(`Canonical article headings are missing generated IDs: ${relative}`);
      for (const entry of article.toc) {
        if (!uniqueIds.has(entry.id)) fail(`Canonical TOC heading target is missing in ${relative}: ${entry.id}`);
        if (!html.includes(`href="#${entry.id}"`)) fail(`Canonical TOC link is missing in ${relative}: ${entry.id}`);
        if (/^section-\d+$/.test(entry.id)) fail(`Canonical article uses a positional heading ID in ${relative}: ${entry.id}`);
      }
      if (!html.includes('class="article-section"')) fail(`Canonical body sections did not receive shared article styling: ${relative}`);
    }
  }
  const isCollectionRoute = route === '/blog/' || /^\/blog\/page\/[2-9][0-9]*\/$/.test(route) || site.hubs.some((hub) => route === `/${hub.slug}/` || new RegExp(`^/${hub.slug}/page/[2-9][0-9]*/$`).test(route));
  if (isCollectionRoute) {
    const hasPublishedItems = (html.match(/class="article-card/g) || []).length > 0;
    if (hasPublishedItems && !types.includes('ItemList')) fail(`Collection page is missing ItemList schema: ${relative}`);
    if (route.includes('/page/') && !html.includes('rel="prev"')) fail(`Paginated route is missing rel=prev: ${relative}`);
  }
  if (route === '/404.html' && !html.includes('name="robots" content="noindex,follow"')) fail('404 page is not marked noindex.');
}

const knownRoutes = new Set(routeMap.keys());
for (const [route, page] of routeMap) {
  for (const match of page.html.matchAll(/(?:href|src)="(\/[^"]*)"/g)) {
    const raw = match[1].split('#')[0].split('?')[0];
    if (!raw || raw.startsWith('/go/') || raw.startsWith('/api/')) continue;
    if (/\.[a-z0-9]+$/i.test(raw)) {
      if (!await exists(path.join('dist', raw.replace(/^\//, '')))) fail(`Broken asset link ${raw} in ${page.relative}.`);
      continue;
    }
    const normalized = raw.endsWith('/') ? raw : `${raw}/`;
    if (!knownRoutes.has(raw) && !knownRoutes.has(normalized)) fail(`Broken internal route ${raw} in ${page.relative}.`);
  }
}

const home = routeMap.get('/')?.html || '';
if ((home.match(/role="tab"/g) || []).length !== 2) fail('Homepage must have exactly two search-mode tabs.');
if ((home.match(/class="search-quick-links"/g) || []).length !== 1 || !home.includes('Best VPNs') || !home.includes('Best Proxies')) fail('Homepage VPN/proxy links are missing.');
for (const forbidden of ['What is Jivaro Torrent?', 'class="hub-grid"', 'Featured guides']) if (home.includes(forbidden)) fail(`Homepage contains forbidden publication content: ${forbidden}`);
if (!home.includes('class="search-footer"')) fail('Homepage is not using the compact legal footer.');
if (home.includes('<h2>Explore</h2>')) fail('Homepage footer contains hub-directory content.');
if ((home.match(/<nav class="desktop-nav"/g) || []).length !== 1) fail('Homepage shared navigation is missing.');

for (const article of articles) {
  const variants = [
    { source: article.hero?.srcSmall || `/assets/images/${article.slug}-960.webp`, width: article.hero?.smallWidth || 960, height: article.hero?.smallHeight || 540 },
    { source: article.hero?.src || `/assets/images/${article.slug}-1600.webp`, width: article.hero?.width || 1600, height: article.hero?.height || 900 }
  ];
  for (const variant of variants) {
    const relative = `public/${variant.source.replace(/^\//, '')}`;
    const buffer = await fs.readFile(path.join(root, relative)).catch(() => null);
    if (!buffer) { fail(`Missing hero image: ${relative}`); continue; }
    const dimensions = webpDimensions(buffer);
    if (!dimensions || dimensions.width !== variant.width || dimensions.height !== variant.height) fail(`Incorrect image dimensions for ${relative}: ${JSON.stringify(dimensions)}`);
    if (variant.width === 1600 && buffer.length > 153_600) fail(`Article hero exceeds the 150 KB performance budget: ${relative} (${buffer.length.toLocaleString()} bytes)`);
  }
}

const sourceFiles = (await walk(root)).filter((file) => !file.includes(`${path.sep}.git${path.sep}`) && !file.includes(`${path.sep}node_modules${path.sep}`) && !file.includes(`${path.sep}dist${path.sep}`));
const forbiddenMetadataNames = new Set(['desktop.ini', 'thumbs.db', '.ds_store']);
for (const file of sourceFiles) {
  if (forbiddenMetadataNames.has(path.basename(file).toLowerCase())) fail(`OS metadata file found in source: ${path.relative(root, file)}`);
}
for (const file of await walk(dist)) {
  if (forbiddenMetadataNames.has(path.basename(file).toLowerCase())) fail(`OS metadata file found in output: ${path.relative(dist, file)}`);
}

const sitemap = await fs.readFile(path.join(dist, 'sitemap.xml'), 'utf8').catch(() => '');
const noindexRoutes = new Set(articles.filter((article) => article.indexing === false).map((article) => article.path));
for (const route of [...knownRoutes].filter((route) => route !== '/404.html' && !noindexRoutes.has(route))) {
  if (!sitemap.includes(`<loc>${canonical(route)}</loc>`)) fail(`Sitemap is missing ${route}.`);
}
for (const route of noindexRoutes) if (sitemap.includes(`<loc>${canonical(route)}</loc>`)) fail(`Sitemap contains noindex article ${route}.`);
const robots = await fs.readFile(path.join(dist, 'robots.txt'), 'utf8').catch(() => '');
if (!robots.includes(`Sitemap: ${canonical('/sitemap-index.xml')}`)) fail('robots.txt does not reference sitemap-index.xml.');
if (!robots.includes('Disallow: /editorial/')) fail('robots.txt does not exclude generated editorial manifests.');
const rss = await fs.readFile(path.join(dist, 'rss.xml'), 'utf8').catch(() => '');
const expectedFeedItems = indexableArticles.length;
if (!rss.includes('rel="self"') || (rss.match(/<item>/g) || []).length !== expectedFeedItems) fail(`Global RSS feed is missing its self link or expected ${expectedFeedItems} entries.`);
for (const article of articles.filter((item) => item.indexing === false)) if (rss.includes(canonical(article.path))) fail(`RSS contains noindex article ${article.path}.`);
for (const hub of site.hubs) {
  const hubArticles = indexableArticles.filter((article) => article.hub === hub.slug);
  const feedPath = path.join(dist, hub.slug, 'rss.xml');
  if (hub.feed && hubArticles.length) {
    const feed = await fs.readFile(feedPath, 'utf8').catch(() => '');
    if (!feed.includes(`href="${canonical(`/${hub.slug}/rss.xml`)}"`) || (feed.match(/<item>/g) || []).length !== hubArticles.length) fail(`Hub RSS feed is incomplete for ${hub.slug}.`);
    for (const article of hubArticles) if (!feed.includes(canonical(article.path))) fail(`Hub RSS feed is missing ${article.path}.`);
  } else if (await exists(path.relative(root, feedPath))) fail(`Empty or disabled hub has an unexpected RSS feed: ${hub.slug}.`);
}

const searchIndex = JSON.parse(await fs.readFile(path.join(dist, 'search-index.json'), 'utf8').catch(() => '[]'));
if (searchIndex.length !== indexableArticles.length) fail(`Search index must contain ${indexableArticles.length} public indexable articles; found ${searchIndex.length}.`);
const searchPaths = searchIndex.map((item) => item.path);
if (new Set(searchPaths).size !== searchPaths.length) fail('Search index contains duplicate article routes.');
for (const article of indexableArticles) if (!searchPaths.includes(article.path)) fail(`Search index is missing ${article.path}.`);
for (const draft of editorial.drafts) if (searchPaths.includes(draft.path)) fail(`Search index exposes draft ${draft.path}.`);

if (warnings.length) console.warn(warnings.map((warning) => `WARN: ${warning}`).join('\n'));
if (errors.length) {
  console.error(errors.map((error) => `ERROR: ${error}`).join('\n'));
  console.error(`\nQC failed with ${errors.length} error${errors.length === 1 ? '' : 's'}.`);
  process.exit(1);
}

console.log(`QC passed: ${routeFiles.length} HTML files, ${articles.length} canonical articles, ${site.hubs.length} hubs, ${blogPageCount} Blog pages, 8 default + 15 optional site-search sources, 21 affiliate destinations, ${Object.keys(editorial.legacyRedirects).length} direct legacy redirects, and ${articles.length * 2 + 2} responsive image assets.`);
