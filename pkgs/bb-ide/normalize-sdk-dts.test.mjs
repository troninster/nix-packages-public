import assert from "node:assert/strict";
import { createRequire } from "node:module";
import path from "node:path";
import test from "node:test";
import { normalizeDeclarations } from "./normalize-sdk-dts.mjs";

const ts = createRequire(path.resolve("packages/bb-app/package.json"))("typescript");

test("equivalent nested Zod shape and quoted enum keys become identical", () => {
  const first = 'type A = z.ZodObject<{ b: z.ZodEnum<{ auto: "auto"; "accept-edits": "accept-edits" }>; a: z.ZodString }>;';
  const second = 'type A = z.ZodObject<{ a: z.ZodString; b: z.ZodEnum<{ "accept-edits": "accept-edits"; auto: "auto" }> }>;';
  const normalized = normalizeDeclarations(first, ts);
  assert.equal(normalized, normalizeDeclarations(second, ts));
  assert.equal(normalized, normalizeDeclarations(normalized, ts));
  assert.match(normalized, /a: z.ZodString/);
  assert.match(normalized, /"accept-edits": "accept-edits"/);
});

test("non-shape declarations and overload precedence are not sorted", () => {
  const input = 'type A = { z: string; a: number; f(x: "z"): 1; f(x: string): 2 }; type B = z.ZodObject<{ f(x: "z"): 1; f(x: string): 2 }>;';
  const normalized = normalizeDeclarations(input, ts);
  assert.ok(normalized.indexOf('z: string') < normalized.indexOf('a: number'));
  assert.ok(normalized.indexOf('f(x: "z"): 1') < normalized.indexOf('f(x: string): 2'));
  assert.ok(normalized.lastIndexOf('f(x: "z"): 1') < normalized.lastIndexOf('f(x: string): 2'));
});
