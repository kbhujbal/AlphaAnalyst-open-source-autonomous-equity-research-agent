// Test-only loader: render the actual TSX components with existing TypeScript/React.
import { readFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import { fileURLToPath } from "node:url";
import ts from "typescript";

const root = new URL("../../", import.meta.url);
export async function resolve(specifier, context, next) {
  let candidate;
  if (specifier.startsWith("@/")) candidate = new URL(specifier.slice(2), root);
  else if (specifier.startsWith(".") && context.parentURL) candidate = new URL(specifier, context.parentURL);
  if (candidate) {
    for (const suffix of ["", ".ts", ".tsx"]) {
      const url = `${candidate.href}${suffix}`;
      if (/\.tsx?$/.test(url) && existsSync(fileURLToPath(url))) return { url, shortCircuit: true };
    }
  }
  return next(specifier, context);
}
export async function load(url, context, next) {
  if (/\.tsx?$/.test(url) && !url.includes("/node_modules/")) {
    const source = await readFile(new URL(url), "utf8");
    return { format: "module", shortCircuit: true, source: ts.transpileModule(source, {
      fileName: fileURLToPath(url), compilerOptions: { module: ts.ModuleKind.ESNext, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2022 },
    }).outputText };
  }
  return next(url, context);
}
