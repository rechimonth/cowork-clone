/**
 * Tipos espejo del contrato de `api/schemas.py`.
 *
 * Se mantienen a mano en lugar de generarlos desde OpenAPI para que el
 * frontend no dependa de tener el backend levantado en tiempo de compilacion.
 * Si el backend cambia un campo, TypeScript senala exactamente los usos.
 */

export type SessionState =
  | "created"
  | "scanning"
  | "scanned"
  | "planning"
  | "awaiting_approval"
  | "approved"
  | "rejected"
  | "executing"
  | "completed"
  | "failed"
  | "expired";

export type AgentEventType =
  | "state"
  | "log"
  | "scan"
  | "plan"
  | "approval"
  | "execution"
  | "error";

export interface AgentEvent {
  seq: number;
  ts: string;
  type: AgentEventType;
  message: string;
  data: Record<string, unknown>;
}

export interface RenameView {
  src: string;
  dst: string;
  reason: string | null;
}

export interface CreateDirView {
  dir_path: string;
  reason: string | null;
}

export interface PlanView {
  plan_id: string | null;
  execution_id: string | null;
  created_at: string | null;
  summary: string;
  create_dirs: CreateDirView[];
  rename_files: RenameView[];
  allowed_operations: string[];
  rename_count: number;
  mkdir_count: number;
}

export interface ApprovalView {
  approved: boolean;
  decision: string;
  decided_at: string;
}

export interface SessionView {
  session_id: string;
  state: SessionState;
  root_dir: string;
  recursive: boolean;
  dry_run: boolean;
  name: string | null;
  created_at: string;
  updated_at: string;
  file_count: number;
  plan: PlanView | null;
  approval: ApprovalView | null;
  applied: RenameView[];
  error: string | null;
  event_count: number;
}

export interface CreateSessionRequest {
  root_dir: string;
  recursive?: boolean;
  dry_run?: boolean;
  name?: string | null;
}

export interface ApprovalResponse {
  session_id: string;
  approved: boolean;
  decision: string;
  state: SessionState;
}

export interface HealthResponse {
  status: "ok";
  version: string;
  authentication_required: boolean;
  approval_timeout_s: number;
}