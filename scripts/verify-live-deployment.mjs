import process from 'node:process';
import { pathToFileURL } from 'node:url';

const DEFAULTS = Object.freeze({
  baseUrl: 'https://www.jtorrent.net',
  apexUrl: 'https://jtorrent.net',
  previewUrl: 'https://website-jtorrent-net.pages.dev',
  backendUrl: 'https://officialjivaro.github.io/backend-jtorrent',
  timeoutMs: 15_000,
  minimumArticles: 27,
  skipApex: false,
  skipPreview: false,
  skipBackend: false,
  skipFunctions: false
});

const CONTENT_HUBS = Object.freeze([
  '/torrenting/',
  '/privacy/',
  '/proxies/',
  '/vpn/',
  '/apps/',
  '/software/',
  '/providers/',
  '/blog/'
]);

const LEGACY_REDIRECT = Object.freeze({
  source: '/blog/what-is-my-ip-use-netpeek-to-check-vpn-and-proxy-leaks',
  target: '/privacy/what-is-my-ip/'
});

const FORBIDDEN_STALE_MARKERS = Object.freeze([
  'static1.squarespace.com',
  'squarespace-cdn.com',
  'Open Menu Close Menu',
  'Command Palette',
  'Enhance Privacy, Find Torrents Effortlessly'
]);

const trimSlash = (value) => String(value || '').replace(/\/+$/, '');
const joinUrl = (base, pathname = '/') => `${trimSlash(base)}${pathname.startsWith('/') ? pathname : `/${pathname}`}`;

function parseArguments(argv) {
  const options = { ...DEFAULTS };
  for (let index = 0; index < argv.length; index += 1) {
    const token = argv[index];
    const next = () => {
      const value = argv[index + 1];
      if (!value || value.startsWith('--')) throw new Error(`${token} requires a value.`);
      index += 1;
      return value;
    };
    if (token === '--base-url') options.baseUrl = next();
    else if (token === '--apex-url') options.apexUrl = next();
    else if (token === '--preview-url') options.previewUrl = next();
    else if (token === '--backend-url') options.backendUrl = next();
    else if (token === '--timeout-ms') options.timeoutMs = Number.parseInt(next(), 10);
    else if (token === '--minimum-articles') options.minimumArticles = Number.parseInt(next(), 10);
    else if (token === '--skip-apex') options.skipApex = true;
    else if (token === '--skip-preview') options.skipPreview = true;
    else if (token === '--skip-backend') options.skipBackend = true;
    else if (token === '--skip-functions') options.skipFunctions = true;
    else if (token === '--help' || token === '-h') options.help = true;
    else throw new Error(`Unknown argument: ${token}`);
  }
  if (!Number.isInteger(options.timeoutMs) || options.timeoutMs < 500) throw new Error('--timeout-ms must be an integer of at least 500.');
  if (!Number.isInteger(options.minimumArticles) || options.minimumArticles < 1) throw new Error('--minimum-articles must be a positive integer.');
  return options;
}

function helpText() {
  return `Jivaro Torrent live deployment verifier\n\nUsage:\n  npm run verify:live -- [options]\n\nOptions:\n  --base-url <url>          Production base URL (default: https://www.jtorrent.net)\n  --apex-url <url>          Apex URL expected to redirect to production\n  --preview-url <url>       Cloudflare Pages preview/project URL\n  --backend-url <url>       Aggregated-search backend base URL\n  --timeout-ms <number>     Per-request timeout (default: 15000)\n  --minimum-articles <n>    Minimum published article count (default: 27)\n  --skip-apex               Skip apex-domain redirect validation\n  --skip-preview            Skip pages.dev validation\n  --skip-backend            Skip aggregated-search backend validation\n  --skip-functions          Skip /go/* and /blog/* Function validation\n`;
}

async function request(url, options = {}) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), options.timeoutMs || DEFAULTS.timeoutMs);
  try {
    return await fetch(url, {
      method: options.method || 'GET',
      redirect: options.redirect || 'follow',
      cache: 'no-store',
      headers: {
        'Accept': options.accept || '*/*',
        'Cache-Control': 'no-cache',
        'Pragma': 'no-cache',
        'User-Agent': 'Jivaro-Torrent-Phase5-Deployment-Verifier/1.0'
      },
      signal: controller.signal
    });
  } finally {
    clearTimeout(timeout);
  }
}

const countMatches = (source, pattern) => [...String(source).matchAll(pattern)].length;
const canonicalFrom = (html) => String(html).match(/<link\s+rel="canonical"\s+href="([^"]+)"/i)?.[1] || '';
const titleFrom = (html) => String(html).match(/<title>([^<]+)<\/title>/i)?.[1]?.trim() || '';

