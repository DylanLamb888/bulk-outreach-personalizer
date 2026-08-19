import fs from "node:fs/promises";
import { createRequire } from "node:module";

const require = createRequire(
  "/Users/fundamentalhospitality/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/package.json",
);
const { Workbook } = require("@oai/artifact-tool");

const csvPath = process.argv[2];
const csvText = await fs.readFile(csvPath, "utf8");
const workbook = await Workbook.fromCSV(csvText, { sheetName: "Ready" });
const overview = await workbook.inspect({
  kind: "workbook,sheet",
  maxChars: 2000,
  tableMaxRows: 3,
  tableMaxCols: 8,
});
const sheet = workbook.worksheets.getItem("Ready");
const values = sheet.getUsedRange(true).values;
const headers = values[0].map((value) => String(value ?? ""));
const wanted = [
  "Company name",
  "Company domain",
  "personalization_source_focus",
  "personalization_focus",
  "personalization_buyer_phrase",
  "personalization_focus_rule",
  "personalized_pitch",
  "personalized_email",
  "personalization_status",
];
const indexes = wanted.map((header) => headers.indexOf(header));
const rows = values.slice(1).map((row) =>
  Object.fromEntries(wanted.map((header, index) => [header, row[indexes[index]] ?? ""])),
);
console.log(JSON.stringify({ overview: overview.ndjson, rowCount: rows.length, rows }, null, 2));
