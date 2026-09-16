import type { SessionView } from "../api/types";

interface Props {
  session: SessionView | null;
  canDecide: boolean;
  onDecide: (decision: "approve" | "reject") => void;
}

/**
 * Muestra el plan propuesto y los botones de aprobacion.
 *
 * Los botones solo se habilitan en `awaiting_approval`: es la misma regla que
 * aplica el backend (responde 409 en cualquier otro estado), asi que la UI no
 * ofrece una accion que el servidor va a rechazar.
 */
export function PlanReview({ session, canDecide, onDecide }: Props) {
  if (!session) {
    return (
      <section className="panel">
        <h2>Plan</h2>
        <p className="muted">Conecta el backend y escanea un directorio para ver el plan.</p>
      </section>
    );
  }

  const plan = session.plan;

  return (
    <section className="panel">
      <h2>Plan</h2>
      {!plan ? (
        <p className="muted">Todavia no hay plan para esta sesion.</p>
      ) : (
        <>
          <p>{plan.summary}</p>
          <p className="muted">
            {plan.rename_count} renombre(s) · {plan.mkdir_count} carpeta(s) · operaciones
            permitidas: {plan.allowed_operations.join(", ") || "ninguna"}
          </p>

          {plan.create_dirs.length > 0 && (
            <table>
              <caption>Carpetas a crear</caption>
              <tbody>
                {plan.create_dirs.map((dir) => (
                  <tr key={dir.dir_path}>
                    <td className="mono">{dir.dir_path}</td>
                    <td className="muted">{dir.reason ?? ""}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}

          {plan.rename_files.length > 0 && (
            <table>
              <caption>Renombres propuestos</caption>
              <thead>
                <tr>
                  <th>Origen</th>
                  <th>Destino</th>
                  <th>Motivo</th>
                </tr>
              </thead>
              <tbody>
                {plan.rename_files.map((rename) => (
                  <tr key={rename.src}>
                    <td className="mono">{rename.src}</td>
                    <td className="mono">{rename.dst}</td>
                    <td className="muted">{rename.reason ?? ""}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </>
      )}

      <div className="row">
        <button
          type="button"
          className="approve"
          disabled={!canDecide}
          onClick={() => onDecide("approve")}
        >
          [A] Aprobar
        </button>
        <button
          type="button"
          className="reject"
          disabled={!canDecide}
          onClick={() => onDecide("reject")}
        >
          [R] Rechazar
        </button>
      </div>

      {session.approval && (
        <p className="muted">
          Decision registrada: {session.approval.decision} ({session.approval.decided_at})
        </p>
      )}

      {session.applied.length > 0 && (
        <table>
          <caption>Cambios aplicados</caption>
          <tbody>
            {session.applied.map((change) => (
              <tr key={change.src}>
                <td className="mono">{change.src}</td>
                <td className="mono">{change.dst}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}