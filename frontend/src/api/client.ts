/**
 * Cliente HTTP y WebSocket del backend cowork-clone.
 *
 * El token vive solo en memoria (estado de React). No se persiste en
 * localStorage ni en cookies: en un webview de Tauri eso dejaria el secreto
 * accesible a cualquier XSS y sobreviviendo al cierre de la app. Se pide al
 * arrancar y se pierde al cerrar.
 */

import type {
  AgentEvent,
  ApprovalResponse,
  CreateSessionRequest,
  HealthResponse,
  SessionView,
} from "./types";

export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

export class CoworkClient {
  private readonly baseUrl: string;
  private readonly token: string;

  constructor(baseUrl: string, token: string) {
    this.baseUrl = baseUrl.replace(/\/+$/, "");
    this.token = token;
  }

  private async request<T>(path: string, init: RequestInit = {}): Promise<T> {
    const response = await fetch(`${this.baseUrl}${path}`, {
      ...init,
      headers: {
        ...(init.headers ?? {}),
        Authorization: `Bearer ${this.token}`,
        "Content-Type": "application/json",
      },
    });

    if (!response.ok) {
      throw new ApiError(response.status, await this.extractError(response));
    }
    if (response.status === 204) {
      return undefined as T;
    }
    return (await response.json()) as T;
  }

  /** El backend devuelve `{"detail": "..."}`; si no, se usa el texto crudo. */
  private async extractError(response: Response): Promise<string> {
    try {
      const body = (await response.json()) as { detail?: unknown };
      if (typeof body.detail === "string") {
        return body.detail;
      }
    } catch {
      // El cuerpo no era JSON; se cae al texto plano de abajo.
    }
    return `Error ${response.status}: ${response.statusText}`;
  }

  async health(): Promise<HealthResponse> {
    const response = await fetch(`${this.baseUrl}/health`);
    if (!response.ok) {
      throw new ApiError(response.status, "El backend no responde");
    }
    return (await response.json()) as HealthResponse;
  }

  listSessions(): Promise<{ sessions: SessionView[] }> {
    return this.request<{ sessions: SessionView[] }>("/sessions");
  }

  getSession(sessionId: string): Promise<SessionView> {
    return this.request<SessionView>(`/sessions/${sessionId}`);
  }

  createSession(payload: CreateSessionRequest): Promise<SessionView> {
    return this.request<SessionView>("/sessions", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  }

  decide(sessionId: string, decision: "approve" | "reject"): Promise<ApprovalResponse> {
    return this.request<ApprovalResponse>(`/sessions/${sessionId}/approval`, {
      method: "POST",
      body: JSON.stringify({ decision }),
    });
  }

  deleteSession(sessionId: string): Promise<void> {
    return this.request<void>(`/sessions/${sessionId}`, { method: "DELETE" });
  }

  /**
   * Abre el WebSocket de una sesión.
   *
   * El token va por query param porque la API WebSocket del navegador no
   * permite fijar cabeceras en el handshake. Es el canal que el backend ya
   * acepta para este caso.
   */
  openEvents(sessionId: string, onEvent: (event: AgentEvent) => void): WebSocket {
    const wsUrl = this.baseUrl.replace(/^http/, "ws");
    const socket = new WebSocket(
      `${wsUrl}/sessions/${sessionId}/ws?token=${encodeURIComponent(this.token)}`,
    );

    socket.addEventListener("message", (message) => {
      try {
        onEvent(JSON.parse(message.data as string) as AgentEvent);
      } catch {
        // Un frame no-JSON no debe tumbar el stream; se ignora.
      }
    });

    return socket;
  }
}