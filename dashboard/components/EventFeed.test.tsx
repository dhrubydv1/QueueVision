import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { EventFeed } from "./EventFeed";

const ev = (event_type: string, t: number) => ({ schema_version: "1.0", event_type, timestamp_s: t, frame_index: t });

describe("EventFeed", () => {
  it("maps event types to labels and ignores unknown types", () => {
    render(
      <EventFeed
        events={[
          ev("queue_join", 1), ev("service_start", 2), ev("service_complete", 3), ev("queue_abandon", 4),
          ev("queue_exit", 5), ev("zone_exit", 6), ev("mystery_event", 7),
        ]}
      />,
    );
    expect(screen.getByText("Joined the queue")).toBeInTheDocument();
    expect(screen.getByText("Service started")).toBeInTheDocument();
    expect(screen.getByText("Service completed")).toBeInTheDocument();
    expect(screen.getByText("Left without service")).toBeInTheDocument();
    expect(screen.getAllByText("Left the area")).toHaveLength(2);
    expect(screen.getAllByRole("listitem")).toHaveLength(6);
  });
});
