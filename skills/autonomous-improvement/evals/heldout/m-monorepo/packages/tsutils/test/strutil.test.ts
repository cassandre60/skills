import test from "node:test";
import assert from "node:assert/strict";

import { capitalize } from "../src/strutil.ts";

test("capitalizes a lowercase word", () => {
  assert.equal(capitalize("hello"), "Hello");
});

test("leaves the rest of the string alone", () => {
  assert.equal(capitalize("hELLO"), "HELLO");
});

test("empty string stays empty", () => {
  assert.equal(capitalize(""), "");
});
