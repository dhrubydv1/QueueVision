# QueueVision demo script (about 5 minutes)

Every step below uses only features that are covered by an automated test or
listed in [testing_report.md](testing_report.md). The text in quotes is the text
on screen (taken from the dashboard components).

**Status of the real-browser check: PENDING.** The steps were exercised by the
automated Playwright tests (Chromium and WebKit; the last run, 2026-11-13,
passed 14/14, 7 per browser, see [testing_report.md](testing_report.md)). No
person has yet walked through this script in real Chrome, real Safari or on a
phone; the checklist for that is at the top of the testing report. Run this
script once yourself before presenting it, and note anything that differs.

## Before the audience arrives

1. From the repository root run `scripts/demo.sh`. It builds the dashboard if
   `dashboard/out` is missing, exports the accuracy data and starts the API on
   `http://127.0.0.1:8000` (Ctrl+C stops it).
2. Open `http://127.0.0.1:8000`.
3. Have a clip ready. **Use `datasets/raw/cad_seq27.mp4` with the camera
   `cad_seq27`.** A congestion alert appears reliably only on this clip: in the
   Sprint 8 end-to-end test `cad_seq27` produced a `congestion_start` about 17 s
   into the video, and `cad_seq25` produced none. If you use `cad_seq25` the
   Acknowledge step cannot be shown. The clips are not committed to Git
   (no licence is stated for them), so they must already be on the machine.

## 1. Overview (30 s)

Open `/`. The audience sees the "Overview" page: under the title a full date
and "N runs processing" (or "1 run processing"), the lists "Latest completed
runs" and "Recent recorded runs", and a card with the accuracy summary (or a
message that the accuracy data is not generated). Say: everything on screen
comes from the API; heads are blurred in every image the dashboard shows.

## 2. Upload with real time on (30 s)

Open the Runs page. The audience sees the upload form: "Video file", "Camera
config", "Every Nth frame", "Blur heads", "Process in real time" and the
"Start" button. Choose `cad_seq27.mp4`, camera `cad_seq27`, keep "Blur heads"
on, tick "Process in real time" and press "Start". Because real time is on, the
page opens on the Live page (`/live?id=N`). Without real time the page would
open on the recorded run instead.

## 3. Live page (2 minutes)

The audience sees the video preview with **heads blurred**, and the figures
"Queue length", "Predicted wait", "Service rate" and "People in frame" update
while the run is paced to the video's own speed. The queue chart and the Events
list grow, and "Last update HH:MM:SS" appears. Point out that "Predicted wait"
may read Unknown or Insufficient data early on: the engine says so instead of
guessing.

Around 17 s into the video (video time; the run is paced, so about 17 s of real
time) a red "Congestion alert" banner appears with its reasons, and the chip
reads "Live · congested". Under the button the note reads "Acknowledging
records the time for this alert." Press **Acknowledge**: the banner goes away.
Reload the page: the banner stays hidden for that alert, because the
acknowledgement was saved on the server (it is no longer stored only in the
browser). If you want to show Cancel, "Cancel run" stops the run and the chip
becomes Cancelled, but let this run finish if you want step 4 to show the
acknowledgement.

If the alert has not appeared yet, wait; do not skip ahead. If it never
appears, you are probably not on `cad_seq27`.

## 4. Recorded run (1 minute)

When the run finishes, use "Open the recorded run" (or the run in "Recent
recorded runs" on the Overview). The audience sees the status chip, the summary
cards, the queue chart and the video with blurred heads, played with the
browser's plain video control (press play, drag the scrubber; there is no
click-to-seek from the chart). Three tabs: **Waits**, **Events** and **Alerts**;
switch them by click or with the Left and Right arrow keys. In Alerts the rows
read "Congestion started" / "Congestion ended", and the alert you acknowledged
shows "Acknowledged HH:MM:SS NPT" (NPT is Nepal Time; the time is shown in that
zone regardless of the browser). "Report (JSON)" and "Time series (CSV)" are
download links.

## 5. Accuracy (1 minute)

Open the Accuracy page. The audience sees headline cards (mean absolute error,
bias, within one person) with their frame counts, and the split buttons "All",
"Validation" and "Test", which change the count-error charts. A section whose
data file is missing reads "Not available" with the reason and path, never a
zero. The Waiting time card reads "Measured" with the number of recorded clips,
or "Not measured yet": the waiting-time ground truth is not filled in yet, so
expect "Not measured yet" unless that has been done. Finish on "Limits of these
numbers": say what the numbers do not show (CAD is public queueing footage, not
the target deployment, and no model was trained, so the splits separate tuning
from held-out reporting).

## Limits to state out loud

- One queue zone and one service point per camera; two counters need two camera
  configs.
- There is no login: the API binds to 127.0.0.1 only. Do not demo over a
  network without reading the phone section of the testing report.
- Real-time pacing at stride 1 never waits on this machine (processing is about
  as slow as the video), see the pacing section of the testing report.
- Not shown, because not built yet: live cameras, history pages, notifications.

## After the demo

Record what did not match this script in the Notes column of the browser
checklist in [testing_report.md](testing_report.md).
