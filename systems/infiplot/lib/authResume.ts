// Shared primitives for surviving an OAuth full-page round-trip.
//
// Google / GitHub OAuth is a full-page redirect: it unmounts the React tree
// and discards all in-memory state (the server is stateless, so the client
// carries everything). To resume where the user left off after the redirect,
// a page snapshots its domain state into sessionStorage just before navigating
// away, then consumes the snapshot on the next mount — but only if the user is
// now actually signed in.
//
// Email-OTP login never redirects (it resolves in-page), so it bypasses this
// machinery entirely and resumes synchronously via AuthModal.onSuccess.
//
// This module holds the three page-agnostic pieces: the login check, a
// quota-safe sessionStorage write (heavy data-URL fields are stripped on
// QuotaExceededError), and the consume-once resume gate. Each page keeps its
// own snapshot shape and restore side effects — only the plumbing is shared.

import { AUTH_ENABLED } from "@/lib/supabase/config";
import { createClient as createSupabaseClient } from "@/lib/supabase/client";

// True when auth is disabled (self-host with blank Supabase env) or the visitor
// already has a session. Gates any auth-required action (and the resume path).
export async function isAuthed(): Promise<boolean> {
  if (!AUTH_ENABLED) return true;
  const sb = createSupabaseClient();
  const { data } = await sb.auth.getUser();
  return !!data.user;
}

// Write a resume snapshot to sessionStorage with a quota-safe fallback.
// `fallbacks` is an ordered list of progressively-lighter payloads to try if
// the primary write fails (typically QuotaExceededError from a data-URL image).
// Each fallback drops some non-essential heavy field while keeping the data
// needed to resume. A dropped field only affects *future* generation (e.g. the
// painter on later scenes), never the scene being resumed, so degrading is
// graceful. Returns true if any write succeeded.
export function writeResumeSnapshot<T>(
  key: string,
  primary: T,
  fallbacks: readonly T[] = [],
): boolean {
  const tryWrite = (candidate: T): boolean => {
    try {
      sessionStorage.setItem(key, JSON.stringify(candidate));
      return true;
    } catch {
      return false; // QuotaExceededError (or disabled storage)
    }
  };
  if (tryWrite(primary)) return true;
  for (const fb of fallbacks) {
    if (tryWrite(fb)) return true;
  }
  return false;
}

// Consume-once resume gate. Returns the parsed snapshot if one exists at `key`
// AND the user is now signed in (so a stale snapshot from a failed/abandoned
// login doesn't resurrect a half-flow). Always removes the entry — either it's
// consumed here, or it's stale and must not linger. Returns null when there's
// nothing to resume, the user isn't signed in, or the payload is corrupt.
//
// `removeItem` intentionally runs before `isAuthed()` so that a network error
// during the auth check does not leave a zombie snapshot behind. Without this
// ordering, callers that guard on the snapshot's presence (play-page bootstrap)
// would re-enter this path on every effect cycle, producing an infinite retry
// loop. Dropping the snapshot on a transient network glitch is an acceptable
// trade-off — the worst case is the user lands on the first scene instead of
// resuming mid-story, which is the same experience as before this feature.
export async function consumeResumeSnapshot<T>(key: string): Promise<T | null> {
  const raw = sessionStorage.getItem(key);
  if (!raw) return null;
  sessionStorage.removeItem(key);
  let authed: boolean;
  try {
    authed = await isAuthed();
  } catch {
    // Network / unexpected error during auth check. Snapshot already removed
    // (prevents the caller's retry loop); return null so callers fall back to
    // their default path (normal bootstrap).
    return null;
  }
  if (!authed) return null;
  try {
    return JSON.parse(raw) as T;
  } catch {
    return null; // corrupt snapshot — ignore
  }
}