function inspectBuildFingerprint(data, label, minimumArticles, errors) {
  requireCondition(data?.project === 'website-jtorrent-net', `${label} has the wrong project identifier.`, errors);
  requireCondition(Number(data?.routeCount) >= 43, `${label} reports too few public routes.`, errors);
  requireCondition(Number(data?.articleCount) >= minimumArticles, `${label} reports fewer than ${minimumArticles} articles.`, errors);
  requireCondition(/^v3/.test(String(data?.searchContract || '')), `${label} does not advertise the v3-preferred search contract.`, errors);
  requireCondition(/^[a-f0-9]{12}$/.test(String(data?.assetHash || '')), `${label} has an invalid asset hash.`, errors);
  return data;
}

function requireCondition(condition, message, errors) {
  if (!condition) errors.push(message);
}

function requireStatus(response, expected, label, errors) {
  const valid = Array.isArray(expected) ? expected.includes(response.status) : response.status === expected;
  if (!valid) errors.push(`${label} returned HTTP ${response.status}; expected ${Array.isArray(expected) ? expected.join('/') : expected}.`);
}

async function readText(response, label, errors) {
  try {
    return await response.text();
  } catch (error) {
    errors.push(`${label} could not be read: ${error.message}`);
    return '';
  }
}

async function readJson(response, label, errors) {
  try {
    return await response.json();
  } catch (error) {
    errors.push(`${label} is not valid JSON: ${error.message}`);
    return null;
  }
}

export function inspectHomepage(html, expectedBaseUrl, errors = []) {
  const expectedCanonical = `${trimSlash(expectedBaseUrl)}/`;
  requireCondition(titleFrom(html) === 'Jivaro Torrent Search', `Homepage title is not the production search title: ${titleFrom(html) || '(missing)'}.`, errors);
  requireCondition(canonicalFrom(html) === expectedCanonical, `Homepage canonical is incorrect; expected ${expectedCanonical}.`, errors);
  requireCondition(html.includes('data-search-console'), 'Homepage search console is missing.', errors);
  requireCondition(countMatches(html, /role="tab"/g) === 2, 'Homepage must expose exactly two search-mode tabs.', errors);
  requireCondition(html.includes('Search Sites') && html.includes('Aggregated Results'), 'Homepage search modes are incomplete.', errors);
  requireCondition(html.includes('Best VPNs') && html.includes('Best Proxies'), 'Homepage VPN/proxy links are missing.', errors);
  requireCondition(html.includes('data-source-grid') && html.includes('data-aggregate-filters'), 'Homepage source and aggregated-result filters are incomplete.', errors);
  requireCondition(!html.includes('class="hub-grid"') && !html.includes('Featured guides'), 'Homepage contains editorial landing-page modules that do not belong there.', errors);
  for (const route of CONTENT_HUBS) requireCondition(html.includes(`href="${route}"`), `Homepage navigation is missing ${route}.`, errors);
  for (const marker of FORBIDDEN_STALE_MARKERS) requireCondition(!html.includes(marker), `Homepage still contains retired/stale marker: ${marker}.`, errors);
  return errors;
}

function inspectSecurityHeaders(response, label, errors) {
  const csp = response.headers.get('content-security-policy') || '';
  const xContent = response.headers.get('x-content-type-options') || '';
  const frame = response.headers.get('x-frame-options') || '';
  requireCondition(csp.includes("default-src 'self'") && csp.includes('https://officialjivaro.github.io'), `${label} is missing the expected Content-Security-Policy.`, errors);
  requireCondition(xContent.toLowerCase() === 'nosniff', `${label} is missing X-Content-Type-Options: nosniff.`, errors);
  requireCondition(frame.toUpperCase() === 'DENY', `${label} is missing X-Frame-Options: DENY.`, errors);
}

