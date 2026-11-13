import { describe, expect, it } from "vitest";
import { type QueueEvent, eventKey } from "./live-types";

const ev = (over: Partial<QueueEvent> = {}): QueueEvent => ({
  schema_version: "1.0", event_type: "congestion_start", timestamp_s: 5, frame_index: 150, ...over,
});

describe("eventKey", () => {
  it("is identical for the same event delivered twice", () => {
    expect(eventKey(ev({ person_key: "p0001", track_ids: [3, 7], detected_s: 6 }))).toBe(
      eventKey(JSON.parse(JSON.stringify(ev({ person_key: "p0001", track_ids: [3, 7], detected_s: 6 })))),
    );
  });
  it("separates events differing only by frame_index", () => {
    expect(eventKey(ev({ frame_index: 150 }))).not.toBe(eventKey(ev({ frame_index: 151 })));
  });
  it("separates events differing only by track_ids", () => {
    expect(eventKey(ev({ track_ids: [1, 2] }))).not.toBe(eventKey(ev({ track_ids: [1, 3] })));
    expect(eventKey(ev({ track_ids: [1, 2] }))).not.toBe(eventKey(ev({ track_ids: [1] })));
  });
  it("separates events differing only by person_key or track_id", () => {
    expect(eventKey(ev({ person_key: "p0001" }))).not.toBe(eventKey(ev({ person_key: "p0002" })));
    expect(eventKey(ev({ track_id: 4 }))).not.toBe(eventKey(ev({ track_id: 5 })));
  });
  it("does not confuse person_key with track_id", () => {
    expect(eventKey(ev({ person_key: "4" }))).not.toBe(eventKey(ev({ track_id: 4 })));
  });
});
