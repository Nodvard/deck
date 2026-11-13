import ts from "typescript";
import { expect, it } from "vitest";

it("weist eine neue Anzeigeart ohne Fixture zurück", () => {
  const entry = ts.sys.resolvePath("src/widgets/__fixtures__/index.ts");
  const options: ts.CompilerOptions = {
    target: ts.ScriptTarget.ES2022,
    module: ts.ModuleKind.ESNext,
    moduleResolution: ts.ModuleResolutionKind.Bundler,
    strict: true,
    skipLibCheck: true,
    noEmit: true,
  };
  const diagnostics = (extraKind: boolean) => {
    const host = ts.createCompilerHost(options);
    const readFile = host.readFile;
    host.readFile = (file) => {
      const source = readFile(file);
      if (extraKind && file.replace(/\\/g, "/").endsWith("/src/widgets/types.ts")) {
        return source?.replace("export type View =", 'export type View = { kind: "missing_fixture" }');
      }
      return source;
    };
    const program = ts.createProgram([entry], options, host);
    return program.getSemanticDiagnostics(program.getSourceFile(entry));
  };

  expect(diagnostics(false)).toEqual([]);
  expect(diagnostics(true).map((diagnostic) => ts.flattenDiagnosticMessageText(diagnostic.messageText, "\n")))
    .toEqual([expect.stringContaining("missing_fixture")]);
}, 15_000);
