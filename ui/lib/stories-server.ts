/**
 * 서버 전용: data/stories_latest.json 읽기.
 *
 * 이 모듈은 Server Component / Route Handler 에서만 import. 클라이언트가
 * 끌고 가면 webpack 이 node:fs 에서 fail.
 */
import "server-only";

import { promises as fs } from "node:fs";
import path from "node:path";

import type { StoriesLatest } from "./stories";

function defaultPath(): string {
  if (process.env.STORIES_LATEST_PATH) return process.env.STORIES_LATEST_PATH;
  // ui/ 에서 실행되므로 한 단계 위 data/stories_latest.json
  return path.join(process.cwd(), "..", "data", "stories_latest.json");
}

export async function readStoriesLatest(
  filePath: string = defaultPath()
): Promise<StoriesLatest | null> {
  try {
    const raw = await fs.readFile(filePath, "utf-8");
    return JSON.parse(raw) as StoriesLatest;
  } catch (err) {
    if ((err as NodeJS.ErrnoException).code === "ENOENT") return null;
    throw err;
  }
}
