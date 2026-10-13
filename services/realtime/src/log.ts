type Level = "info" | "warn" | "error";

export function log(level: Level, message: string, extra: Record<string, unknown> = {}): void {
  const line = JSON.stringify({ time: new Date().toISOString(), level, message, ...extra });
  (level === "info" ? console.log : console.error)(line);
}
