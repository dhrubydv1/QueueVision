"use client";

import { useState } from "react";
import { uploadVideo, type CameraInfo, type Run } from "@/lib/api";

const CONTROL = "h-10 max-sm:h-11 rounded-control border border-border-input bg-surface px-3 text-sm";

export function UploadForm({ cameras, onStarted }: Readonly<{ cameras: CameraInfo[]; onStarted: (run: Run) => void }>) {
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function onSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    const form = new FormData();
    form.set("file", data.get("file") as Blob);
    form.set("camera_id", String(data.get("camera_id") ?? ""));
    form.set("frame_stride", String(data.get("frame_stride") ?? "1"));
    form.set("blur_heads", data.get("blur_heads") === "on" ? "true" : "false");
    form.set("realtime", data.get("realtime") === "on" ? "true" : "false");
    setBusy(true);
    setError(null);
    try {
      onStarted(await uploadVideo(form));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Upload failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={onSubmit} className="flex flex-wrap items-end gap-4 rounded-card border border-border bg-surface p-5">
      <label className="flex flex-col gap-1 text-[13px] font-medium text-text-2">
        Video file
        <input type="file" name="file" accept="video/*" required className={`${CONTROL} py-1.5`} />
      </label>
      <label className="flex flex-col gap-1 text-[13px] font-medium text-text-2">
        Camera config
        <select name="camera_id" required className={CONTROL}>
          {cameras.map((c) => <option key={c.id} value={c.id}>{c.id}</option>)}
        </select>
      </label>
      <label className="flex flex-col gap-1 text-[13px] font-medium text-text-2">
        Every Nth frame
        <input type="number" name="frame_stride" min={1} max={10} defaultValue={1} className={`${CONTROL} w-24`} />
      </label>
      <label className="flex items-center gap-2 text-sm text-text">
        <input type="checkbox" name="blur_heads" defaultChecked /> Blur heads
      </label>
      <label className="flex items-center gap-2 text-sm text-text">
        <input type="checkbox" name="realtime" /> Process in real time
      </label>
      <button
        type="submit"
        disabled={busy}
        className="h-10 max-sm:h-11 rounded-control bg-primary px-4 text-sm font-medium text-white hover:bg-primary-hover disabled:opacity-60"
      >
        Start
      </button>
      {error ? <p role="alert" className="basis-full text-sm text-danger">{error}</p> : null}
    </form>
  );
}
