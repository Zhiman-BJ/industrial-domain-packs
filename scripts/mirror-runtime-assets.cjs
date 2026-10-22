#!/usr/bin/env node
// Stage managed runtime assets for the organization mirror.
//
// Downloads every declared runtimeAsset from its upstream URL exactly once,
// verifies the pinned sha256, and stages it under dist/runtime-assets/ with a
// manifest. The staged files are then uploaded (unchanged) to the
// organization's own storage; harness-pack.json points clients at that mirror
// so end users never download from upstream release CDNs.
//
// Usage:
//   node scripts/mirror-runtime-assets.cjs [stagingDir]
//   INDUSTRIAL_ASSETS_UPSTREAM_TIMEOUT_MS=600000 node scripts/mirror-runtime-assets.cjs
"use strict";
const crypto = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");

const root = path.resolve(__dirname, "..");
const staging = path.resolve(process.argv[2] || path.join(root, "dist", "runtime-assets"));
const timeoutMs = Number(process.env.INDUSTRIAL_ASSETS_UPSTREAM_TIMEOUT_MS || 900000);

async function download(url, target, expectedSize) {
  for (let attempt = 1; attempt <= 3; attempt += 1) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
      const response = await fetch(url, { signal: controller.signal, redirect: "follow" });
      if (!response.ok || !response.body) throw Error(`HTTP ${response.status}`);
      const digest = crypto.createHash("sha256");
      const stream = fs.createWriteStream(target, { mode: 0o644 });
      let bytes = 0;
      const writer = new Promise((resolve, reject) => {
        stream.on("error", reject);
        stream.on("finish", resolve);
      });
      for await (const chunk of response.body) {
        bytes += chunk.length;
        if (bytes > expectedSize) throw Error("Download exceeds declared size");
        digest.update(chunk);
        if (!stream.write(chunk)) await new Promise((resolve) => stream.once("drain", resolve));
      }
      stream.end();
      await writer;
      return { sha256: digest.digest("hex"), bytes };
    } catch (error) {
      fs.rmSync(target, { force: true });
      if (attempt === 3) throw error;
      await new Promise((resolve) => setTimeout(resolve, 5000 * attempt));
    } finally {
      clearTimeout(timer);
    }
  }
  throw Error("unreachable");
}

async function main() {
  const manifest = [];
  const packs = fs.readdirSync(path.join(root, "packs"));
  for (const pack of packs) {
    const file = path.join(root, "packs", pack, "harness-pack.json");
    if (!fs.statSync(file, { throwIfNoEntry: false })?.isFile()) continue;
    const declared = JSON.parse(fs.readFileSync(file, "utf8")).runtimeAssets || [];
    for (const asset of declared) {
      const filename = path.basename(new URL(asset.url).pathname);
      const directory = path.join(staging, `${asset.id}-${asset.version}`);
      const target = path.join(directory, filename);
      fs.mkdirSync(directory, { recursive: true });
      const existing = fs.statSync(target, { throwIfNoEntry: false });
      let sha256 = existing
        ? crypto.createHash("sha256").update(fs.readFileSync(target)).digest("hex")
        : null;
      if (sha256 !== asset.sha256) {
        process.stdout.write(`downloading ${asset.id} ${asset.version} (${asset.size} bytes)…\n`);
        const result = await download(asset.url, target, asset.size);
        sha256 = result.sha256;
        if (result.bytes !== asset.size) throw Error(`${asset.id}: size mismatch`);
      }
      if (sha256 !== asset.sha256)
        throw Error(`${asset.id} ${asset.version}: upstream bytes do not match the pinned sha256`);
      manifest.push({
        pack,
        id: asset.id,
        version: asset.version,
        file: `${asset.id}-${asset.version}/${filename}`,
        sha256,
        size: asset.size,
        upstreamUrl: asset.url,
      });
      process.stdout.write(`staged ${asset.id} ${asset.version} sha256=${sha256.slice(0, 16)}…\n`);
    }
  }
  fs.writeFileSync(
    path.join(staging, "manifest.json"),
    JSON.stringify({ schemaVersion: 1, generatedAt: new Date().toISOString(), assets: manifest }, null, 2) + "\n",
  );
  process.stdout.write(`manifest: ${path.join(staging, "manifest.json")} (${manifest.length} assets)\n`);
}

main().catch((error) => {
  console.error(error.message);
  process.exit(1);
});
