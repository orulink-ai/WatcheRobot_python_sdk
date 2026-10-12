import { build } from "esbuild";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import { readFile, writeFile, readdir } from "node:fs/promises";
const root = dirname(fileURLToPath(import.meta.url));
const result = await build({
  absWorkingDir: root,
  entryPoints: ["visuals.tsx"],
  outfile: resolve(root, "../web/visuals.js"),
  bundle: true,
  format: "esm",
  minify: true,
  legalComments: "external",
  target: "es2022",
  define: { "process.env.NODE_ENV": '"production"' },
  metafile: true,
  banner: {
    js: "/*! Licenses: ELEVENLABS-LICENSE.txt, VISUALS-LICENSES.txt, visuals.js.LEGAL.txt */",
  },
});
const packages = new Set(
  Object.keys(result.metafile.inputs).flatMap((input) => {
    const match = input
      .replaceAll("\\", "/")
      .match(/^node_modules\/((?:@[^/]+\/)?[^/]+)/);
    return match ? [match[1]] : [];
  }),
);
const licenses = [];
for (const name of [...packages].sort()) {
  const directory = resolve(root, "node_modules", name);
  const manifest = JSON.parse(
    await readFile(resolve(directory, "package.json"), "utf8"),
  );
  const filename = (await readdir(directory)).find((file) =>
    /^licen[cs]e(?:\.[^.]+)?$/i.test(file),
  );
  const vendored =
    name === "@react-three/fiber"
      ? "react-three-fiber.txt"
      : `${name.split("/").at(-1)}.txt`;
  const licensePath = filename
    ? resolve(directory, filename)
    : resolve(root, "licenses", vendored);
  // Fail instead of silently omitting an attribution from the release bundle.
  licenses.push(
    `${name} ${manifest.version}\n\n${await readFile(licensePath, "utf8")}`,
  );
}
await writeFile(
  resolve(root, "../web/VISUALS-LICENSES.txt"),
  licenses.join("\n\n--------------------\n\n"),
);
