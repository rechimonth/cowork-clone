import { useState } from "react";

interface Props {
  connected: boolean;
  busy: boolean;
  onConnect: (baseUrl: string, token: string) => void;
  onStart: (rootDir: string, dryRun: boolean) => void;
}

const DEFAULT_BASE_URL = "http://127.0.0.1:8000";

/** Conexion al backend y arranque de una sesion sobre un directorio raiz. */
export function ConnectionPanel({ connected, busy, onConnect, onStart }: Props) {
  const [baseUrl, setBaseUrl] = useState(DEFAULT_BASE_URL);
  const [token, setToken] = useState("");
  const [rootDir, setRootDir] = useState("");
  const [dryRun, setDryRun] = useState(true);

  return (
    <section className="panel">
      <h2>1. Backend</h2>
      <div className="row">
        <label>
          URL
          <input
            value={baseUrl}
            onChange={(event) => setBaseUrl(event.target.value)}
            placeholder={DEFAULT_BASE_URL}
          />
        </label>
        <label>
          Token
          <input
            type="password"
            value={token}
            onChange={(event) => setToken(event.target.value)}
            placeholder="COWORK_API_TOKEN"
          />
        </label>
        <button
          type="button"
          disabled={busy || token.length === 0}
          onClick={() => onConnect(baseUrl, token)}
        >
          {connected ? "Reconectar" : "Conectar"}
        </button>
      </div>
      {!connected && (
        <p className="muted">
          El token se guarda solo en memoria y se pierde al cerrar la ventana.
        </p>
      )}

      <h2>2. Directorio a operar</h2>
      <div className="row">
        <label className="grow">
          Ruta raiz
          <input
            value={rootDir}
            onChange={(event) => setRootDir(event.target.value)}
            placeholder="/home/usuario/Descargas"
          />
        </label>
        <label className="checkbox">
          <input
            type="checkbox"
            checked={dryRun}
            onChange={(event) => setDryRun(event.target.checked)}
          />
          Simulacion
        </label>
        <button
          type="button"
          disabled={!connected || busy || rootDir.length === 0}
          onClick={() => onStart(rootDir, dryRun)}
        >
          Escanear
        </button>
      </div>
    </section>
  );
}