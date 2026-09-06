#!/usr/bin/env node
/**
 * Enrich the prebaked homepage first-act JSONs with a StepFun preset voice
 * id for each character, so that when the server is configured for StepFun
 * TTS, players entering via a homepage card (?card=...) still get a voice
 * instead of silence.
 *
 * The prebaked JSONs ship with a Xiaomi voice (baked at build time). When the
 * server runs StepFun, that Xiaomi voice is useless on the synth path AND
 * costs ~220KB per beat-audio request in wasted Fast Origin Transfer. So we
 * additionally write `characters[i].stepfunVoiceId`, picked by ONE LLM call
 * per character using the same 32-preset catalog the CharacterDesigner uses.
 *
 * Idempotent: skips any character that already has a stepfunVoiceId.
 * Pass --force to re-pick every character. --only=f0,f1 to filter.
 * --portrait targets firstact-portrait/ instead of firstact/.
 *
 * Reads .env.local for TEXT_BASE_URL / TEXT_API_KEY / TEXT_MODEL (same env
 * convention as scripts/generate-presets.mjs). Does NOT touch voice / imageUrl
 * / scene / storyState — only appends stepfunVoiceId.
 *
 * Usage:
 *   node scripts/enrich-firstacts-stepfun.mjs
 *   node scripts/enrich-firstacts-stepfun.mjs --force
 *   node scripts/enrich-firstacts-stepfun.mjs --only=f0,f1 --portrait
 */

import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import { existsSync, readFileSync, writeFileSync, readdirSync, statSync } from "node:fs";

const __dirname = dirname(fileURLToPath(import.meta.url));
const WEB_ROOT = resolve(__dirname, "..");
const ENV_FILE = resolve(WEB_ROOT, ".env.local");
const OUT_DIR_NAME = process.argv.includes("--portrait")
  ? "firstact-portrait"
  : "firstact";
const OUT_DIR = resolve(WEB_ROOT, "public", "home", OUT_DIR_NAME);
const CATALOG_FILE = resolve(WEB_ROOT, "lib", "tts-client", "stepfun-voices.json");

const FORCE = process.argv.includes("--force");
const ONLY_ARG = process.argv.find((a) => a.startsWith("--only="));
const ONLY = ONLY_ARG ? ONLY_ARG.split("=")[1].split(",") : null;
const CONCURRENCY = 4;
const MAX_ATTEMPTS = 4;

// ── env ───────────────────────────────────────────────────────────────
function loadEnv(path) {
  if (!existsSync(path)) return {};
  const txt = readFileSync(path, "utf8");
  const env = {};
  for (const raw of txt.split(/\r?\n/)) {
    const line = raw.trim();
    if (!line || line.startsWith("#")) continue;
    const eq = line.indexOf("=");
    if (eq < 0) continue;
    const k = line.slice(0, eq).trim();
    let v = line.slice(eq + 1).trim();
    if ((v.startsWith('"') && v.endsWith('"')) || (v.startsWith("'") && v.endsWith("'"))) {
      v = v.slice(1, -1);
    }
    env[k] = v;
  }
  return env;
}

const env = loadEnv(ENV_FILE);
const BASE_URL = env.TEXT_BASE_URL;
const API_KEY = env.TEXT_API_KEY;
const MODEL = env.TEXT_MODEL;
if (!BASE_URL || !API_KEY || !MODEL) {
  console.error(`Missing TEXT_BASE_URL / TEXT_API_KEY / TEXT_MODEL in ${ENV_FILE}`);
  process.exit(2);
}

// ── catalog ───────────────────────────────────────────────────────────
const CATALOG = JSON.parse(readFileSync(CATALOG_FILE, "utf8"));
const VALID_IDS = new Set(CATALOG.map((v) => v.id));
const CATALOG_TEXT = CATALOG.map(
  (v) => `- ${v.id}：${v.desc}（${v.gender}/${v.age}）`,
).join("\n");

// ── LLM pick ──────────────────────────────────────────────────────────
const SYSTEM_PROMPT = `你是一个 TTS 音色匹配助手。给你一个角色的名字、世界观背景、以及中文音色设定描述，你要从下面的 StepFun 预设音色清单里挑选「最贴合该角色」的一个预设 id。

挑选原则：
- 性别必须一致（男声只能选 male 行，女声只能选 female 行）。
- 年龄段尽量一致；拿不准时优先气质匹配。
- id 必须**原样复制**清单里的某个值（拼写、大小写、连字符都不能变），不允许编造清单外的 id。

StepFun 预设音色清单：
${CATALOG_TEXT}

只输出一个 JSON 对象，不要输出任何其它文本：
{ "stepfunVoiceId": "清单内某个 id" }`;

function buildUser(charName, voiceDescription, worldSetting) {
  return [
    `角色名：${charName}`,
    `世界观：${worldSetting}`,
    `音色设定描述：${voiceDescription}`,
    "",
    "请挑选最贴合该角色的 StepFun 预设 id，严格以 JSON 返回。",
  ].join("\n");
}

