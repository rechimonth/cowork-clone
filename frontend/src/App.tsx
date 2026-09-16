import { useEffect, useMemo, useRef, useState } from "react";

import { ApiError, CoworkClient } from "./api/client";
import type { AgentEvent, SessionView } from "./api/types";
import { ConnectionPanel } from "./components/ConnectionPanel";
import { EventLog } from "./components/EventLog";
import { PlanReview } from "./components/PlanReview";

const MAX_EVENTS = 500;

export function App() {
  const [client, setClient] = useState<CoworkClient | null>(null);
  const [session, setSession] = useState<SessionView | null>(null);
  const [events, setEvents] = useState<AgentEvent[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const socketRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    return () => {
      socketRef.current?.close();
    };
  }, []);

  const pushEvent = (event: AgentEvent) => {
    setEvents((previous) => [...previous, event].slice(-MAX_EVENTS));
  };

  /** Refresca el estado; el stream avisa de cambios, pero el estado es la verdad. */
  const refresh = async (active: CoworkClient, sessionId: string) => {
    try {
      setSession(await active.getSession(sessionId));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  const handleConnect = (baseUrl: string, token: string) => {
    setError(null);
    setEvents([]);
    setSession(null);
    setClient(new CoworkClient(baseUrl, token));
  };

  const handleStart = async (rootDir: string, dryRun: boolean) => {
    if (!client) return;
    setBusy(true);
    setError(null);
    setEvents([]);
    try {
      const created = await client.createSession({ root_dir: rootDir, dry_run: dryRun });
      setSession(created);

      socketRef.current?.close();
      const socket = client.openEvents(created.session_id, (event) => {
        pushEvent(event);
        // Los eventos de estado son la senal para volver a leer la sesion.
        if (event.type === "state" || event.type === "plan" || event.type === "error") {
          void refresh(client, created.session_id);
        }
      });
      socket.addEventListener("error", () => {
        setError("Se perdio la conexion con el stream de eventos");
      });
      socketRef.current = socket;
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const handleDecision = async (decision: "approve" | "reject") => {
    if (!client || !session) return;
    setBusy(true);
    setError(null);
    try {
      await client.decide(session.session_id, decision);
      await refresh(client, session.session_id);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const canDecide = session?.state === "awaiting_approval" && !busy;

  const statusLabel = useMemo(() => {
    if (!session) return "Sin sesion";
    return `Estado: ${session.state}`;
  }, [session]);

  return (
    <div className="app">
      <header>
        <h1>cowork-clone</h1>
        <p className="subtitle">
          Escanea, propone un plan y espera tu aprobacion. Nada se ejecuta sin ella.
        </p>
      </header>

      <ConnectionPanel
        connected={client !== null}
        busy={busy}
        onConnect={handleConnect}
        onStart={handleStart}
      />

      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}

      <section className="status">
        <strong>{statusLabel}</strong>
        {session && (
          <span className="muted">
            {session.file_count} archivo(s) · {session.event_count} evento(s)
            {session.dry_run ? " · simulacion" : ""}
          </span>
        )}
      </section>

      {session?.error && <p className="error">Fallo de la sesion: {session.error}</p>}

      <PlanReview session={session} canDecide={canDecide} onDecide={handleDecision} />

      <EventLog events={events} />
    </div>
  );
}