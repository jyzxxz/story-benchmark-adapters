import type { CharacterVoice, TtsConfig } from "@infiplot/types";
import catalogData from "./stepfun-voices.json";

// Preset voice record. The 32 presets live in stepfun-voices.json (the single
// source of truth — shared with the CharacterDesigner prompt, /api/tts-provider
// validity check, and the offline enrich script). gender/age are discriminant
// unions so detectGender / detectAge scoring stays type-safe.
export type PresetVoice = {
  id: string;
  gender: "male" | "female";
  age: "teen" | "young" | "middle";
  /** Keywords (中文 or English) that, when present in the LLM's voice
   *  description, boost this preset's score. Drawn from StepFun's published
   *  voice name + recommended scenario. */
  tones: string[];
  /** 中文人设短语，供 LLM（设定师 prompt / enrich 脚本）在选音色时理解每个
   *  预设适合的角色类型。打分函数（pickStepfunVoiceId）仍只用 tones。 */
  desc: string;
};

// JSON literals widen gender/age to `string`; cast back to the discriminant
// unions. The catalog is a build-time-checked asset (touched rarely), and
// pickStepfunVoiceId / isValidStepfunVoiceId tolerate anything we ship, so a
// wrong entry surfaces as a bad voice pick rather than a crash.
const PRESET_VOICES = catalogData as unknown as PresetVoice[];

// StepFun TTS uses an OpenAI-compatible /v1/audio/speech endpoint with PRESET
// voice IDs only — there is no "design a new voice from text description"
// equivalent to Xiaomi MiMo's voicedesign. We therefore translate the LLM's
// Chinese voiceDescription into a preset voice ID by keyword matching
// (gender + age + tone), with a deterministic hash-based spread across the
// top-N candidates so multiple similar characters don't collapse onto the
// same voice. Provision is a pure function — no network call needed.

/** Provider detection — shared by /api/tts-provider, orchestrator fallback,
 *  and the client (via the route). StepFun is inferred from a *.stepfun.com
 *  host in the base URL, matching lib/tts-client/index.ts. Exported so every
 *  caller agrees on the same rule. */
export function isStepfun(cfg: TtsConfig): boolean {
  return /(^|[./])stepfun\.com\b/i.test(cfg.baseUrl);
}

function arrayBufferToBase64(buffer: ArrayBuffer): string {
  const bytes = new Uint8Array(buffer);
  let binary = "";
  const len = bytes.byteLength;
  for (let i = 0; i < len; i++) {
    binary += String.fromCharCode(bytes[i]!);
  }
  return btoa(binary);
}

const OUTPUT_FORMAT = "mp3";
const OUTPUT_MIME = "audio/mpeg";

// Full catalog from StepFun's docs (32 presets across step-tts-mini /
// step-tts-2 / stepaudio-2.5-tts). The JSON is the single source of truth —
// shared by the scorer here, the CharacterDesigner prompt (via
// formatStepfunCatalogForPrompt), the /api/tts-provider route's validity
// check, and the offline enrich script. Adding more later is safe — the
// scorer degrades gracefully when an unknown id is picked.
// (catalogData is cast to PresetVoice[] at the import above; kept as
// PRESET_VOICES so existing references stay unchanged.)

/** All valid preset voice ids — for validation by the CharacterDesigner
 *  (discard an out-of-catalog LLM pick) and the enrich script. */
export const STEPFUN_PRESET_VOICE_IDS: string[] = PRESET_VOICES.map(
  (v) => v.id,
);

const STEPFUN_ID_SET = new Set(STEPFUN_PRESET_VOICE_IDS);

/** True iff `id` is one of the 32 catalog presets. Used to drop LLM-hallucinated
 *  ids before they reach StepFun (which would otherwise 4xx on synth). */
export function isValidStepfunVoiceId(id: string | null | undefined): boolean {
  return !!id && STEPFUN_ID_SET.has(id);
}

/** Render the catalog as a 中文 prompt-friendly list, one line per preset,
 *  so the CharacterDesigner and the enrich script can ask the LLM to pick a
 *  matching voice id. Each line: `id — desc（gender/age）`. */
export function formatStepfunCatalogForPrompt(): string {
  return PRESET_VOICES.map(
    (v) => `- ${v.id}：${v.desc}（${v.gender}/${v.age}）`,
  ).join("\n");
}

// Cheap deterministic 32-bit hash — used only to spread similar descriptions
// across the top-N candidate voices so two "温柔女声" characters don't collide.
function hashStr(s: string): number {
  let h = 5381;
  for (let i = 0; i < s.length; i++) h = ((h << 5) + h + s.charCodeAt(i)) | 0;
  return Math.abs(h);
}

