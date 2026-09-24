// The single-use code a CLI-assisted sign-in link carries, taken out of the address.
// Run: node --experimental-strip-types --test <this file>
import { test } from "node:test"
import assert from "node:assert/strict"

import { LOGIN_CODE_PARAM, splitLoginCode } from "../../../frontend/src/providers/login-code.ts"

test("the code is read and the rest of the query is kept", () => {
  assert.deepEqual(splitLoginCode(`?source=apim&${LOGIN_CODE_PARAM}=abc123&page=budgets`), {
    code: "abc123",
    rest: "?source=apim&page=budgets",
  })
})

test("a link that is only the code leaves no query at all", () => {
  assert.deepEqual(splitLoginCode(`?${LOGIN_CODE_PARAM}=abc123`), { code: "abc123", rest: "" })
})

test("no code, or an empty one, is no code", () => {
  assert.deepEqual(splitLoginCode("?source=apim"), { code: null, rest: "?source=apim" })
  assert.deepEqual(splitLoginCode(`?${LOGIN_CODE_PARAM}=%20`), { code: null, rest: "" })
  assert.deepEqual(splitLoginCode(""), { code: null, rest: "" })
})