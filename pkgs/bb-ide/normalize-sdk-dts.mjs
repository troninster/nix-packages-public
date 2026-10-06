// The bundled SDK can emit equivalent Zod shape/enum property maps in a
// different order. Canonicalize only their unique property signatures; never
// reorder overloads, unions, tuples or arbitrary declaration members.
import { readFileSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath } from "node:url";

export function normalizeDeclarations(source, ts) {
  const file = ts.createSourceFile("index.d.ts", source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS);
  if (file.parseDiagnostics.length) throw new Error("Invalid SDK declaration syntax");
  const transformed = ts.transform(file, [(context) => {
    function visit(node) {
      const visited = ts.visitEachChild(node, visit, context);
      if (!ts.isTypeReferenceNode(visited)) return visited;
      const name = ts.isQualifiedName(visited.typeName) ? visited.typeName.right.text : visited.typeName.text;
      const shape = visited.typeArguments?.[0];
      if (!["ZodObject", "ZodEnum"].includes(name) || !shape || !ts.isTypeLiteralNode(shape)) return visited;
      const members = [...shape.members];
      if (!members.every((member) => ts.isPropertySignature(member)
          && (ts.isIdentifier(member.name) || ts.isStringLiteral(member.name)))) return visited;
      const keys = members.map((member) => member.name.text);
      if (new Set(keys).size !== keys.length) return visited;
      members.sort((a, b) => a.name.text < b.name.text ? -1 : a.name.text > b.name.text ? 1 : 0);
      return ts.factory.updateTypeReferenceNode(visited, visited.typeName, [
        ts.factory.updateTypeLiteralNode(shape, members), ...visited.typeArguments.slice(1),
      ]);
    }
    return (root) => ts.visitNode(root, visit);
  }]);
  try {
    return ts.createPrinter({ newLine: ts.NewLineKind.LineFeed }).printFile(transformed.transformed[0]);
  } finally {
    transformed.dispose();
  }
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  if (process.argv.length !== 3) throw new Error("Expected exactly one SDK declaration file");
  // Use the already pinned JS TypeScript compiler, not a new dependency.
  const ts = createRequire(path.resolve("packages/bb-app/package.json"))("typescript");
  const file = process.argv[2];
  writeFileSync(file, normalizeDeclarations(readFileSync(file, "utf8"), ts));
}
