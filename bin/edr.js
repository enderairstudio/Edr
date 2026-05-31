#!/usr/bin/env node

const { spawnSync } = require("node:child_process");
const path = require("node:path");

const cli = path.resolve(__dirname, "..", "command.py");
const candidates = process.platform === "win32" ? ["py -3", "python", "python3"] : ["python3", "python"];

let lastResult = null;

for (const candidate of candidates) {
  const [command, ...prefixArgs] = candidate.split(" ");
  const result = spawnSync(command, [...prefixArgs, cli, ...process.argv.slice(2)], {
    stdio: "inherit",
    shell: false,
  });

  if (result.error && result.error.code === "ENOENT") {
    lastResult = result;
    continue;
  }

  if (result.error) {
    console.error(`edr: failed to launch ${candidate}: ${result.error.message}`);
    process.exit(1);
  }

  process.exit(result.status ?? 0);
}

console.error("edr: Python 3.11+ is required. Install Python, then run this command again.");
if (lastResult?.error?.message) {
  console.error(`edr: last error: ${lastResult.error.message}`);
}
process.exit(1);
