// Lantern desktop shell: native window + local Python answer server.
// Not a browser app — the webview is only the UI surface.

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::path::PathBuf;
use std::process::{Child, Command};
use std::sync::Mutex;
use std::thread;
use std::time::Duration;

use tauri::Manager;
#[cfg(not(debug_assertions))]
use tauri_plugin_shell::process::CommandChild;
#[cfg(not(debug_assertions))]
use tauri_plugin_shell::ShellExt;

struct Server(Mutex<Option<ChildHandle>>);

enum ChildHandle {
    Std(Child),
    #[cfg(not(debug_assertions))]
    Shell(CommandChild),
}

impl ChildHandle {
    fn kill(self) {
        match self {
            ChildHandle::Std(mut c) => {
                let _ = c.kill();
                let _ = c.wait();
            }
            #[cfg(not(debug_assertions))]
            ChildHandle::Shell(c) => {
                let _ = c.kill();
            }
        }
    }
}

const PORT: &str = "7451";

fn project_root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .expect("src-tauri has a parent")
        .to_path_buf()
}

fn wait_for_api() {
    for _ in 0..50 {
        if std::net::TcpStream::connect("127.0.0.1:7451").is_ok() {
            return;
        }
        thread::sleep(Duration::from_millis(100));
    }
}

#[cfg(debug_assertions)]
fn start_server(_app: &tauri::AppHandle) -> Result<ChildHandle, Box<dyn std::error::Error>> {
    let root = project_root();
    let child = Command::new("python3")
        .args(["sidecar.py", "--port", PORT])
        .current_dir(&root)
        .spawn()
        .map_err(|e| {
            format!(
                "failed to start python3 sidecar from {}: {e}",
                root.display()
            )
        })?;
    Ok(ChildHandle::Std(child))
}

#[cfg(not(debug_assertions))]
fn start_server(app: &tauri::AppHandle) -> Result<ChildHandle, Box<dyn std::error::Error>> {
    let sidecar = app
        .shell()
        .sidecar("lantern-server")
        .map_err(|e| format!("sidecar binary missing — run the build script: {e}"))?
        .args(["--port", PORT]);
    let (_rx, child) = sidecar.spawn()?;
    Ok(ChildHandle::Shell(child))
}

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .setup(|app| {
            let child = start_server(app.handle())?;
            app.manage(Server(Mutex::new(Some(child))));
            wait_for_api();
            Ok(())
        })
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::Destroyed = event {
                if let Some(state) = window.app_handle().try_state::<Server>() {
                    if let Some(child) = state.0.lock().unwrap().take() {
                        child.kill();
                    }
                }
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running Lantern");
}