function detectGender(desc: string): "male" | "female" {
  if (/女性|女声|少女|姐姐|妹妹|熟女|御姐|阿姨|奶奶|女孩|姑娘|大妈|女子|女生|女士|小姐/.test(desc)) {
    return "female";
  }
  if (/男性|男声|少年|青年|大叔|哥哥|弟弟|男人|男孩|大爷|爷爷|男子|男生|先生|公子|师傅/.test(desc)) {
    return "male";
  }
  // Weak signals: single-char pronouns checked last to avoid false positives
  // on compound words like "其他" (other) or "她们" (they-fem).
  if (/她/.test(desc)) return "female";
  if (/他/.test(desc)) return "male";
  return "female";
}

function detectAge(desc: string): "teen" | "young" | "middle" {
  if (/中年|熟女|大叔|大妈|阿姨|奶奶|爷爷|老师|师傅|御姐|经理|总监|教授|博士|总裁|长辈|父亲|母亲|爸爸|妈妈/.test(desc)) {
    return "middle";
  }
  if (/少女|少年|学生|高中|初中|妹妹|弟弟|小学|童年|稚嫩|十几岁|十六|十七|十八|未成年/.test(desc)) {
    return "teen";
  }
  return "young";
}

/** Map an LLM-written 中文 voice description to a StepFun preset voice ID.
 *  Pure function — exported for tests and for the synthesis-time sanity log.
 */
export function pickStepfunVoiceId(description: string, salt = ""): string {
  const desc = description.toLowerCase();
  const gender = detectGender(desc);
  const age = detectAge(desc);

  const scored = PRESET_VOICES
    .filter((v) => v.gender === gender)
    .map((v) => {
      let score = 0;
      if (v.age === age) score += 4;
      for (const tone of v.tones) {
        if (desc.includes(tone.toLowerCase())) score += 2;
      }
      return { v, score };
    })
    .sort((a, b) => b.score - a.score);

  // Catalog can't be filtered to zero; this guards against a future edit
  // that prunes the table too aggressively.
  if (scored.length === 0) return PRESET_VOICES[0]!.id;

  // Pick from the top 3 (or fewer) deterministically by hashing the
  // description + an optional salt (charName) so two characters that share
  // archetype keywords don't collapse onto the identical preset. Hash the
  // lowercased desc so case differences in the same description don't pick
  // different presets (scoring above is already case-insensitive).
  const top = scored.slice(0, Math.min(3, scored.length));
  const idx = hashStr(desc + "|" + salt.toLowerCase()) % top.length;
  return top[idx]!.v.id;
}

// Provision is synchronous / no network — StepFun has no voicedesign equivalent.
// We mirror xiaomiProvision's async signature so the router stays uniform.
// The optional `salt` (character name) spreads two characters that share
// archetype keywords across the top-N candidate presets.
//
// `opts.stepfunVoiceId` — when the CharacterDesigner already picked a preset
// (it sees the same catalog via formatStepfunCatalogForPrompt), honor it if
// valid; otherwise fall back to the keyword scorer. This keeps StepFun
// provisioning a pure function (zero network cost) while lifting voice-id
// selection quality to LLM-grade on the live path.
export type StepfunProvisionOptions = {
  /** LLM-selected preset id from the CharacterDesigner; validated against the
   *  catalog and ignored when out of range (hallucination guard). */
  stepfunVoiceId?: string;
};

export async function stepfunProvision(
  cfg: TtsConfig,
  description: string,
  salt?: string,
  opts?: StepfunProvisionOptions,
): Promise<CharacterVoice> {
  const voiceId =
    opts && isValidStepfunVoiceId(opts.stepfunVoiceId)
      ? opts.stepfunVoiceId!
      : pickStepfunVoiceId(description, salt);
  return {
    provider: "stepfun",
    voiceId,
    model: cfg.speechModel,
    mimeType: OUTPUT_MIME,
  };
}

export async function stepfunSynthesize(
  cfg: TtsConfig,
  voice: CharacterVoice,
  text: string,
  _delivery?: string,
  signal?: AbortSignal,
): Promise<{ audioBase64: string; mimeType: string }> {
  if (voice.provider !== "stepfun") {
    throw new Error(
      `stepfunSynthesize received non-stepfun voice (provider="${voice.provider}")`,
    );
  }

  // Strip trailing slash so /v1 + /audio/speech doesn't double up.
  const base = cfg.baseUrl.replace(/\/$/, "");
  const url = `${base}/audio/speech`;

  const body = {
    model: voice.model || cfg.speechModel,
    input: text,
    voice: voice.voiceId,
    response_format: OUTPUT_FORMAT,
  };

  const res = await fetch(url, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${cfg.apiKey}`,
    },
    body: JSON.stringify(body),
    signal,
  });

  if (!res.ok) {
    const txt = await res.text();
    throw new Error(`StepFun TTS ${res.status}: ${txt.slice(0, 300)}`);
  }

  const ab = await res.arrayBuffer();
  const audioBase64 = arrayBufferToBase64(ab);
  return { audioBase64, mimeType: OUTPUT_MIME };
}
