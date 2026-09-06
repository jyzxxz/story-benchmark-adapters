import type { CharacterVoice, TtsConfig, TtsProvider } from "@infiplot/types";
import {
  formatStepfunCatalogForPrompt,
  isStepfun,
  isValidStepfunVoiceId,
  stepfunProvision,
  type StepfunProvisionOptions,
  stepfunSynthesize,
} from "./stepfun";
import { xiaomiProvision, xiaomiSynthesize } from "./xiaomi";

// Re-export so /api/tts-provider, orchestrator, CharacterDesigner prompt, and
// the client all share ONE provider-detection rule + ONE catalog rendering +
// ONE validity check with the synth path.
export { isStepfun, isValidStepfunVoiceId, formatStepfunCatalogForPrompt };

/** Map a configured TtsConfig to its provider tag. Single source of truth for
 *  the inference rule (host contains stepfun.com → stepfun, else xiaomi) so
 *  /api/tts-provider and resolveVoice can't drift when a third provider is
 *  added. A PRESENT TtsConfig always maps to a concrete provider — `null`
 *  (no TTS configured) is the caller's responsibility to handle separately. */
export function inferTtsProvider(cfg: TtsConfig): Exclude<TtsProvider, null> {
  return isStepfun(cfg) ? "stepfun" : "xiaomi";
}

// `opts.stepfunVoiceId` threads the CharacterDesigner's LLM-selected preset
// down to stepfunProvision. Xiaomi ignores it. See StepfunProvisionOptions.
export type ProvisionVoiceOptions = StepfunProvisionOptions;

export async function provisionVoice(
  cfg: TtsConfig,
  description: string,
  // Optional per-character salt (typically the character name). Only
  // StepFun's preset-picker uses it — Xiaomi voicedesign mints a unique
  // clip per call regardless. Threading it through keeps the API uniform
  // and prevents archetype collisions on the StepFun path.
  salt?: string,
  opts?: ProvisionVoiceOptions,
): Promise<CharacterVoice> {
  return isStepfun(cfg)
    ? stepfunProvision(cfg, description, salt, opts)
    : xiaomiProvision(cfg, description);
}

// Dispatch by the voice's own provider tag, not by the current config. A
// session can outlive a provider switch (e.g. .env.local flip mid-game), and
// each voice must be synthesized via the protocol that minted it. The cfg
// still needs to point at the matching provider's endpoint; mismatch surfaces
// as a transparent network error, which `synthesizeBeat` already swallows.
export async function synthesize(
  cfg: TtsConfig,
  voice: CharacterVoice,
  text: string,
  delivery?: string,
  signal?: AbortSignal,
): Promise<{ audioBase64: string; mimeType: string }> {
  if (voice.provider === "stepfun") {
    return stepfunSynthesize(cfg, voice, text, delivery, signal);
  }
  return xiaomiSynthesize(cfg, voice, text, delivery, signal);
}
