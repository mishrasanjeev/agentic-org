// SPDX-License-Identifier: Apache-2.0
import { useCallback, useRef } from "react";

/**
 * Milliseconds since the screen rendered, for advisory dwell telemetry.
 *
 * This is *not* the dwell that a decision grant carries: that one is measured
 * by the approval page from its own timestamps and is the only one any
 * authority path trusts. This number only tells operators how long a case
 * screen was open before someone acted on it.
 */
export function useDwellTimer(): () => number {
  const startedAt = useRef<number>(Date.now());
  return useCallback(() => Math.max(0, Date.now() - startedAt.current), []);
}
