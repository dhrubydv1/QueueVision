import { type ChildProcess, spawn } from "node:child_process";
import { existsSync, mkdtempSync, rmSync } from "node:fs";
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

/** One `python -m api` process on a temp data dir; `restart()` reuses the same data dir. */
export class ApiServer {
  readonly dataDir = mkdtempSync(path.join(tmpdir(), "qv-e2e-"));
  private child: ChildProcess | null = null;

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
