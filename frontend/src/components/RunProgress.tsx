"use client";

import type { RunEvent } from "@/lib/types";

export default function RunProgress({ events }: { events: RunEvent[] }) {
  if (events.length === 0) return null;
  const last = events[events.length - 1];
  return (
    <div style={{ padding: "10px 16px" }}>
      <div className="eventlog">
        {events.map((event, index) => (
          <div key={index} className={event.level}>
            [{event.stage}] {event.message}
          </div>
        ))}
      </div>
      {last.progress != null && last.stage !== "done" && (
        <div
          style={{
            height: 4,
            background: "var(--panel-2)",
            borderRadius: 2,
            marginTop: 6,
          }}
        >
          <div
            style={{
              width: `${Math.round(last.progress * 100)}%`,
              height: "100%",
              background: "var(--accent)",
              borderRadius: 2,
              transition: "width 0.3s",
            }}
          />
        </div>
      )}
    </div>
  );
}
