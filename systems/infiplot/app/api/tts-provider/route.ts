import type { TtsProviderResponse } from "@infiplot/types";
import { inferTtsProvider } from "@infiplot/tts-client";
import { NextResponse } from "next/server";
import { loadEngineConfig } from "@/lib/config";
import { requireUser } from "@/lib/supabase/guard";

export const runtime = "nodejs";

// GET /api/tts-provider — tells the client which TTS provider the server is
// configured for, so the play page can shape /api/beat-audio request bodies
// accordingly (skip the ~220KB Xiaomi reference audio when the server runs
// StepFun → saves Fast Origin Transfer bandwidth; the response itself is a
// few dozen bytes). Runs once at /play mount; same auth as other routes so
// the provider (a server-config fact, not user data) isn't leaked publicly.
// BYO client TTS (clientTts:true) takes precedence and bypasses this signal.
export async function GET() {
  const auth = await requireUser();
  if (auth instanceof NextResponse) return auth;

  const cfg = loadEngineConfig();
  const provider = cfg.tts ? inferTtsProvider(cfg.tts) : null;

  const body: TtsProviderResponse = { provider };
  return NextResponse.json(body);
}
