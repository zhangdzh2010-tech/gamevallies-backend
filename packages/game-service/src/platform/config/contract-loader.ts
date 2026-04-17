import { existsSync, readFileSync } from 'fs';
import { resolve } from 'path';

function candidateContractPaths(name: string): string[] {
  return [
    resolve(__dirname, '../../../../../contracts/generation', name),
    resolve(__dirname, '../../../contracts/generation', name),
  ];
}

export function loadContractJson<T>(name: string): T {
  for (const path of candidateContractPaths(name)) {
    if (existsSync(path)) {
      return JSON.parse(readFileSync(path, 'utf8')) as T;
    }
  }

  throw new Error(
    `Unable to locate generation contract ${name}. Checked: ${candidateContractPaths(name).join(', ')}`,
  );
}
