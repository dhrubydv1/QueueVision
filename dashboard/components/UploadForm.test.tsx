import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "@/lib/api";
import { UploadForm } from "./UploadForm";

vi.mock("@/lib/api", async (importOriginal) => ({ ...(await importOriginal<typeof import("@/lib/api")>()), uploadVideo: vi.fn() }));

const cameras = [{ id: "walker" }] as never;

async function submit(container: HTMLElement) {
  const file = new File(["x"], "clip.mp4", { type: "video/mp4" });
  fireEvent.change(container.querySelector("input[type=file]")!, { target: { files: [file] } });
  fireEvent.submit(container.querySelector("form")!);
  await waitFor(() => expect(api.uploadVideo).toHaveBeenCalled());
  return vi.mocked(api.uploadVideo).mock.calls[0][0] as FormData;
}

describe("UploadForm privacy selects", () => {
  beforeEach(() => vi.mocked(api.uploadVideo).mockReset().mockResolvedValue({ video_id: 1 } as never));

  it("defaults the saved video to boxes and the preview to frame", async () => {
    const { container } = render(<UploadForm cameras={cameras} onStarted={() => {}} />);
    expect((screen.getByLabelText("Saved video privacy") as HTMLSelectElement).value).toBe("boxes");
    expect((screen.getByLabelText("Live preview privacy") as HTMLSelectElement).value).toBe("frame");
    const form = await submit(container);
    expect(form.get("privacy_mode")).toBe("boxes");
    expect(form.get("preview_privacy_mode")).toBe("frame");
    expect(form.get("blur_heads")).toBe("true");
  });

  it("sends the chosen modes and offers no 'none' option", async () => {
    const { container } = render(<UploadForm cameras={cameras} onStarted={() => {}} />);
    fireEvent.change(screen.getByLabelText("Saved video privacy"), { target: { value: "frame" } });
    fireEvent.change(screen.getByLabelText("Live preview privacy"), { target: { value: "boxes" } });
    for (const name of ["Saved video privacy", "Live preview privacy"]) {
      const values = Array.from((screen.getByLabelText(name) as HTMLSelectElement).options).map((o) => o.value);
      expect(values.sort()).toEqual(["boxes", "frame"]);
    }
    const form = await submit(container);
    expect(form.get("privacy_mode")).toBe("frame");
    expect(form.get("preview_privacy_mode")).toBe("boxes");
  });
});
