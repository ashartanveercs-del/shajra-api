import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import RootLayout from "./layout";

vi.mock("@/components/Navbar", () => ({
  default: () => <nav aria-label="Primary navigation" />,
}));

describe("public layout privacy", () => {
  it("does not publish WhatsApp or phone contact details", () => {
    const markup = renderToStaticMarkup(
      <RootLayout>
        <div>Family content</div>
      </RootLayout>,
    );

    expect(markup).not.toMatch(/whatsapp/i);
    expect(markup).not.toMatch(/\b(?:\+?92|0)3\d{9}\b/);
  });
});
