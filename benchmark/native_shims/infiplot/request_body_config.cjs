// Runtime-only configuration for the frozen Next loader. No source files,
// request payloads, middleware, or generation code are rewritten.
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');

function installRequestBodyConfig(nativeRequire, root, limitBytes, evidencePath) {
  if (limitBytes !== null && (!Number.isSafeInteger(limitBytes) || limitBytes <= 0)) {
    throw new Error('native_request_body_limit_bytes must be a positive safe integer or null');
  }
  const version = nativeRequire('next/package.json').version;
  // This hook depends on the frozen loader contract; a dependency upgrade
  // requires revalidation, not silent use of an unverified integration.
  if (version !== '16.2.7') throw new Error(`Unverified Next config loader version: ${version}`);
  const configPath = nativeRequire.resolve('next/dist/server/config');
  const originalExports = nativeRequire(configPath);
  const originalLoad = originalExports.default;
  if (typeof originalLoad !== 'function') throw new Error('Native Next config loader unavailable');
  const target = path.resolve(root);
  const configHash = crypto.createHash('sha256')
    .update(fs.readFileSync(path.join(target, 'next.config.ts'))).digest('hex');
  const evidence = {
    version: 1, next_version: version, setting: 'experimental.proxyClientMaxBodySize',
    requested_bytes: limitBytes, mode: limitBytes === null ? 'native_default' : 'external_runtime_override',
    native_config_file_sha256: configHash, native_source_changed: false,
    payload_changed: false, generation_budget_changed: false, loads: [],
  };
  function save() {
    const temporary = `${evidencePath}.${process.pid}.tmp`;
    fs.writeFileSync(temporary, JSON.stringify(evidence, null, 2));
    fs.renameSync(temporary, evidencePath);
  }
  async function loadWithRequestCapacity(phase, directory, ...args) {
    const config = await originalLoad(phase, directory, ...args);
    if (path.resolve(directory) !== target) return config;
    const before = config.experimental?.proxyClientMaxBodySize;
    if (!Number.isSafeInteger(before) || before <= 0) {
      throw new Error('Native Next request-body configuration contract changed');
    }
    const effective = limitBytes === null ? config : {
      ...config, experimental: {...config.experimental, proxyClientMaxBodySize: limitBytes},
    };
    evidence.loads.push({phase, before_bytes: before,
      effective_bytes: effective.experimental.proxyClientMaxBodySize});
    save();
    return effective;
  }
  // NextCustomServer.prepare() in 16.2.7 does not forward options.conf to its
  // routing server. Wrap the actual config loader before requiring Next so
  // both routing and render initialization see the same effective setting.
  const replacement = {...originalExports, default: loadWithRequestCapacity};
  Object.defineProperty(replacement, '__esModule', {value: true});
  nativeRequire.cache[configPath].exports = replacement;
  save();
  return evidence;
}

module.exports = {installRequestBodyConfig};
