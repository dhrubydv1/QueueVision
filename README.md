# QueueVision — Milestone 1

This milestone detects people in a local image or video with Ultralytics YOLO.
It draws a box around each detected person, labels the confidence, displays the
current person count on every frame, and saves the annotated result.

Queue-area detection, tracking, waiting-time estimation, databases, and a web
dashboard are intentionally not included yet.

## Setup

Activate the existing virtual environment and install the dependencies:

```bash
source venv/bin/activate
python -m pip install -r requirements.txt
```

Place an image or video in `media/`, then run:

```bash
python person_detection.py media/your_file.jpg
```

For a video, use its filename in the same way:

```bash
python person_detection.py media/your_video.mp4
```

The annotated file is saved in `outputs/`. Press any key to close an image
preview, or press `q` to stop a video preview early. On a server or other
computer without a desktop display, add `--no-display`:

```bash
python person_detection.py media/your_file.jpg --no-display
```

The default `yolo11n.pt` model downloads automatically on the first run.
