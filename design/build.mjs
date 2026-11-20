import { readFileSync, writeFileSync } from "node:fs";
import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const root = join(here, "..");
const t = JSON.parse(readFileSync(join(here, "tokens.json"), "utf8"));

const lines = ["@theme static {", "  --color-*: initial;"];
for (const [k, v] of Object.entries(t.color)) lines.push(`  --color-${k}: ${v};`);
lines.push(`  --color-white: #FFFFFF;`, `  --color-black: #000000;`, `  --color-transparent: transparent;`, `  --color-current: currentColor;`);
for (const [k, v] of Object.entries(t.font)) lines.push(`  --font-${k}: ${v};`);
lines.push(`  --font-mono: ${t.font.sans};`);
for (const [k, v] of Object.entries(t.text)) lines.push(`  --text-${k}: ${v};`);
lines.push(`  --spacing: ${t.spacing.unit};`);
lines.push(`  --radius-control: ${t.radius.control};`, `  --radius-panel: ${t.radius.panel};`, `  --radius-card: ${t.radius.card};`, `  --radius-pill: ${t.radius.pill};`);
lines.push(`  --radius-xl: ${t.radius.control};`, `  --radius-2xl: ${t.radius.panel};`, `  --radius-3xl: ${t.radius.card};`);
for (const [k, v] of Object.entries(t.shadow)) lines.push(`  --shadow-${k}: ${v};`);
for (const [k, v] of Object.entries(t.motion)) lines.push(`  --motion-${k}: ${v};`);
lines.push("}");
writeFileSync(join(here, "theme.css"), lines.join("\n") + "\n");

const bin = join(here, "node_modules", ".bin", process.platform === "win32" ? "tailwindcss.cmd" : "tailwindcss");
execFileSync(bin, ["-i", join(here, "input.css"), "-o", join(root, "static/css/app.css"), "--minify"], {
  stdio: "inherit",
  shell: process.platform === "win32",
  cwd: here,
});