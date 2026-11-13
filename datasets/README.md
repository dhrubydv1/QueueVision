# Datasets

Raw and processed videos and all annotation files are **not committed to Git**
(see `.gitignore`). Only this README and the empty folder skeleton are tracked.
**No footage of a real deployment exists yet**; MOT17 (walking pedestrians) and the Collective Activity Dataset (short queueing clips, below) are the only data used so far.

```
datasets/
├── raw/          # untouched originals for new acquisitions (see note below)
├── videos/       # derived/converted MP4s for new acquisitions (not committed)
├── frames/       # extracted frames (not committed)
├── processed/    # converted or trimmed copies
├── annotations/  # ground truth (not committed)
│   ├── cvat_original/   # untouched CVAT exports
│   ├── mot_format/      # MOT-format conversions
│   ├── yolo_format/     # YOLO-format conversions
│   ├── queue_events/    # queue-event and waiting-time CSVs
│   ├── cad/  mot17/     # existing Sprint 1-4 annotations
├── splits/       # train.txt / val.txt / test.txt (tracked)
└── metadata/     # manifest, licence register, annotation guidelines (tracked)
```

**Backward compatibility:** the existing MP4s in `raw/` (`cad_seq*.mp4`,
`mot17_*.mp4`) are *derived* files (CAD clips converted from JPEG frames by
`ai_engine/tools/prepare_cad.py`; MOT17 preview WebM re-encoded to MP4). They
stay in `raw/` because configs, docs and tests reference them. For **new**
acquisitions put the untouched original in `raw/` and any derived MP4 in
`videos/`.

## Manifest, licences, splits and validation (Sprint 5)

- `metadata/dataset_manifest.csv`: one row per clip (acquired or planned). The
  planned rows are an acquisition plan only; nothing in them has been downloaded.
- `metadata/dataset_licensing_register.csv`: licence terms per dataset, checked
  against official pages on 2026-11-09 (details and quotes in
  `docs/dataset_licences.md`). MOT17 is explicit (CC BY-NC-SA 3.0). CAD, MIT
  Traffic and CUHK Square state **no terms** (`none stated`, redistribution
  `unknown`): do not redistribute them or derived videos without written
  permission from the authors.
- `splits/*.txt`: no model is trained here; the splits separate parameter tuning
  (`val`) from held-out reporting (`test`). Rule: CAD clips sorted by id, first
  three to `val`, the other four to `test`; MOT17 clips to `test`; `train` is empty.
- `metadata/annotation_guidelines.md`: how to label queue counts, events and
  waiting times.

```bash
python scripts/validate_dataset.py --manifest datasets/metadata/dataset_manifest.csv
```

Checks files, video readability (first and near-last frame), fps, duplicate ids,
licence registration, split consistency and missing annotations, and writes
`outputs/metrics/dataset_validation_report.json`. Planned clips are reported as
`planned_not_acquired`, not errors. Exit code 1 if an acquired clip has an error
or the manifest is structurally invalid.

## Videos used in Sprint 1

| File | Source | Resolution | FPS | Frames | Duration |
|---|---|---|---|---|---|
| `raw/mot17_09.mp4` | MOTChallenge MOT17-09 (pedestrian street, eye level, static camera) | 960×540 | 30 | 525 | 17.5 s |
| `raw/mot17_04.mp4` | MOTChallenge MOT17-04 (busy street at night, high-angle static camera) | 960×540 | 30 | 1050 | 35.0 s |

Both are MOTChallenge's per-sequence preview videos (WebM, about 5 MB each),
re-encoded to MP4 with H.264 locally. The full-resolution frame sequences
were **not** downloaded.

