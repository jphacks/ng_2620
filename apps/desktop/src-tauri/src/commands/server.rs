//! この PC の GPU サーバー（services/gpu_server）を子プロセスとして起動・停止する（F03 P1、ADR-0002）
//!   server_start  <serverDir>/.venv の Python で `-m vvlog_server`。トークン・ポート・推論用 Python は環境変数で渡す
//!   server_stop   プロセスツリーごと止める（実行中のジョブは次の起動で INTERRUPTED になる）
//!   server_status 動いているか、終了コード、最近のログ
//! アプリの終了時にも止める（lib.rs）

use super::{err, hide_console, CmdResult};
use serde::{Deserialize, Serialize};
use std::collections::VecDeque;
use std::io::{BufRead, BufReader, Read};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::{Arc, Mutex};
use tauri::State;

const LOG_LINES: usize = 300;

type Log = Arc<Mutex<VecDeque<String>>>;

#[derive(Default)]
pub struct ServerState {
    child: Mutex<Option<Child>>,
    exit_code: Mutex<Option<i32>>,
    log: Log,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct StartConfig {
    server_dir: String,
    python_path: String,
    port: u16,
    token: String,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ServerStatus {
    running: bool,
    pid: Option<u32>,
    exit_code: Option<i32>,
    log: Vec<String>,
}

fn venv_python(server_dir: &Path) -> PathBuf {
    if cfg!(windows) {
        server_dir.join(".venv").join("Scripts").join("python.exe")
    } else {
        server_dir.join(".venv").join("bin").join("python")
    }
}

fn push_line(log: &Log, line: String) {
    let mut l = log.lock().unwrap();
    if l.len() >= LOG_LINES {
        l.pop_front();
    }
    l.push_back(line);
}

/// 出力を行ごとにログへ（Windows の Python は UTF-8 にしているが、念のため不正なバイトは置き換える）
fn pipe_to_log<R: Read + Send + 'static>(src: R, log: Log) {
    std::thread::spawn(move || {
        let mut reader = BufReader::new(src);
        let mut buf = Vec::new();
        while reader.read_until(b'\n', &mut buf).map(|n| n > 0).unwrap_or(false) {
            push_line(&log, String::from_utf8_lossy(&buf).trim_end().to_string());
            buf.clear();
        }
    });
}

#[cfg(windows)]
fn kill_tree(child: &mut Child) {
    // パイプラインの推論もサブプロセスなので、ツリーごと止める
    let mut cmd = Command::new("taskkill");
    cmd.args(["/T", "/F", "/PID", &child.id().to_string()]);
    hide_console(&mut cmd);
    let _ = cmd.output();
    let _ = child.kill();
}

#[cfg(not(windows))]
fn kill_tree(child: &mut Child) {
    let _ = child.kill();
}

impl ServerState {
    /// 終わっていれば子プロセスを外して終了コードを残す
    fn reap(&self) -> Option<u32> {
        let mut child = self.child.lock().unwrap();
        if let Some(c) = child.as_mut() {
            match c.try_wait() {
                Ok(None) => return Some(c.id()),
                Ok(Some(st)) => *self.exit_code.lock().unwrap() = st.code(),
                Err(_) => *self.exit_code.lock().unwrap() = None,
            }
            *child = None;
        }
        None
    }

    pub fn shutdown(&self) {
        if let Some(mut c) = self.child.lock().unwrap().take() {
            kill_tree(&mut c);
            let _ = c.wait();
        }
    }
}

#[tauri::command]
pub async fn server_start(config: StartConfig, state: State<'_, ServerState>) -> CmdResult<u32> {
    if let Some(pid) = state.reap() {
        return Ok(pid);
    }
    let dir = PathBuf::from(&config.server_dir);
    if !dir.join("vvlog_server").is_dir() {
        return Err(format!("{} に vvlog_server がありません。services/gpu_server のフォルダを選んでください", dir.display()));
    }
    let py = venv_python(&dir);
    if !py.exists() {
        return Err(format!("サーバーの Python（{}）がありません。services/gpu_server で run_local.ps1 を一度実行して作ってください", py.display()));
    }
    if !Path::new(&config.python_path).is_file() {
        return Err(format!("推論用の Python（{}）が見つかりません", config.python_path));
    }

    let mut cmd = Command::new(&py);
    cmd.args(["-m", "vvlog_server"])
        .current_dir(&dir)
        .env("VVLOG_API_TOKEN", &config.token)
        .env("VVLOG_PYTHON", &config.python_path)
        .env("VVLOG_PORT", config.port.to_string())
        .env("VVLOG_DATA_ROOT", dir.join("data"))
        .env("PYTHONUNBUFFERED", "1")
        .env("PYTHONUTF8", "1")
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    hide_console(&mut cmd);
    let mut child = cmd.spawn().map_err(|e| format!("サーバーを起動できません: {e}"))?;

    state.log.lock().unwrap().clear();
    *state.exit_code.lock().unwrap() = None;
    if let Some(out) = child.stdout.take() {
        pipe_to_log(out, state.log.clone());
    }
    if let Some(e) = child.stderr.take() {
        pipe_to_log(e, state.log.clone());
    }
    let pid = child.id();
    *state.child.lock().unwrap() = Some(child);
    Ok(pid)
}

#[tauri::command]
pub async fn server_stop(state: State<'_, ServerState>) -> CmdResult<()> {
    state.shutdown();
    Ok(())
}

#[tauri::command]
pub async fn server_status(state: State<'_, ServerState>) -> CmdResult<ServerStatus> {
    let pid = state.reap();
    let exit_code = *state.exit_code.lock().map_err(err)?;
    let log = state.log.lock().map_err(err)?.iter().cloned().collect();
    Ok(ServerStatus { running: pid.is_some(), pid, exit_code, log })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn finds_venv_python() {
        let p = venv_python(Path::new("gpu_server"));
        assert!(p.starts_with(Path::new("gpu_server").join(".venv")));
    }

    #[test]
    fn keeps_only_recent_lines() {
        let log: Log = Arc::default();
        for i in 0..LOG_LINES + 5 {
            push_line(&log, i.to_string());
        }
        let l = log.lock().unwrap();
        assert_eq!(l.len(), LOG_LINES);
        assert_eq!(l.front().map(String::as_str), Some("5"));
    }
}
