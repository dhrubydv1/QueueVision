# Datasets

Raw and processed videos are **not committed to Git** (see `.gitignore`).
Only this README and the empty folder skeleton are tracked.

```
datasets/
├── raw/          # original downloaded videos (MP4/AVI/MOV)
├── processed/    # converted or trimmed copies
└── annotations/  # ground truth (e.g. MOT gt.txt) for future evaluation
```

## Videos used in Sprint 1

| File | Source | Resolution | FPS | Frames | Duration |
|---|---|---|---|---|---|
| `raw/mot17_09.mp4` | MOTChallenge MOT17-09 (pedestrian street, eye level, static camera) | 960×540 | 30 | 525 | 17.5 s |
| `raw/mot17_04.mp4` | MOTChallenge MOT17-04 (busy street at night, high-angle static camera) | 960×540 | 30 | 1050 | 35.0 s |

Both are MOTChallenge's per-sequence preview videos (WebM, about 5 MB each),
re-encoded to MP4 with H.264 locally. The full-resolution frame sequences
were **not** downloaded.

**Licence:** MOTChallenge sequences are released under
[Creative Commons Attribution-NonCommercial-ShareAlike 3.0](https://creativecommons.org/licenses/by-nc-sa/3.0/).
They may be used for this academic, non-commercial project with attribution.
Cite: Milan et al., *MOT16: A Benchmark for Multi-Object Tracking*, arXiv:1603.00831.

## Reproduce the downloads

```bash
cd datasets/raw
for s in MOT17-09-FRCNN MOT17-04-FRCNN; do
  curl -fL -o "$s-raw.webm" "https://motchallenge.net/sequenceVideos/$s-raw.webm"
done
ffmpeg -i MOT17-09-FRCNN-raw.webm -c:v libx264 -pix_fmt yuv420p -an mot17_09.mp4
ffmpeg -i MOT17-04-FRCNN-raw.webm -c:v libx264 -pix_fmt yuv420p -an mot17_04.mp4
rm *.webm
```

`ffmpeg` is available via `brew install ffmpeg`.

## Other candidate sources

| Source | Notes |
|---|---|
| [MOTChallenge](https://motchallenge.net/) | Pedestrian tracking benchmarks with ground truth. Useful for MOTA/IDF1 evaluation later. |
| [Oxford pedestrian counting](https://www.robots.ox.ac.uk/~vgg/research/counting/index_org.html) | Fixed-camera crowd footage; check the page for access terms. |
| [CrowdHuman](https://www.crowdhuman.org/) | Still images of dense crowds. Useful for fine-tuning, not for video tracking. Non-commercial licence. |
| [COCO](https://cocodataset.org/) | Source of YOLO11's `person` class. Images only. |

Guidelines:
- Start with one small video and do not download full benchmarks unless they are needed.
- Do not use videos without a licence that permits this use (e.g. random YouTube footage).
- Record the source, licence, resolution and FPS of every new video in the table above.
- Queue-specific footage (people standing in line) is still needed for Sprint 2. MOT17 shows walking pedestrians, not queues.