**Licence (re-checked 2026-11-09 on https://motchallenge.net/data/MOT17/; some sequences come from third-party sources whose own conditions may also apply):** MOTChallenge sequences are released under
[Creative Commons Attribution-NonCommercial-ShareAlike 3.0](https://creativecommons.org/licenses/by-nc-sa/3.0/).
They may be used for this academic, non-commercial project with attribution.
Cite: Milan et al., *MOT16: A Benchmark for Multi-Object Tracking*, arXiv:1603.00831.

## Collective Activity Dataset (Sprint 4 demo and queue-length check)

Short clips that include people standing in line; used for the demo and as a
first queue-length check. Nothing from it is committed.

| Item | Value |
|---|---|
| Source | Project page: https://cvgl.stanford.edu/projects/collective/collectiveActivity.html |
| Content | 44 sequences of JPEG frames, 720×480 |
| Annotations | Every 10th frame, one row per person: `frame x y w h action pose`. Actions: 1 NA, 2 crossing, 3 waiting, 4 queueing, 5 walking, 6 talking |
| Person IDs | None, so there is **no waiting-time ground truth**; queue length is available (number of people labelled 4 in an annotated frame) |
| Queueing sequences | 10, 25, 26, 27, 39, 40, 41 |
| Frame rate | **Not stated by the dataset.** `prepare_cad.py` assumes 30 fps (`--fps`); every time value depends on this assumption |
| Git | Never commit its frames or videos (this repository is public); `datasets/raw/` and `datasets/annotations/` are git-ignored |

**Licence / terms.** The project page (checked 2026-11-09) states **no licence
or terms of use**; it only asks users to cite the paper below. Absence of terms
is not permission: keep use local and academic, never redistribute the data or
derived videos, and request permission from the authors for anything further.
See `docs/dataset_licences.md`.

**Citation.** W. Choi, K. Shahid, S. Savarese, "What are they doing?:
Collective activity classification using spatio-temporal relationship among
people", VSWS 2009 (with ICCV).

**Prepare the clips.** Download `ActivityDataset.zip` yourself from the project
page, then from the repository root:

```bash
python ai_engine/tools/prepare_cad.py --source ~/Downloads/ActivityDataset.zip
# or a subset: --sequences 25 27   (default: 10 25 26 27 39 40 41)
# other options: --fps 30 (assumed), --video-dir, --annotation-dir, --force
```

For each sequence it writes `datasets/raw/cad_seqNN.mp4`,
`datasets/annotations/cad/cad_seqNN_queue_counts.csv` (`time_s, frame,
queue_count`, one row per annotated frame, the format `evaluate_queue.py`
reads) and `datasets/annotations/cad/cad_seqNN.json` (records the assumed
fps). Only `seqNN/frameN.jpg` and `seqNN/annotations.txt` are read from the
zip, in memory; the zip is treated as untrusted. It will not overwrite
existing outputs unless `--force` is given.

**Camera configs.** `configs/cameras/cad_seq10.yaml`, `cad_seq25`, `cad_seq26`,
`cad_seq27`, `cad_seq39`, `cad_seq40` and `cad_seq41` hold a queue zone and a
service point per sequence (committed; they contain no images). The cameras
are handheld, so zones have generous margins; seq39 moves the most.

**Demo and check.**

```bash
python -m api        # upload datasets/raw/cad_seq27.mp4 with camera cad_seq27 at http://127.0.0.1:8000
python ai_engine/run_tracking.py --video datasets/raw/cad_seq25.mp4 --camera-config configs/cameras/cad_seq25.yaml
python ai_engine/evaluate_queue.py --predicted outputs/timeseries/cad_seq25_queue.csv \
  --annotations datasets/annotations/cad/cad_seq25_queue_counts.csv
```

Results are in [docs/testing_report.md](../docs/testing_report.md). Limits to
keep in mind: handheld cameras, the dataset's "queueing" label versus this
project's zone-based membership rule, and the fps assumption.

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

## Waiting-time ground truth

Waiting-time accuracy needs a manual record of who waited how long. The
annotation kit (below) writes an empty template next to the queue-count CSV:

```
datasets/annotations/<name>_waits_gt.csv      # header: person,join_s,service_s,abandoned
```

(It is not overwritten if it already exists, unless `--force` is given.)

**How to fill it.** Watch the video in a player and write one row per person
who joins the queue, using the player's video time in seconds:

| Column | Fill with |
|---|---|
| `person` | Any label you choose for that person (1, 2, 3, ... or a short description); it is not compared with anything |
| `join_s` | Time the person joins the back of the queue |
| `service_s` | Time the person reaches the service point (the same place as the configured service line or zone); leave blank if they were never served |
| `abandoned` | `true` if they left without being served, otherwise `false` (blank counts as `false`; `1`/`0` and `yes`/`no` also work) |

Skip people who only walk past. Use video time, not wall-clock time, and the
same convention for every person (for example, the moment the foot reaches the
back of the queue). Fully blank rows are ignored; an invalid row stops the
evaluation with its row number.

**Evaluate.**

```bash
python ai_engine/run_tracking.py --video datasets/raw/clip.mp4 --camera-config configs/cameras/clip.yaml
python ai_engine/evaluate_waiting.py \
  --predicted-waits outputs/events/clip_waits.csv \
  --ground-truth datasets/annotations/clip_waits_gt.csv \
  --timeseries outputs/timeseries/clip_queue.csv
```

`--tolerance` (default 3.0 s) is the largest difference between predicted and
true service time for two records to count as the same person. `--duration`
and `--output` are optional. The report is written to
`outputs/metrics/clip_wait_eval.json` by default.

**How much footage.** Aim for at least 3 to 5 clips of 5 to 15 minutes each,
at least 100 completed waits in total, and a range of queue lengths (quiet,
normal and busy). Tune settings on some clips and confirm on others. The
camera's service point must lie inside (or at the edge of) the queue zone,
otherwise people are recorded as abandoned.

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

This writes `datasets/annotations/clip_frames/`,
`datasets/annotations/clip_queue_counts.csv` (`time_s, frame, queue_count`,
with `queue_count` empty) and the waiting-time template
`datasets/annotations/clip_waits_gt.csv` (see above). Look at each frame and enter the number of people
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

## Reserved clip-id suffix `_observed`

`ai_engine/evaluate_waiting.py` writes `<name>_observed_wait_eval.json` when the
ground truth uses the observed-wait format (person-measured
`observed_wait_seconds`), and `scripts/export_accuracy.py` reads
`outputs/metrics/*_wait_eval.json` for the Accuracy page. A file name ending in
`_observed_wait_eval.json` is reported as kind **observed** for the clip whose
id is the name with `_observed` removed; every other `*_wait_eval.json` is kind
**predicted**. So `_observed` is a reserved suffix: never end a real clip id in
`_observed`, or its predicted evaluation would be filed under a shortened clip
id as observed.
