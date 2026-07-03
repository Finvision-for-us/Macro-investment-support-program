import "server-only";

import { promises as fs } from "node:fs";
import path from "node:path";

import type { Ingest2Report } from "./ingest2";

function defaultPath(): string {
  if (process.env.INGEST2_TOP10_PATH) return process.env.INGEST2_TOP10_PATH;
  return path.join(process.cwd(), "..", "data", "ingest2", "top10.json");
}

export async function readIngest2Report(
  filePath: string = defaultPath()
): Promise<Ingest2Report | null> {
  try {
    const raw = await fs.readFile(filePath, "utf-8");
    return JSON.parse(raw) as Ingest2Report;
  } catch (err) {
    if ((err as NodeJS.ErrnoException).code === "ENOENT") return null;
    throw err;
  }
}
