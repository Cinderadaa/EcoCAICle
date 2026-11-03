# Live kiosk camera pilot checklist

## Status

The MediaPipe detector has been removed from the active frontend. Do not run
this detector pilot with the current build. Keep these cases as the validation
checklist if a detector is proposed again; it must remain non-blocking until a
separate review approves any change to capture behavior.

## Detector mode

MediaPipe diagnostics are not present in the current frontend build. The cases
below are retained only for a future detector evaluation.

## Required live cases

Run each case through the deployed kiosk camera with the real item placed in its
normal scanning position. Repeat each case at least five times, including
different positions and lighting where practical.

| Case | Trials | Captured | Prevented | Detector labels/scores | Box alignment | Qwen prediction / correct? | Captured image shows real waste? | False blocks |
|---|---:|---:|---:|---|---|---|---|---:|
| Valid object centered | | | | | | | | |
| Valid object held by hand | | | | | | | | |
| Hand only | | | | | | | | |
| Person only | | | | | | | | |
| Empty frame | | | | | | | | |
| Paper | | | | | | | | |
| Cardboard | | | | | | | | |
| Plastic | | | | | | | | |
| Glass | | | | | | | | |
| Metal | | | | | | | | |
| E-waste | | | | | | | | |

For every trial record the date/time, item or case ID, ground-truth category,
camera lighting/position, capture outcome, whether the captured frame actually
shows the waste item, detector labels/scores and whether each box is aligned to
the item, Qwen's prediction and whether it is correct, and any reason the camera
flow prevented capture. Count a **false block** whenever real waste is present
but capture was prevented. Keep false blocks separate from detector misses,
because diagnostics do not control capture.

Do not enable detector-based capture approval or rejection based on these
observations alone. Review the false-block rate by waste category and the
hand-held-object cases first; require a separate explicit change and review
before introducing any detector gate.
