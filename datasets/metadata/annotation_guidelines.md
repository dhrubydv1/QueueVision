# Annotation guidelines

Practical rules for people labelling QueueVision clips. Follow them exactly so
that two annotators produce comparable ground truth.

## 1. Who is "in the queue"

Count a person as in the queue only if all of these hold:

- they are standing or waiting in the line that leads to a service point;
- they are oriented toward the service point or the person ahead of them;
- they are not just passing through (passers-by, people crossing the line) and
  are not staff.

The person currently being served at the counter is **not** waiting and is not
counted. When you cannot tell, do not count the person and give the annotation
`annotation_confidence` of `low` where a waiting-time row is affected.

## 2. Queue events

| Event | Define it as | Notes |
|---|---|---|
| `queue_join` | The person enters the actual waiting line (joins the end of it). | Not when they first appear in the frame. |
| `service_start` | The person reaches the counter or service point. | Blank for people who abandon. |
| `service_complete` | Service is visibly finished: the person leaves the counter after being served. | Never infer it from the person disappearing from view. If it is not visible, leave blank. |
| `queue_abandon` | The person leaves the line before service starts. | |
| `queue_exit` | The person leaves the monitored area. | Records the time only; it says nothing about whether service occurred. |

## 3. File formats

### 3.1 Queue-count CSV (already in use)

`time_s,frame,queue_count`, one row per sampled frame, for example
`datasets/annotations/cad/cad_seq10_queue_counts.csv`. `queue_count` is the
number of people who are in the queue (section 1) at that frame. Leave a row
blank to skip it. The reader is `ai_engine/evaluation/queue_eval.py`.

### 3.2 Waiting-time ground truth CSV (new)

One row per person:

```
clip_id,track_id,queue_join_seconds,service_start_seconds,service_complete_seconds,observed_wait_seconds,annotation_confidence
```

- Times are video seconds counted from frame 0, at the clip's real fps (or the
  assumed fps recorded in the manifest, `fps_assumed=true`).
- `service_start_seconds` is blank for people who abandon.
- `observed_wait_seconds = service_start_seconds - queue_join_seconds`.
- `annotation_confidence` is one of `high`, `medium`, `low`.
- `service_complete_seconds` is blank when the end of service is not visible.

### 3.3 Tracking ground truth (MOT format)

`frame,id,x,y,w,h,conf,class,visibility`, comma separated, **1-based** frame
numbers, one row per box.

## 4. Folder layout and tools

- CVAT exports go **untouched** into `datasets/annotations/cvat_original/`.
- Conversions go into `datasets/annotations/mot_format/` and
  `datasets/annotations/yolo_format/`; queue-event and waiting-time CSVs go
  into `datasets/annotations/queue_events/`.
- Annotation files are not committed to Git (see `.gitignore`). Register every
  clip in `datasets/metadata/dataset_manifest.csv`.

## 5. Sampling rate

Label queue counts every 5 seconds of video (the default of
`ai_engine/tools/make_annotation_kit.py --every 5`). Label events at the exact
frame they occur and convert to seconds.

## 6. Quality control

Double-annotate at least 10% of clips with a second annotator working
independently. Report the agreement (for example mean absolute difference in
queue count, and difference in observed wait per person) next to the results.
Disagreements are discussed, not silently overwritten; keep both files.

## 7. Privacy

Do not write faces, names or any identifying details in annotation notes or
file names; refer to people only by `track_id`. See `docs/privacy.md`.
