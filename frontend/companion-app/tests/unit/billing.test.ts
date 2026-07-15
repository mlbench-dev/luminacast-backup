import { describe, it, expect } from "vitest";
import {
  formatCents,
  formatDollars,
  calculateStreamingCost,
  formatDuration,
  formatMinutes,
  PRICING,
} from "@/lib/billing";

describe("billing utilities", () => {
  describe("formatCents", () => {
    it("should format cents to dollar string", () => {
      expect(formatCents(1499)).toBe("$14.99");
      expect(formatCents(0)).toBe("$0.00");
      expect(formatCents(100)).toBe("$1.00");
      expect(formatCents(999)).toBe("$9.99");
    });
  });

  describe("formatDollars", () => {
    it("should format dollar amounts", () => {
      expect(formatDollars(14.99)).toBe("$14.99");
      expect(formatDollars(0)).toBe("$0.00");
      expect(formatDollars(1.5)).toBe("$1.50");
    });
  });

  describe("calculateStreamingCost", () => {
    it("should calculate cost for streaming minutes", () => {
      expect(calculateStreamingCost(60)).toBe(180); // $1.80/hr
      expect(calculateStreamingCost(1)).toBe(3); // $0.03
      expect(calculateStreamingCost(0)).toBe(0);
    });

    it("should round up partial minutes", () => {
      expect(calculateStreamingCost(0.5)).toBe(3); // ceil(0.5) = 1
    });
  });

  describe("formatDuration", () => {
    it("should format seconds to human-readable duration", () => {
      expect(formatDuration(0)).toBe("0s");
      expect(formatDuration(45)).toBe("45s");
      expect(formatDuration(90)).toBe("1m 30s");
      expect(formatDuration(3661)).toBe("1h 1m 1s");
    });
  });

  describe("formatMinutes", () => {
    it("should format minutes to human-readable", () => {
      expect(formatMinutes(5)).toBe("5m");
      expect(formatMinutes(90)).toBe("1h 30m");
      expect(formatMinutes(0)).toBe("0m");
    });
  });

  describe("PRICING", () => {
    it("should have correct pricing constants", () => {
      expect(PRICING.AVATAR_SETUP_CENTS).toBe(999);
      expect(PRICING.CAST_CREATION_CENTS).toBe(1499);
      expect(PRICING.STREAMING_PER_MINUTE_CENTS).toBe(3);
      expect(PRICING.CLIP_REGENERATION_CENTS).toBe(99);
    });
  });
});
