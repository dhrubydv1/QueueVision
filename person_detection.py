"""Detect people in a local image or video with Ultralytics YOLO."""

from argparse import ArgumentParser
from pathlib import Path

import cv2
from ultralytics import YOLO


# File types supported by this small milestone.
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".m4v"}


def draw_people(frame, result) -> int:
    """Draw every detected person and return the number of people."""
    person_count = len(result.boxes)

    for box in result.boxes:
        # xyxy contains the top-left and bottom-right box coordinates.
        x1, y1, x2, y2 = map(int, box.xyxy[0].tolist())
        confidence = float(box.conf[0])

        cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 200, 0), 2)
        cv2.putText(
            frame,
            f"Person {confidence:.2f}",
            (x1, max(y1 - 10, 20)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            (0, 200, 0),
            2,
        )

    # A filled background keeps the total readable on light and dark frames.
    label = f"People detected: {person_count}"
    cv2.rectangle(frame, (10, 10), (310, 50), (0, 0, 0), -1)
    cv2.putText(
        frame,
        label,
        (20, 38),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.8,
        (255, 255, 255),
        2,
    )
    return person_count


def detect_image(model, input_path: Path, output_path: Path, display: bool) -> None:
    """Detect and annotate people in one image."""
    frame = cv2.imread(str(input_path))
    if frame is None:
        raise ValueError(f"OpenCV could not read the image: {input_path}")

    # COCO class 0 is "person", so other object classes are ignored.
    result = model.predict(frame, classes=[0], verbose=False)[0]
    count = draw_people(frame, result)

    if not cv2.imwrite(str(output_path), frame):
        raise RuntimeError(f"Could not save the output image: {output_path}")

    print(f"People detected: {count}")
    print(f"Saved result to: {output_path}")

    if display:
        cv2.imshow("QueueVision - press any key to close", frame)
        cv2.waitKey(0)
        cv2.destroyAllWindows()


def detect_video(model, input_path: Path, output_path: Path, display: bool) -> None:
    """Detect and annotate people in every frame of a video."""
    video = cv2.VideoCapture(str(input_path))
    if not video.isOpened():
        raise ValueError(f"OpenCV could not read the video: {input_path}")

    width = int(video.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(video.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = video.get(cv2.CAP_PROP_FPS) or 30.0
    writer = cv2.VideoWriter(
        str(output_path),
        cv2.VideoWriter_fourcc(*"mp4v"),
        fps,
        (width, height),
    )

    if not writer.isOpened():
        video.release()
        raise RuntimeError(f"Could not create the output video: {output_path}")

    frame_number = 0
    try:
        while True:
            success, frame = video.read()
            if not success:
                break

            result = model.predict(frame, classes=[0], verbose=False)[0]
            count = draw_people(frame, result)
            writer.write(frame)
            frame_number += 1

            print(f"\rFrame {frame_number}: {count} people", end="", flush=True)

            if display:
                cv2.imshow("QueueVision - press q to stop", frame)
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    break
    finally:
        video.release()
        writer.release()
        cv2.destroyAllWindows()

    print(f"\nSaved result to: {output_path}")


def parse_arguments():
    """Read command-line options."""
    parser = ArgumentParser(description="Detect people in an image or video.")
    parser.add_argument("input", type=Path, help="Path to a local image or video")
    parser.add_argument(
        "--model",
        default="yolo11n.pt",
        help="YOLO model to use (default: yolo11n.pt)",
    )
    parser.add_argument(
        "--no-display",
        action="store_true",
        help="Save the result without opening a preview window",
    )
    return parser.parse_args()


def main() -> None:
    """Validate the input, load YOLO, and run the correct detector."""
    args = parse_arguments()
    input_path = args.input.expanduser().resolve()

    if not input_path.is_file():
        raise FileNotFoundError(f"Input file does not exist: {input_path}")

    extension = input_path.suffix.lower()
    if extension not in IMAGE_EXTENSIONS | VIDEO_EXTENSIONS:
        supported = ", ".join(sorted(IMAGE_EXTENSIONS | VIDEO_EXTENSIONS))
        raise ValueError(f"Unsupported file type '{extension}'. Use one of: {supported}")

    output_directory = Path("outputs")
    output_directory.mkdir(exist_ok=True)
    output_extension = extension if extension in IMAGE_EXTENSIONS else ".mp4"
    output_path = output_directory / f"{input_path.stem}_detected{output_extension}"

    # The small nano model is quick to run and downloads automatically once.
    model = YOLO(args.model)
    display = not args.no_display

    if extension in IMAGE_EXTENSIONS:
        detect_image(model, input_path, output_path, display)
    else:
        detect_video(model, input_path, output_path, display)


if __name__ == "__main__":
    main()
