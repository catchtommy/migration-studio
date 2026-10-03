import { slug } from "./slug";

test("slug", () => {
  expect(slug("Security Review!")).toBe("security_review");
  expect(slug("  2nd Pass: Collateral  ", "-")).toBe("nd-pass-collateral");
  expect(slug("123")).toBe("");
});
