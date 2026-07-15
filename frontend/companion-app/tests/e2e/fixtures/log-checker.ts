import { Page, ConsoleMessage } from "@playwright/test";

export interface LogEntry {
  type: string;
  text: string;
  timestamp: number;
}

/**
 * Collects browser console logs during a test for post-hoc analysis.
 * Attaches to page console events and stores them.
 */
export class LogChecker {
  private logs: LogEntry[] = [];
  private errors: LogEntry[] = [];

  constructor(private page: Page) {
    this.page.on("console", (msg: ConsoleMessage) => {
      const entry: LogEntry = {
        type: msg.type(),
        text: msg.text(),
        timestamp: Date.now(),
      };
      this.logs.push(entry);
      if (msg.type() === "error") {
        this.errors.push(entry);
      }
    });

    this.page.on("pageerror", (error: Error) => {
      this.errors.push({
        type: "pageerror",
        text: error.message,
        timestamp: Date.now(),
      });
    });
  }

  /** Get all collected logs */
  getLogs(): LogEntry[] {
    return [...this.logs];
  }

  /** Get only error logs */
  getErrors(): LogEntry[] {
    return [...this.errors];
  }

  /** Check if any error matches a pattern */
  hasError(pattern: string | RegExp): boolean {
    return this.errors.some((e) =>
      typeof pattern === "string"
        ? e.text.includes(pattern)
        : pattern.test(e.text)
    );
  }

  /** Assert no unexpected errors occurred (filters out known benign ones) */
  assertNoUnexpectedErrors(allowPatterns: (string | RegExp)[] = []): void {
    const defaultAllowed = [
      /favicon\.ico/,
      /ResizeObserver/,
      /Download the React DevTools/,
      /Failed to load resource.*404/,
      /net::ERR_/,
      /PostHog/i,
    ];
    const allowed = [...defaultAllowed, ...allowPatterns];

    const unexpected = this.errors.filter(
      (e) => !allowed.some((p) => (typeof p === "string" ? e.text.includes(p) : p.test(e.text)))
    );

    if (unexpected.length > 0) {
      const summary = unexpected
        .map((e) => `[${e.type}] ${e.text}`)
        .join("\n");
      throw new Error(
        `Found ${unexpected.length} unexpected error(s) in console:\n${summary}`
      );
    }
  }

  /** Clear collected logs */
  clear(): void {
    this.logs = [];
    this.errors = [];
  }

  /** Get log count by type */
  countByType(type: string): number {
    return this.logs.filter((l) => l.type === type).length;
  }
}
