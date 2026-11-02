# Datasets

Raw and processed videos and all annotation files are **not committed to Git**
(see `.gitignore`). Only this README and the empty folder skeleton are tracked.
**No real queue footage exists yet**; only MOT17 walking pedestrians are available.

```
datasets/
├── raw/          # original downloaded videos (MP4/AVI/MOV)
├── processed/    # converted or trimmed copies
└── annotations/  # ground truth: MOT17 labels, manual queue counts, annotation frames
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

## MOT17 ground truth (tracking evaluation)

Used by `ai_engine/evaluate_tracking.py` to score tracking accuracy.

| Item | Value |
|---|---|
| Source | https://motchallenge.net/data/MOT17Labels.zip (labels only, about 10 MB) |
| SHA-256 | `0aa79322e91583369f42f17c4d79a0b145380d8732487bba59272048dc82b2b9` |
| Extracted | Only `train/MOT17-04-FRCNN/{seqinfo.ini,gt/gt.txt}` and `train/MOT17-09-FRCNN/{seqinfo.ini,gt/gt.txt}` |
| Licence | CC BY-NC-SA 3.0 (same as the videos) |
| Git | Ignored (`datasets/annotations/*`); reproduce with the commands below |

Layout after extraction:

```
datasets/annotations/mot17/
├── MOT17-04/
│   ├── seqinfo.ini
│   └── gt/gt.txt
└── MOT17-09/
    ├── seqinfo.ini
    └── gt/gt.txt
```

The ground truth is for the 1920x1080 sequences; the evaluator scales it to
the 960x540 preview videos and refuses to run if the frame count differs from
`seqLength` in `seqinfo.ini`.

```bash
mkdir -p datasets/annotations/mot17 && cd datasets/annotations/mot17
curl -fL -o MOT17Labels.zip https://motchallenge.net/data/MOT17Labels.zip
shasum -a 256 MOT17Labels.zip        # must print the SHA-256 above
for n in 04 09; do
  mkdir -p MOT17-$n/gt
  unzip -p MOT17Labels.zip train/MOT17-$n-FRCNN/seqinfo.ini > MOT17-$n/seqinfo.ini
  unzip -p MOT17Labels.zip train/MOT17-$n-FRCNN/gt/gt.txt   > MOT17-$n/gt/gt.txt
done
rm MOT17Labels.zip
```

Then, from the repository root:

```bash
python ai_engine/run_tracking.py --video datasets/raw/mot17_09.mp4 --save-mot
python ai_engine/evaluate_tracking.py --tracks outputs/tracks/mot17_09_mot.txt \
  --sequence-dir datasets/annotations/mot17/MOT17-09 --video datasets/raw/mot17_09.mp4
```

Results are in [docs/testing_report.md](../docs/testing_report.md).

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
- Queue-specific footage (people standing in line) is still needed. MOT17 shows walking pedestrians, not queues.

## Recording queue footage

Real queue footage is needed to measure queue-length accuracy. Read
[docs/privacy.md](../docs/privacy.md) first, and never commit footage.

**Setup**

* A **fixed camera** on a tripod or mount; do not pan, zoom or move it.
* A **high mounting angle** (looking down at the queue) to reduce people hiding each other.
* The **whole queue visible**, including the **service point** (counter, till, door) at its head.
* **720p or 1080p** at **25-30 fps**.
* **5-15 minutes** per clip, covering quiet and busy moments if possible.
* Written **permission from the location** and visible **signage** (see the privacy page).
* Note the **date, place and conditions** (time of day, lighting, crowd level, camera height) for each clip.

**Prepare and label**

```bash
# 1. put the clip in datasets/raw/ (convert to H.264 MP4 if needed)
# 2. draw the queue zone
python ai_engine/tools/draw_zone.py --video datasets/raw/clip.mp4 --config configs/cameras/clip.yaml
# 3. extract a frame every 5 s with the zone drawn, plus a CSV template
python ai_engine/tools/make_annotation_kit.py --video datasets/raw/clip.mp4 \
  --camera-config configs/cameras/clip.yaml --every 5
```

This writes `datasets/annotations/clip_frames/` and
`datasets/annotations/clip_queue_counts.csv` (`time_s, frame, queue_count`,
with `queue_count` empty). Look at each frame and enter the number of people
who are queuing (not passers-by). The tool will not overwrite the CSV unless
you pass `--force`. Leave a row blank to skip it.

**Evaluate**

```bash
python ai_engine/run_tracking.py --video datasets/raw/clip.mp4 --camera-config configs/cameras/clip.yaml
python ai_engine/evaluate_queue.py --predicted outputs/timeseries/clip_queue.csv \
  --annotations datasets/annotations/clip_queue_counts.csv
```

The report (`outputs/metrics/clip_queue_eval.json`) gives MAE, RMSE, bias and
the exact-match and within-one rates. Tune the membership settings in the
camera YAML on one clip and confirm on another so the numbers are not fitted
to the test data.
