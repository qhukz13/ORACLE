/**
 * Test setup.
 *
 * `cleanup` after every test is not optional with @testing-library/react: without it
 * each render is appended to the same document, and a query that should find one
 * element finds every element from every test that ran before it. The failure looks
 * like a component bug ("found multiple elements") and is not one.
 */

import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

afterEach(() => {
  cleanup();
});
