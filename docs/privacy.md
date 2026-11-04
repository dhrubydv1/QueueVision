# Privacy

QueueVision processes video of people. This page says what the system keeps,
what it does not, and what you must do before pointing a camera at anyone.

## Before you record

* Get **written permission** from the owner or operator of the location.
* Display **clear signage** at the entrance to the filmed area saying that
  video is being recorded, for what purpose (a queue-measurement study), who
  is responsible, and how to ask questions or request removal.
* Where the law or your institution requires it (for example an ethics
  review or data-protection rules in your country), complete that process
  first. This document is not legal advice.
* Aim the camera at the queue and floor area, not at faces, screens or
  private spaces; a high mounting angle helps.
* Record date, place and conditions, but not the names of people.

## What the system stores

| Item | Content | Personal data risk |
|---|---|---|
| Annotated video (`outputs/videos/`) | The original frames with boxes and IDs drawn on them | **Yes**: people are visible unless heads are blurred |
| Annotated video with `--blur-heads` | Same, with the top fraction of every person box blurred | Reduced, not removed (see limitations) |
| Track IDs | Temporary per-video integers from ByteTrack; they restart at 1 for each video and change after occlusion | Not an identity |
| MOT track file (`outputs/tracks/`) | Frame number, track ID, box coordinates, score | Low: boxes and numbers only |
| Queue CSVs (`outputs/timeseries/`, `outputs/events/`) | Times, counts, track IDs, zone entry times, wait durations | Low: no images |
| Event log and waits (`outputs/events/<name>_events.jsonl`, `<name>_waits.csv`, `<name>_alerts.csv`) | Event times, anonymous `person_key`, track IDs, wait durations, queue lengths | Low: no identities, no images |
| Metrics JSON (`outputs/metrics/`) | Counts, timings, settings, file paths | Low |
| Annotation frames (`datasets/annotations/<name>_frames/`) | Still images from the footage | **Yes** |

Events and waits contain **no identities**. `person_key` (`p0001`, `p0002`,
...) is an anonymous counter that restarts for every video; it is only used to
tie together the tracks of one person inside one video (ID-switch stitching)
and cannot be linked to a real person or to another video. **No images** are
stored in events, waits or alerts. See [event_schema.md](event_schema.md).

The system does **not** save face crops, cropped person images or appearance
features (no re-identification model is used), and it does not identify or
recognise individuals. It sees a person as a box and a short-lived number.

## Retention

Suggested policy for real footage:

1. Keep raw footage only as long as needed to label and evaluate it.
2. Delete raw footage, annotation frames and annotated videos once the
   annotation and evaluation are complete.
3. Keep only aggregated results: metrics JSON, queue CSVs and evaluation
   reports (no images).
4. If a clip must be shown (report, demo), use a `--blur-heads` render and
   check it frame by frame first.

## Limits of head blur

`--blur-heads` blurs the top `--head-fraction` (default 0.2) of each person
box in the saved video. It uses no face model, so it is best-effort:

* If a person is missed by the detector, nothing is blurred.
* Boxes that are cropped, loose or tilted (bending, sitting, carrying a
  child) may not cover the head.
* The blur applies to the saved video only, not to the live `--show` window.
* A fixed fraction cannot follow every height or camera angle. Tune it and
  review the output.
* Clothing, gait, body shape and location remain visible.

Treat blurred video as lower risk, not as anonymous.

## Public repository warning

The GitHub repository is public. **Never commit footage**, frames,
annotated videos, exported crops or the filled annotation CSVs of real
people. `.gitignore` excludes `datasets/raw/`, `datasets/processed/`,
`datasets/annotations/` and `outputs/`, but check `git status` before every
commit. If footage is committed by mistake, treat it as published: remove it
from history and tell the people responsible for the location.

## Third-party datasets

The MOT17 videos and labels used for development are public benchmark data
under CC BY-NC-SA 3.0; see [datasets/README.md](../datasets/README.md).
