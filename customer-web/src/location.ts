// The location check for ordering without the PIN (only where the restaurant turned it on).
// One fresh position, only when the guest taps the button: never watched, never cached, never
// stored by this page. The server decides; this only reports what the phone says.

export interface PositionReport {
  latitude: number;
  longitude: number;
  accuracy_m: number;
  captured_at: string; // ISO 8601, UTC
}

interface PositionLike {
  coords: { latitude: number; longitude: number; accuracy: number };
  timestamp: number;
}

/** Browsers only share a position with secure pages (HTTPS). */
export function locationSupported(): boolean {
  return typeof window !== "undefined" && window.isSecureContext && "geolocation" in navigator;
}

// Browsers don't agree on Position.timestamp: WebKit gives microseconds, some engines and
// phones stamp fixes hours off. The fix was just requested with maximumAge 0 (never a cached
// one), so a stamp far from the phone's clock is replaced by "now". The server still refuses
// stale or future reports.
const STAMP_TOLERANCE_MS = 5 * 60_000;

export function toReport(position: PositionLike, now: number = Date.now()): PositionReport {
  const stamp = position.timestamp;
  const taken = Number.isFinite(stamp) && Math.abs(stamp - now) <= STAMP_TOLERANCE_MS ? stamp : now;
  return {
    latitude: position.coords.latitude,
    longitude: position.coords.longitude,
    accuracy_m: position.coords.accuracy,
    captured_at: new Date(taken).toISOString(),
  };
}

/** A fresh, precise fix (maximumAge 0: no cached position). Rejects with the browser's error. */
export function currentPosition(timeoutMs = 20_000): Promise<PositionReport> {
  return new Promise((resolve, reject) => {
    navigator.geolocation.getCurrentPosition(
      (position) => resolve(toReport(position)),
      (error) => reject(error),
      { enableHighAccuracy: true, timeout: timeoutMs, maximumAge: 0 },
    );
  });
}

/** A browser geolocation failure (GeolocationPositionError: 1 denied, 2 unavailable, 3 timeout). */
export function isPositionError(error: unknown): error is { code: number } {
  return typeof error === "object" && error !== null && "code" in error && typeof (error as { code: unknown }).code === "number";
}

export function positionErrorText(code: number): string {
  if (code === 1) return "Location permission was not given.";
  if (code === 3) return "Finding your location took too long.";
  return "Your phone couldn't find its location.";
}

/** What to say when the server refuses the check (its own wording, kept short). */
export function refusalText(code: string, message: string): string {
  switch (code) {
    case "location_outside":
      return "You don't seem to be at the restaurant.";
    case "location_inaccurate":
      return "Your phone isn't sure enough where it is. Try again in a minute, nearer a window.";
    case "location_stale":
    case "location_reused":
      return "Please try again.";
    case "access_method_off":
      return "Ordering by location isn't available here right now.";
    default:
      return message;
  }
}
