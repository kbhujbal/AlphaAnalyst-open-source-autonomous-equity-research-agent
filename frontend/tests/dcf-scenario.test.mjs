import assert from "node:assert/strict";
import test from "node:test";
import { DCFScenarioZ } from "../lib/api/dcf-scenario.ts";

const scenario = { name: "Base case", rationale: "Analyst assumptions for a steady-state business", wacc: "0.10000000000000000001", terminal_growth: "0.025", fcff_margin: "0.12", projection_years: 5 };

test("scenario inputs preserve exact decimal strings", () => {
  assert.equal(DCFScenarioZ.parse(scenario).wacc, scenario.wacc);
});

test("scenarios require explicit assumptions and a valid terminal spread", () => {
  for (const update of [{ wacc: "" }, { wacc: "NaN" }, { wacc: "10" }, { terminal_growth: "0.2" }, { fcff_margin: "" }, { rationale: "" }, { projection_years: 2 }]) {
    assert.equal(DCFScenarioZ.safeParse({ ...scenario, ...update }).success, false);
  }
});
