// Session slimming — the single definition of "shed a Session's bulky,
// reconstructible fields before it crosses a size-sensitive boundary".
//
// Two boundaries consume this, so the rule lives in one place (depends only on
// @infiplot/types, no engine/client imports, so both the storage layer and the
// engine transport layer can import it without pulling in each other's deps):
//   - network transport (lib/engineClient.ts) drops voice before POSTing the
//     session to scene/vision/insert-beat — voice is only used by /api/beat-audio.
//   - local persistence (lib/persistence/localStore.ts) drops voice AND the
//     style reference image before writing to IndexedDB.

import type { Session } from "@infiplot/types";

/** Drop each character's `voice` (the ~160-220KB referenceAudioBase64 + provider
 *  fields). The field is destructured out so it's ABSENT from the result rather
 *  than serialized as `undefined`. Tolerates a missing `characters` array. */
export function stripSessionVoices(session: Session): Session {
  return {
    ...session,
    characters: (session.characters ?? []).map(({ voice: _voice, ...rest }) => rest),
  };
}

/** The persistence-grade slim: voices stripped (via stripSessionVoices) AND the
 *  bulky `styleReferenceImage` removed. Both are reconstructible — voices
 *  re-provision on the next /api/scene call, and styleReferenceImage is cosmetic
 *  (the engine paints fine without it). Keeps each stored record small regardless
 *  of IndexedDB quota headroom. */
export function slimSession(session: Session): Session {
  // Destructure styleReferenceImage OUT (rather than set it to `undefined`) so
  // it's ABSENT from the result — the same absent-not-undefined invariant as
  // stripSessionVoices. structured-clone (IndexedDB) preserves an own key whose
  // value is `undefined`, which a next-phase sync reconciler probing
  // `'styleReferenceImage' in session` or Object.keys() would misread as present.
  const { styleReferenceImage: _styleRef, ...rest } = stripSessionVoices(session);
  return rest;
}
