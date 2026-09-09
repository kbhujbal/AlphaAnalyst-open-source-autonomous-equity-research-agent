import { z } from "zod";

const fraction = z.string().regex(/^-?\d+(?:\.\d+)?$/, "Enter a decimal fraction, for example 0.10 for 10%.");
export const DCFScenarioZ = z.object({
  name: z.string().trim().min(1).max(120),
  rationale: z.string().trim().min(12, "Explain the scenario assumptions in at least 12 characters.").max(2000),
  wacc: fraction.refine(value => Number(value) > 0 && Number(value) < 1, "WACC must be between 0 and 1."),
  terminal_growth: fraction.refine(value => Number(value) > -1 && Number(value) < 1, "Terminal growth must be between -1 and 1."),
  fcff_margin: fraction.refine(value => Number(value) >= -1 && Number(value) <= 1, "FCFF margin must be between -1 and 1."),
  projection_years: z.number().int().min(3).max(10),
}).refine(value => Number(value.terminal_growth) < Number(value.wacc), "Terminal growth must be below WACC.");
