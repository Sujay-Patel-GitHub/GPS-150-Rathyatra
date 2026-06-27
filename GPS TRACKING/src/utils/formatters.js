// src/utils/formatters.js

/**
 * Parse a Firebase timestamp string ("YYYY-MM-DD HH:mm:ss") into a JS Date.
 * GPS modules output UTC time. We append "Z" to force UTC parsing —
 * without it, browsers in IST (+05:30) treat the time as local,
 * making the timestamp appear 5.5 hours in the past → always "Offline".
 */
export function parseTimestamp(ts) {
  if (!ts) return new Date(0);
  if (ts instanceof Date) return ts;
  if (typeof ts === "number") return new Date(ts);
  if (typeof ts !== "string") {
    try {
      const d = new Date(ts);
      return isNaN(d.getTime()) ? new Date(0) : d;
    } catch {
      return new Date(0);
    }
  }

  let cleaned = ts.trim();
  if (!cleaned) return new Date(0);

  // If it doesn't contain T, replace space with T
  if (!cleaned.includes("T") && cleaned.includes(" ")) {
    cleaned = cleaned.replace(" ", "T");
  }

  // If it doesn't end with Z and doesn't have an offset (+/-), append Z
  if (!cleaned.endsWith("Z") && !cleaned.includes("+") && !/-\d{2}:\d{2}$/.test(cleaned)) {
    cleaned = cleaned + "Z";
  }

  try {
    const d = new Date(cleaned);
    if (!isNaN(d.getTime())) {
      return d;
    }
  } catch {}

  try {
    const d = new Date(ts);
    if (!isNaN(d.getTime())) {
      return d;
    }
  } catch {}

  return new Date(0);
}

/**
 * Return a human-readable relative time string, e.g. "2 min ago".
 */
export function timeAgo(ts) {
  const date = parseTimestamp(ts);
  if (isNaN(date.getTime()) || date.getTime() === 0) return "No data";
  
  const diffSec = Math.floor((Date.now() - date.getTime()) / 1000);
  if (isNaN(diffSec)) return "No data";

  if (diffSec < 5) return "Just now";
  if (diffSec < 60) return `${diffSec}s ago`;
  if (diffSec < 3600) return `${Math.floor(diffSec / 60)}m ago`;
  if (diffSec < 86400) return `${Math.floor(diffSec / 3600)}h ago`;
  return date.toLocaleDateString();
}

/**
 * Format a timestamp for display in the detail panel.
 */
export function formatDateTime(ts) {
  const date = parseTimestamp(ts);
  if (isNaN(date.getTime()) || date.getTime() === 0) return "—";
  try {
    return date.toLocaleString("en-IN", {
      day: "2-digit",
      month: "short",
      year: "numeric",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    });
  } catch {
    return "—";
  }
}

/**
 * Determine online/offline based on how old the timestamp is.
 */
export function isOnline(ts, thresholdSeconds = 30) {
  const date = parseTimestamp(ts);
  const diffSec = (Date.now() - date.getTime()) / 1000;
  return diffSec <= thresholdSeconds;
}

/**
 * Build a Google Maps navigation URL.
 */
export function buildNavUrl(lat, lng) {
  return `https://www.google.com/maps/dir/?api=1&destination=${lat},${lng}`;
}

/**
 * Format coordinates to 6 decimal places.
 */
export function fmtCoord(val) {
  return typeof val === "number" ? val.toFixed(6) : "—";
}

/**
 * Format speed with unit.
 */
export function fmtSpeed(val) {
  return typeof val === "number" ? `${val} km/h` : "—";
}
