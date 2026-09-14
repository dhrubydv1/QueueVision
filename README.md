# QueueVision — Milestone 2

QueueVision uses Ultralytics YOLO and OpenCV to detect people in a local image
or video. Milestone 2 adds a predefined queue zone and counts only the people
whose bounding-box center lies inside that zone.

The annotated result shows:

- an orange queue-zone polygon;
- green boxes and `IN QUEUE` labels for people inside the zone;
- red boxes and `OUTSIDE` labels for people outside the zone;
- the total number of detected people and the number inside the queue.

Tracking, unique IDs, waiting-time estimation, databases, and a web dashboard
are intentionally not included yet.

## Setup

Activate the existing virtual environment and install the dependencies:

```bash
source venv/bin/activate
python -m pip install -r requirements.txt
```

## Run

Place an image or video in `media/`, then run:

```bash
python person_detection.py media/your_file.jpg
```

For a video, use its filename in the same way:

```bash
python person_detection.py media/your_video.mp4
```

The annotated file is saved in `outputs/`. Press any key to close an image
preview, or press `q` to stop a video preview early. On a computer without a
desktop display, add `--no-display`:

```bash
python person_detection.py media/your_file.jpg --no-display
```

The default `yolo11n.pt` model downloads automatically on the first run.

## Queue-zone definition

The queue zone is the `QUEUE_ZONE_POINTS` polygon near the top of
`person_detection.py`. Its coordinates are fractions from `0.0` to `1.0`, so
the polygon scales to any input resolution. For example, `(0.28, 0.35)` means
28% across the frame and 35% down the frame.

Edit those four points to fit the queue area seen by a fixed camera. A detected
person is counted in the queue when the center point of their bounding box is
inside or on the edge of the polygon.
