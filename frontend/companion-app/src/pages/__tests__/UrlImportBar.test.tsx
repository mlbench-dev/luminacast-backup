/// <reference types="@testing-library/jest-dom" />
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

// Capture every toast() call so we can assert on title/variant.
const toastCalls: Array<Record<string, unknown>> = [];
vi.mock("@/hooks/useToast", () => ({
  useToast: () => ({ toast: (args: Record<string, unknown>) => toastCalls.push(args) }),
}));

// Mock the products API surface used by the import bar.
const fromUrl = vi.fn();
vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    productsApi: { ...actual.productsApi, fromUrl: (url: string) => fromUrl(url) },
  };
});

import { UrlImportBar } from "../ProductLibrary";

function renderBar() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <UrlImportBar />
    </QueryClientProvider>,
  );
}

const TIKTOK_URL =
  "https://shop.tiktok.com/gb/pdp/1729774369529699250?source=ecommerce_category";

describe("UrlImportBar — 202 needs_manual_entry handling", () => {
  beforeEach(() => {
    toastCalls.length = 0;
    fromUrl.mockReset();
  });

  it("opens the manual-entry dialog prefilled with the source fields and shows NO success toast", async () => {
    fromUrl.mockResolvedValue({
      status: "needs_manual_entry",
      source: "tiktok",
      source_product_id: "1729774369529699250",
      source_url: TIKTOK_URL,
      message: "blocked",
    });

    renderBar();

    fireEvent.change(screen.getByPlaceholderText(/paste any product url/i), {
      target: { value: TIKTOK_URL },
    });
    fireEvent.click(screen.getByRole("button", { name: /import/i }));

    // The manual-entry dialog opens.
    await waitFor(() =>
      expect(screen.getByText(/add product details/i)).toBeInTheDocument(),
    );

    // An informational (NOT success) toast was shown.
    expect(toastCalls).toHaveLength(1);
    expect(toastCalls[0].variant).toBe("warning");
    expect(String(toastCalls[0].title)).not.toMatch(/imported|added/i);

    // No success toast ("Product imported!" / "Product added!") was fired.
    const successToast = toastCalls.find((t) =>
      /imported|added/i.test(String(t.title ?? "")),
    );
    expect(successToast).toBeUndefined();
  });

  it("submits the manual-entry form to the normal create endpoint with prefilled origin", async () => {
    fromUrl.mockResolvedValue({
      status: "needs_manual_entry",
      source: "tiktok",
      source_product_id: "1729774369529699250",
      source_url: TIKTOK_URL,
      message: "blocked",
    });

    const api = await import("@/lib/api");
    const createSpy = vi
      .spyOn(api.productsApi, "create")
      .mockResolvedValue({ id: "p_1", name: "My Product" } as never);

    renderBar();

    fireEvent.change(screen.getByPlaceholderText(/paste any product url/i), {
      target: { value: TIKTOK_URL },
    });
    fireEvent.click(screen.getByRole("button", { name: /import/i }));
    await waitFor(() =>
      expect(screen.getByText(/add product details/i)).toBeInTheDocument(),
    );

    fireEvent.change(screen.getByPlaceholderText(/product name/i), {
      target: { value: "My Product" },
    });
    fireEvent.click(screen.getByRole("button", { name: /add product/i }));

    await waitFor(() => expect(createSpy).toHaveBeenCalledTimes(1));
    expect(createSpy.mock.calls[0][0]).toMatchObject({
      name: "My Product",
      source: "tiktok",
      source_url: TIKTOK_URL,
      source_product_id: "1729774369529699250",
    });

    createSpy.mockRestore();
  });

  it("shows a success toast on a real imported product (no dialog)", async () => {
    fromUrl.mockResolvedValue({ id: "p_2", name: "Resolved Product" });

    renderBar();

    fireEvent.change(screen.getByPlaceholderText(/paste any product url/i), {
      target: { value: "https://example.com/p/2" },
    });
    fireEvent.click(screen.getByRole("button", { name: /import/i }));

    await waitFor(() => expect(toastCalls).toHaveLength(1));
    expect(String(toastCalls[0].title)).toMatch(/imported/i);
    expect(screen.queryByText(/add product details/i)).not.toBeInTheDocument();
  });
});
