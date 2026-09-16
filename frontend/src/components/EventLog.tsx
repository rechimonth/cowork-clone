import { useEffect, useRef } from "react";

import type { AgentEvent } from "../api/types";

interface Props {
  events: AgentEvent[];
}

/** Bitacora en vivo del stream WebSocket, con autoscroll al ultimo evento. */
export function EventLog({ events }: Props) {
  const endRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [events.length]);

  return (
    <section className="panel">
      <h2>Actividad</h2>
      {events.length === 0 ? (
        <p className="muted">Sin eventos todavia.</p>
      ) : (
        <div className="log" role="log" aria-live="polite">
          {events.map((event, index) => (
            // seq puede repetirse en el replay del WS, asi que se combina con el indice.
            <div key={`${event.seq}-${index}`} className={`log-line log-${event.type}`}>
              <span className="mono seq">{event.seq}</span>
              <span className="type">{event.type}</span>
              <span>{event.message}</span>
            </div>
          ))}
          <div ref={endRef} />
        </div>
      )}
    </section>
  );
}