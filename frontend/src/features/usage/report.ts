/**
 * The Usage section's pure logic: which weeks a range means, what the file is
 * called, and how the two exports leave the page. No React here.
 */
import type { UsageWeek } from "@/api/types";

/** A range of ISO weeks. "" leaves that end open (the first or the newest week). */
export interface WeekRange {
  from: string;
  to: string;
}

export const OPEN_RANGE: WeekRange = { from: "", to: "" };

/**
 * A stored range made safe against the weeks that exist now: a bound naming a
 * week that is no longer stored falls back to open, and a range picked
 * backwards is put the right way round rather than rendering nothing.
 */
export function normalizeRange(range: WeekRange, weeks: UsageWeek[]): WeekRange {
  const known = new Set(weeks.map((w) => w.week));
  let from = known.has(range.from) ? range.from : "";
  let to = known.has(range.to) ? range.to : "";
  if (from && to && from > to) [from, to] = [to, from];
  return { from, to };
}

/** The newest `n` weeks as a range, open at the newest end. */
export function lastWeeks(weeks: UsageWeek[], n: number): WeekRange {
  if (!weeks.length) return OPEN_RANGE;
  const start = weeks[Math.max(0, weeks.length - n)];
  return { from: start.week, to: "" };
}

/** `usage-report_2026-W35_2026-W38.html`, or `usage-report.html` for an empty page. */
export function reportFilename(selected: string[], ext: "html" | "pdf" = "html"): string {
  if (!selected.length) return `usage-report.${ext}`;
  const first = selected[0];
  const last = selected[selected.length - 1];
  return `usage-report_${first === last ? first : `${first}_${last}`}.${ext}`;
}

/** Download a string as a file, through an object URL. */
export function downloadText(text: string, filename: string, type = "text/html;charset=utf-8"): void {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  // Revoked on the next tick: revoking synchronously can cancel the download.
  setTimeout(() => URL.revokeObjectURL(url), 0);
}

/**
 * "Save as PDF" is the browser's print dialog over the report itself: the page
 * carries print CSS (A4, light, charts kept whole), and its charts are static
 * SVG, so what prints is what is on screen. Printing the frame keeps the app's
 * own chrome out of the PDF. If the frame cannot be printed (a browser that
 * refuses a sandboxed print), the report opens in a window of its own and
 * prints from there.
 */
export function printReport(frame: HTMLIFrameElement | null, html: string, title: string): void {
  const win = frame?.contentWindow;
  try {
    if (!win) throw new Error("no frame");
    win.focus();
    win.print();
    return;
  } catch {
    const url = URL.createObjectURL(new Blob([html], { type: "text/html;charset=utf-8" }));
    const w = window.open(url, "_blank");
    if (w) {
      w.addEventListener("load", () => {
        w.document.title = title;
        w.print();
      });
    }
    setTimeout(() => URL.revokeObjectURL(url), 60_000);
  }
}