// Cheap permissive JSON extraction — the model sometimes wraps in ```json
// fences or adds stray prose. Mirrors parseJsonLoose's first-cut logic
// without pulling in the TS engine code.
function extractJson(raw) {
  const trimmed = raw.trim();
  // Strip ```json ... ``` fences if present.
  const fence = trimmed.match(/```(?:json)?\s*([\s\S]*?)```/i);
  const candidate = (fence ? fence[1] : trimmed).trim();
  // Try direct parse first.
  try {
    return JSON.parse(candidate);
  } catch {
    // Fall back to the first {...} slice.
    const start = candidate.indexOf("{");
    const end = candidate.lastIndexOf("}");
    if (start >= 0 && end > start) {
      try {
        return JSON.parse(candidate.slice(start, end + 1));
      } catch {
        // give up below
      }
    }
  }
  return null;
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function pickVoiceId(charName, voiceDescription, worldSetting) {
  const url = BASE_URL.replace(/\/$/, "") + "/chat/completions";
  let lastErr = "";
  for (let attempt = 1; attempt <= MAX_ATTEMPTS; attempt++) {
    try {
      const res = await fetch(url, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${API_KEY}`,
        },
        body: JSON.stringify({
          model: MODEL,
          messages: [
            { role: "system", content: SYSTEM_PROMPT },
            { role: "user", content: buildUser(charName, voiceDescription, worldSetting) },
          ],
          temperature: 0.3,
        }),
      });
      if (!res.ok) {
        lastErr = `HTTP ${res.status}: ${(await res.text()).slice(0, 200)}`;
      } else {
        const data = await res.json();
        const raw = data?.choices?.[0]?.message?.content ?? "";
        const parsed = extractJson(raw);
        const id = parsed?.stepfunVoiceId;
        if (typeof id === "string" && VALID_IDS.has(id)) {
          return id;
        }
        lastErr = `invalid id in response: ${JSON.stringify(id ?? parsed).slice(0, 120)}`;
      }
    } catch (e) {
      lastErr = e instanceof Error ? e.message : String(e);
    }
    if (attempt < MAX_ATTEMPTS) {
      const delay = 2 ** attempt * 2000;
      console.warn(`    [retry ${attempt}/${MAX_ATTEMPTS}] ${lastErr} — waiting ${delay}ms`);
      await sleep(delay);
    }
  }
  throw new Error(lastErr || "unknown error");
}

// ── main ──────────────────────────────────────────────────────────────
function listCards() {
  const all = readdirSync(OUT_DIR)
    .filter((f) => f.endsWith(".json"))
    .map((f) => f.replace(/\.json$/, ""));
  if (ONLY) {
    const keep = new Set(ONLY);
    return all.filter((f) => keep.has(f));
  }
  return all.sort();
}

async function enrichOne(cardName) {
  const file = resolve(OUT_DIR, `${cardName}.json`);
  const data = JSON.parse(readFileSync(file, "utf8"));
  const worldSetting = data.worldSetting ?? "";
  const characters = Array.isArray(data.characters) ? data.characters : [];
  let changed = false;
  for (const c of characters) {
    if (!c.voiceDescription) {
      console.warn(`    [skip] ${c.name}: no voiceDescription`);
      continue;
    }
    if (!FORCE && typeof c.stepfunVoiceId === "string" && c.stepfunVoiceId) {
      continue; // already enriched
    }
    const id = await pickVoiceId(c.name, c.voiceDescription, worldSetting);
    c.stepfunVoiceId = id;
    changed = true;
    console.log(`    ${c.name} → ${id}`);
  }
  if (changed) {
    writeFileSync(file, JSON.stringify(data));
  }
  return changed;
}

async function worker(queue, counters) {
  while (queue.length > 0) {
    const name = queue.shift();
    if (!name) return;
    try {
      const changed = await enrichOne(name);
      if (changed) {
        counters.enriched++;
        console.log(`[${counters.done}/${counters.total}] ${name} enriched`);
      } else {
        counters.skipped++;
        console.log(`[${counters.done}/${counters.total}] ${name} skip (no change)`);
      }
    } catch (e) {
      counters.failed++;
      const msg = e instanceof Error ? e.message : String(e);
      console.error(`[${counters.done}/${counters.total}] ${name} FAIL: ${msg}`);
    }
    counters.done++;
    await sleep(1000); // be nice to rate limits between cards
  }
}

async function main() {
  if (!existsSync(OUT_DIR)) {
    console.error(`Output dir not found: ${OUT_DIR}`);
    process.exit(2);
  }
  const cards = listCards();
  if (cards.length === 0) {
    console.log(`No cards found in ${OUT_DIR}`);
    return;
  }
  console.log(
    `[enrich] ${cards.length} cards in ${OUT_DIR} | force=${FORCE} | only=${ONLY ? ONLY.join(",") : "all"} | concurrency=${CONCURRENCY}`,
  );

  const counters = { done: 0, total: cards.length, enriched: 0, skipped: 0, failed: 0 };
  const queue = [...cards];
  const t0 = Date.now();
  await Promise.all(
    Array.from({ length: CONCURRENCY }, () => worker(queue, counters)),
  );
  console.log(
    `\n[enrich] done in ${Math.round((Date.now() - t0) / 1000)}s — enriched ${counters.enriched}, skipped ${counters.skipped}, failed ${counters.failed}`,
  );
  process.exit(counters.failed ? 1 : 0);
}

main();
