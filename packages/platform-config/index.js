const { existsSync, readFileSync } = require('fs');
const { resolve } = require('path');

const contractCache = new Map();

function contractPath(name) {
  const candidates = [
    resolve(__dirname, '../../contracts/generation', name),
    resolve(__dirname, '../../../contracts/generation', name),
    resolve(__dirname, './contracts/generation', name),
  ];
  const matched = candidates.find((candidate) => existsSync(candidate));
  if (!matched) {
    throw new Error(`Unable to locate generation contract ${name}. Checked: ${candidates.join(', ')}`);
  }
  return matched;
}

function loadContractJson(name) {
  if (!contractCache.has(name)) {
    contractCache.set(
      name,
      JSON.parse(readFileSync(contractPath(name), 'utf8')),
    );
  }
  return contractCache.get(name);
}

function loadRuntimeProfileContract() {
  return loadContractJson('runtime-profiles.json');
}

function loadTimeoutContract() {
  return loadContractJson('timeout-keys.json');
}

module.exports = {
  loadContractJson,
  loadRuntimeProfileContract,
  loadTimeoutContract,
};
