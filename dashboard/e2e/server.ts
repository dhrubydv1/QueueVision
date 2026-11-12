import { type ChildProcess, spawn } from "node:child_process";
import { existsSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
export const REPO_ROOT = path.resolve(HERE, "../..");
export const PORT = 8765;
export const BASE_URL = `http://127.0.0.1:${PORT}`;
export const CLIP = path.join(REPO_ROOT, "datasets/raw/cad_seq25.mp4");
const MODEL = path.join(REPO_ROOT, "ai_engine/models/yolo11n.pt");
const PYTHON = path.join(REPO_ROOT, ".venv/bin/python");

/** Why the real-run tests cannot run here, or null. CAD footage and weights are not in CI. */
export function missingInputs(): string | null {
  if (!existsSync(CLIP)) return "datasets/raw/cad_seq25.mp4 is not present (CAD footage is local only)";
  if (!existsSync(MODEL)) return "ai_engine/models/yolo11n.pt is not present";
  if (!existsSync(PYTHON)) return ".venv/bin/python is not present";
  return null;
}

function syntheticSplit() {
  const cat = (key: string, label: string, direction: string, per_frame: number) => ({ key, label, direction, total: 10, per_frame });
  return {
    frames: 100,
    categories: [
      cat("o1", "Synthetic over-count cause with a deliberately long descriptive label", "over", 0.11),
      cat("u1", "Synthetic under-count cause with a deliberately long descriptive label", "under", 0.22),
      cat("c1", "Synthetic correct category", "correct", 5),
      cat("x1", "Synthetic other category", "other", 0.01),
    ],
  };
}

/** Made-up accuracy data (rendering checks only) with long names, so the Accuracy page has a wide table. */
const SYNTHETIC_ACCURACY = {
  schema: "accuracy-1",
  generated_at: "2026-01-01T00:00:00Z",
  missing_sources: [],
  queue_length: {
    source: "synthetic",
    protocol: { series: "smoothed", window: "synthetic window", tolerance_seconds: 1, from_seconds: 0 },
    per_clip: [
      { clip: "synthetic_clip_with_a_long_name_one", split: "val", mae: 1.1, rmse: 1.2, bias: 0.1, within_one_rate: 0.5, frames: 100 },
      { clip: "synthetic_clip_with_a_long_name_two", split: "test", mae: 2.2, rmse: 2.3, bias: -0.2, within_one_rate: 0.4, frames: 120 },
      { clip: "synthetic_clip_with_a_long_name_three", split: null, mae: 3.3, rmse: 3.4, bias: 0.3, within_one_rate: 0.3, frames: 90 },
    ],
    pooled: {
      val: { mae: 1.1, rmse: 1.2, bias: 0.1, within_one_rate: 0.5, frames: 100, clips: ["synthetic_clip_with_a_long_name_one"] },
      test: { mae: 2.2, rmse: 2.3, bias: -0.2, within_one_rate: 0.4, frames: 120, clips: ["synthetic_clip_with_a_long_name_two"] },
    },
  },
  count_errors: {
    source: "synthetic",
    iou: 0.5,
    window: "synthetic window",
    per_split: {
      all: syntheticSplit(),
      val: syntheticSplit(),
      test: syntheticSplit(),
    },
  },
  changes_not_adopted: [
    {
      name: "Synthetic change with a deliberately very long descriptive name for the first row",
      source: "synthetic_sprint_results_with_a_long_file_name_one.json",
      sprint: "Sprint A",
      val_mae: 1.5, test_mae: 2.5, baseline_val_mae: 1.4, baseline_test_mae: 2.4,
    },
    {
      name: "Another synthetic change, also long enough to force the table wider than a phone",
      source: "synthetic_sprint_results_with_a_long_file_name_two.json",
      sprint: "Sprint B",
      val_mae: 1.1, test_mae: 2.1, baseline_val_mae: 1.0, baseline_test_mae: 2.0,
    },
  ],
  waiting_time: null,
  limits: ["Synthetic limit"],
};

/** One `python -m api` process on a temp data dir; `restart()` reuses the same data dir. */
export class ApiServer {
  readonly dataDir = mkdtempSync(path.join(tmpdir(), "qv-e2e-"));
  private child: ChildProcess | null = null;

  constructor() {
    writeFileSync(path.join(this.dataDir, "accuracy.json"), JSON.stringify(SYNTHETIC_ACCURACY));
  }

  async start(): Promise<void> {
    if (this.child) throw new Error("server already running");
    const stale = await fetch(`${BASE_URL}/api/health`).then(
      (res) => res.ok,
      () => false,
    );
    if (stale) {
      throw new Error(`Port ${PORT} already answers /api/health (a stale server?). Stop it and rerun the e2e tests.`);
    }
    const child = spawn(PYTHON, ["-m", "api"], {
      cwd: REPO_ROOT,
      stdio: "ignore",
      env: {
        ...process.env,
        QUEUEVISION_DATA_DIR: this.dataDir,
        QUEUEVISION_PORT: String(PORT),
        QUEUEVISION_DASHBOARD_DIR: path.join(REPO_ROOT, "dashboard/out"),
        QUEUEVISION_CAMERA_DIR: path.join(REPO_ROOT, "configs/cameras"),
      },
    });
    this.child = child;
    child.once("exit", () => {
      if (this.child === child) this.child = null;
    });
    const deadline = Date.now() + 60_000;
    while (Date.now() < deadline) {
      if (this.child === null) throw new Error("API process exited during start-up");
      try {
        const res = await fetch(`${BASE_URL}/api/health`);
        if (res.ok) return;
      } catch {
        /* not listening yet */
      }
      await new Promise((r) => setTimeout(r, 250));
    }
    await this.stop();
    throw new Error("API did not become healthy within 60 s");
  }

  /** SIGTERM is a graceful shutdown (the running job ends cancelled); SIGKILL is a crash (the next start fails it). */
  async stop(signal: NodeJS.Signals = "SIGTERM"): Promise<void> {
    const child = this.child;
    if (!child) return;
    const exited = new Promise<void>((resolve) => child.once("exit", () => resolve()));
    child.kill(signal);
    const killer = setTimeout(() => child.kill("SIGKILL"), 15_000);
    await exited;
    clearTimeout(killer);
    this.child = null;
  }

  async dispose(): Promise<void> {
    await this.stop();
    rmSync(this.dataDir, { recursive: true, force: true });
  }
}
