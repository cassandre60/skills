import test from "node:test";
import assert from "node:assert/strict";

import { hasDiscount } from "../src/cart.ts";

test("a large order qualifies for the discount", () => {
  assert.equal(hasDiscount(10), true);
});

test("a single item does not qualify for the discount", () => {
  assert.equal(hasDiscount(1), false);
});
