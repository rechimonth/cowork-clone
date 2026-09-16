//! Shell de escritorio del agente cowork-clone.
//!
//! No reimplementa nada del agente: solo abre el webview que carga el
//! frontend React. Toda la logica (escaneo, plan, aprobacion) vive en el
//! backend FastAPI, que se ejecuta como proceso aparte. Esta separacion es
//! deliberada: el agente opera sobre el filesystem y necesita permisos que no
//! conviene dar al webview.

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    tauri::Builder::default()
        .run(tauri::generate_context!())
        .expect("error al arrancar la aplicacion Tauri");
}