export async function verifyDeployment(input = {}) {
  const options = { ...DEFAULTS, ...input };
  const errors = [];
  const checks = [];
  const base = trimSlash(options.baseUrl);

  async function get(pathname, label, extra = {}) {
    const url = joinUrl(base, pathname);
    try {
      const response = await request(url, { timeoutMs: options.timeoutMs, ...extra });
      checks.push({ label, url, status: response.status });
      return response;
    } catch (error) {
      errors.push(`${label} request failed: ${error.name === 'AbortError' ? 'timed out' : error.message}`);
      return null;
    }
  }

  const homeResponse = await get('/', 'Production homepage', { accept: 'text/html' });
  if (homeResponse) {
    requireStatus(homeResponse, 200, 'Production homepage', errors);
    inspectSecurityHeaders(homeResponse, 'Production homepage', errors);
    const html = await readText(homeResponse, 'Production homepage', errors);
    inspectHomepage(html, base, errors);
  }

  let productionFingerprint = null;
  const fingerprintResponse = await get('/.well-known/jtorrent-build.json', 'Production build fingerprint', { accept: 'application/json' });
  if (fingerprintResponse) {
    requireStatus(fingerprintResponse, 200, 'Production build fingerprint', errors);
    productionFingerprint = await readJson(fingerprintResponse, 'Production build fingerprint', errors);
    inspectBuildFingerprint(productionFingerprint, 'Production build fingerprint', options.minimumArticles, errors);
  }

  const articlePath = '/torrenting/magnet-link-not-opening/';
  const articleResponse = await get(articlePath, 'Canonical article', { accept: 'text/html' });
  if (articleResponse) {
    requireStatus(articleResponse, 200, 'Canonical article', errors);
    const html = await readText(articleResponse, 'Canonical article', errors);
    requireCondition(canonicalFrom(html) === joinUrl(base, articlePath), 'Canonical article has the wrong canonical URL.', errors);
    requireCondition(countMatches(html, /<h1\b/g) === 1, 'Canonical article does not contain exactly one H1.', errors);
    requireCondition(html.includes('Magnet Link Not Opening?') && html.includes('article-toc'), 'Canonical article content or TOC is missing.', errors);
    requireCondition(html.includes('"@type":"Article"') && html.includes('"@type":"BreadcrumbList"'), 'Canonical article schema is incomplete.', errors);
  }

  const hubResponse = await get('/torrenting/', 'Torrenting hub', { accept: 'text/html' });
  if (hubResponse) {
    requireStatus(hubResponse, 200, 'Torrenting hub', errors);
    const html = await readText(hubResponse, 'Torrenting hub', errors);
    requireCondition(canonicalFrom(html) === joinUrl(base, '/torrenting/'), 'Torrenting hub canonical is incorrect.', errors);
    requireCondition(html.includes('"@type":"ItemList"') && html.includes(articlePath), 'Torrenting hub ItemList or migrated article card is missing.', errors);
  }

  const blogResponse = await get('/blog/page/2/', 'Blog page 2', { accept: 'text/html' });
  if (blogResponse) {
    requireStatus(blogResponse, 200, 'Blog page 2', errors);
    const html = await readText(blogResponse, 'Blog page 2', errors);
    requireCondition(canonicalFrom(html) === joinUrl(base, '/blog/page/2/'), 'Blog page 2 canonical is incorrect.', errors);
    requireCondition(html.includes('rel="prev"') && html.includes('rel="next"'), 'Blog page 2 is missing previous/next links.', errors);
  }

  const rssResponse = await get('/rss.xml', 'Global RSS', { accept: 'application/rss+xml, application/xml, text/xml' });
  if (rssResponse) {
    requireStatus(rssResponse, 200, 'Global RSS', errors);
    const xml = await readText(rssResponse, 'Global RSS', errors);
    requireCondition(countMatches(xml, /<item>/g) >= options.minimumArticles, `Global RSS contains fewer than ${options.minimumArticles} articles.`, errors);
    requireCondition(xml.includes('<atom:link') && xml.includes('rel="self"'), 'Global RSS self link is missing.', errors);
  }

  const sitemapIndexResponse = await get('/sitemap-index.xml', 'Sitemap index', { accept: 'application/xml, text/xml' });
  if (sitemapIndexResponse) {
    requireStatus(sitemapIndexResponse, 200, 'Sitemap index', errors);
    const xml = await readText(sitemapIndexResponse, 'Sitemap index', errors);
    requireCondition(xml.includes(joinUrl(base, '/sitemap.xml')), 'Sitemap index does not reference sitemap.xml.', errors);
  }

  const sitemapResponse = await get('/sitemap.xml', 'Sitemap', { accept: 'application/xml, text/xml' });
  if (sitemapResponse) {
    requireStatus(sitemapResponse, 200, 'Sitemap', errors);
    const xml = await readText(sitemapResponse, 'Sitemap', errors);
    requireCondition(xml.includes(joinUrl(base, articlePath)), 'Sitemap is missing the canonical migrated article.', errors);
    requireCondition(xml.includes(joinUrl(base, '/blog/page/2/')), 'Sitemap is missing Blog pagination.', errors);
  }

  const robotsResponse = await get('/robots.txt', 'Robots file', { accept: 'text/plain' });
  if (robotsResponse) {
    requireStatus(robotsResponse, 200, 'Robots file', errors);
    const text = await readText(robotsResponse, 'Robots file', errors);
    requireCondition(text.includes(`Sitemap: ${joinUrl(base, '/sitemap-index.xml')}`), 'robots.txt does not reference the sitemap index.', errors);
    requireCondition(text.includes('Disallow: /go/') && text.includes('Disallow: /api/'), 'robots.txt does not exclude dynamic redirect/search routes.', errors);
  }

  const searchIndexResponse = await get('/search-index.json', 'Editorial search index', { accept: 'application/json' });
  if (searchIndexResponse) {
    requireStatus(searchIndexResponse, 200, 'Editorial search index', errors);
    const data = await readJson(searchIndexResponse, 'Editorial search index', errors);
    requireCondition(Array.isArray(data) && data.length >= options.minimumArticles, `Editorial search index contains fewer than ${options.minimumArticles} records.`, errors);
    requireCondition(Array.isArray(data) && data.some((item) => item?.path === articlePath), 'Editorial search index is missing the migrated article.', errors);
  }

  const manifestResponse = await get('/editorial/content-manifest.json', 'Editorial content manifest', { accept: 'application/json' });
  if (manifestResponse) {
    requireStatus(manifestResponse, 200, 'Editorial content manifest', errors);
    const data = await readJson(manifestResponse, 'Editorial content manifest', errors);
    requireCondition(Number(data?.articleCount) >= options.minimumArticles, `Editorial manifest articleCount is below ${options.minimumArticles}.`, errors);
    requireCondition(Array.isArray(data?.articles) && data.articles.some((item) => item?.path === articlePath), 'Editorial manifest is missing the migrated article.', errors);
  }

  if (!options.skipFunctions) {
    const goResponse = await get('/go/brightdata', 'Affiliate redirect', { redirect: 'manual' });
    if (goResponse) {
      requireStatus(goResponse, 302, 'Affiliate redirect', errors);
      const location = goResponse.headers.get('location') || '';
      requireCondition(location.startsWith('https://get.brightdata.com/'), 'Affiliate redirect target is incorrect.', errors);
      requireCondition((goResponse.headers.get('cache-control') || '').includes('no-store'), 'Affiliate redirect is missing no-store.', errors);
      requireCondition((goResponse.headers.get('x-robots-tag') || '').includes('noindex'), 'Affiliate redirect is missing noindex.', errors);
    }

    const legacyResponse = await get(LEGACY_REDIRECT.source, 'Known legacy Blog redirect', { redirect: 'manual' });
    if (legacyResponse) {
      requireStatus(legacyResponse, 301, 'Known legacy Blog redirect', errors);
      const location = new URL(legacyResponse.headers.get('location') || '/', base).pathname;
      requireCondition(location === LEGACY_REDIRECT.target, `Known legacy Blog redirect points to ${location}, not ${LEGACY_REDIRECT.target}.`, errors);
    }

    const unknownResponse = await get('/blog/phase-5-definitely-unknown-path', 'Unknown legacy Blog redirect', { redirect: 'manual' });
    if (unknownResponse) {
      requireStatus(unknownResponse, 301, 'Unknown legacy Blog redirect', errors);
      const location = new URL(unknownResponse.headers.get('location') || '/', base).pathname;
      requireCondition(location === '/blog/', `Unknown legacy Blog redirect points to ${location}, not /blog/.`, errors);
    }
  }

  if (!options.skipBackend) {
    const backendStatusUrl = joinUrl(options.backendUrl, '/data/status.json');
    try {
      const response = await request(backendStatusUrl, { timeoutMs: options.timeoutMs, accept: 'application/json' });
      checks.push({ label: 'Aggregated-search backend status', url: backendStatusUrl, status: response.status });
      requireStatus(response, 200, 'Aggregated-search backend status', errors);
      const status = await readJson(response, 'Aggregated-search backend status', errors);
      requireCondition(status?.search_contract === 'v3', 'Aggregated-search backend does not advertise search contract v3.', errors);
      requireCondition(Number(status?.item_count) >= 1, 'Aggregated-search backend status has no indexed records.', errors);
      requireCondition(status?.search_manifest === 'data/v3/manifest.json', 'Aggregated-search backend status points to the wrong v3 manifest.', errors);

      const manifestUrl = joinUrl(options.backendUrl, `/${status?.search_manifest || 'data/v3/manifest.json'}`);
      const manifestResponse = await request(manifestUrl, { timeoutMs: options.timeoutMs, accept: 'application/json' });
      checks.push({ label: 'Aggregated-search v3 manifest', url: manifestUrl, status: manifestResponse.status });
      requireStatus(manifestResponse, 200, 'Aggregated-search v3 manifest', errors);
      const index = await readJson(manifestResponse, 'Aggregated-search v3 manifest', errors);
      requireCondition(index?.version === 3, 'Aggregated-search manifest has the wrong version.', errors);
      requireCondition(JSON.stringify(index?.posting_format) === JSON.stringify(['doc_id', 'category_index', 'source_index', 'static_score']), 'Aggregated-search v3 posting format is invalid.', errors);
      for (const term of ['public', 'domain', 'creative', 'commons', 'archive', 'official', 'torrent', 'download', 'metadata']) {
        requireCondition(!new Set(index?.stopwords || []).has(term), `Aggregated-search v3 incorrectly removes searchable term: ${term}.`, errors);
      }
    } catch (error) {
      errors.push(`Aggregated-search backend request failed: ${error.name === 'AbortError' ? 'timed out' : error.message}`);
    }
  }

  if (!options.skipPreview && options.previewUrl) {
    const previewUrl = joinUrl(options.previewUrl, '/');
    try {
      const response = await request(previewUrl, { timeoutMs: options.timeoutMs, accept: 'text/html' });
      checks.push({ label: 'Cloudflare Pages project URL', url: previewUrl, status: response.status });
      requireStatus(response, 200, 'Cloudflare Pages project URL', errors);
      const html = await readText(response, 'Cloudflare Pages project URL', errors);
      requireCondition(titleFrom(html) === 'Jivaro Torrent Search' && html.includes('data-search-console'), 'Cloudflare Pages project URL is not serving the current search build.', errors);
      for (const marker of FORBIDDEN_STALE_MARKERS) requireCondition(!html.includes(marker), `Cloudflare Pages project URL contains retired marker: ${marker}.`, errors);
      const previewFingerprintUrl = joinUrl(options.previewUrl, '/.well-known/jtorrent-build.json');
      const previewFingerprintResponse = await request(previewFingerprintUrl, { timeoutMs: options.timeoutMs, accept: 'application/json' });
      checks.push({ label: 'Cloudflare Pages build fingerprint', url: previewFingerprintUrl, status: previewFingerprintResponse.status });
      requireStatus(previewFingerprintResponse, 200, 'Cloudflare Pages build fingerprint', errors);
      const previewFingerprint = await readJson(previewFingerprintResponse, 'Cloudflare Pages build fingerprint', errors);
      inspectBuildFingerprint(previewFingerprint, 'Cloudflare Pages build fingerprint', options.minimumArticles, errors);
      if (productionFingerprint && previewFingerprint) {
        for (const key of ['assetHash', 'routeCount', 'articleCount', 'searchContract']) {
          requireCondition(previewFingerprint[key] === productionFingerprint[key], `Production and Pages fingerprints differ for ${key}.`, errors);
        }
      }
    } catch (error) {
      errors.push(`Cloudflare Pages project URL request failed: ${error.name === 'AbortError' ? 'timed out' : error.message}`);
    }
  }

  if (!options.skipApex && options.apexUrl) {
    const apexUrl = joinUrl(options.apexUrl, '/');
    try {
      const response = await request(apexUrl, { timeoutMs: options.timeoutMs, redirect: 'manual', accept: 'text/html' });
      checks.push({ label: 'Apex-domain redirect', url: apexUrl, status: response.status });
      requireStatus(response, [301, 302, 307, 308], 'Apex-domain redirect', errors);
      const location = response.headers.get('location') || '';
      requireCondition(location.startsWith(`${base}/`) || location === `${base}/`, `Apex domain redirects to ${location || '(missing)'}, not ${base}/.`, errors);
    } catch (error) {
      errors.push(`Apex-domain request failed: ${error.name === 'AbortError' ? 'timed out' : error.message}`);
    }
  }

  return { ok: errors.length === 0, errors, checks, options: { ...options, baseUrl: base } };
}

async function main() {
  let options;
  try {
    options = parseArguments(process.argv.slice(2));
  } catch (error) {
    console.error(error.message);
    console.error(helpText());
    process.exit(2);
  }
  if (options.help) {
    console.log(helpText());
    return;
  }

  const result = await verifyDeployment(options);
  if (!result.ok) {
    console.error(result.errors.map((error) => `ERROR: ${error}`).join('\n'));
    console.error(`\nLive deployment verification failed with ${result.errors.length} error${result.errors.length === 1 ? '' : 's'}.`);
    process.exit(1);
  }
  console.log(`Live deployment verified: ${result.checks.length} HTTP checks passed for ${result.options.baseUrl}.`);
}

const invokedDirectly = process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href;
if (invokedDirectly) await main();
