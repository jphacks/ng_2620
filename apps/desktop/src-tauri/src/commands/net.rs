//! 大きなデータの送受信（ADR-0012）。WebView を通さずに Rust 側で行う
//!   net_download     成果物を dest に保存。<dest>.part があれば Range で続きから
//!   net_upload_chunk ファイルの範囲をそのまま PUT（8MB の断片を JS の配列に変換しない）
//! エラーは "HTTP <status> <本文>" の形で返し、JS 側がサーバーのエラー本文を読む

use super::fs::read_range;
use super::{err, CmdResult};
use serde::Serialize;
use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::OnceLock;
use std::time::{Duration, Instant};
use tauri::ipc::Channel;
use tauri_plugin_http::reqwest;
use tokio::io::AsyncWriteExt;

fn client() -> &'static reqwest::Client {
    static CLIENT: OnceLock<reqwest::Client> = OnceLock::new();
    CLIENT.get_or_init(reqwest::Client::new)
}

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Progress {
    received: u64,
    total: Option<u64>,
}

#[derive(Serialize)]
pub struct UploadResult {
    status: u16,
    body: String,
}

fn part_path(dest: &Path) -> PathBuf {
    let name = dest
        .file_name()
        .map(|n| n.to_string_lossy().to_string())
        .unwrap_or_default();
    dest.with_file_name(format!("{name}.part"))
}

async fn get(url: &str, headers: &HashMap<String, String>, offset: u64) -> CmdResult<reqwest::Response> {
    let mut req = client().get(url);
    for (k, v) in headers {
        req = req.header(k.as_str(), v.as_str());
    }
    if offset > 0 {
        req = req.header("Range", format!("bytes={offset}-"));
    }
    req.send().await.map_err(err)
}

#[tauri::command]
pub async fn net_download(
    url: String,
    headers: HashMap<String, String>,
    dest: String,
    on_progress: Channel<Progress>,
) -> CmdResult<()> {
    let dest = PathBuf::from(dest);
    if let Some(dir) = dest.parent() {
        tokio::fs::create_dir_all(dir).await.map_err(err)?;
    }
    let part = part_path(&dest);
    let mut offset = tokio::fs::metadata(&part).await.map(|m| m.len()).unwrap_or(0);
    let mut resp = get(&url, &headers, offset).await?;
    if resp.status() == reqwest::StatusCode::RANGE_NOT_SATISFIABLE {
        // 途中のファイルが合わない → 最初から
        let _ = tokio::fs::remove_file(&part).await;
        offset = 0;
        resp = get(&url, &headers, 0).await?;
    }
    let status = resp.status();
    if !status.is_success() {
        let body = resp.text().await.unwrap_or_default();
        return Err(format!("HTTP {} {}", status.as_u16(), body));
    }
    let resuming = offset > 0 && status == reqwest::StatusCode::PARTIAL_CONTENT;
    if !resuming {
        offset = 0;
    }
    let total = resp.content_length().map(|n| n + offset);
    let mut file = tokio::fs::OpenOptions::new()
        .create(true)
        .write(true)
        .append(resuming)
        .truncate(!resuming)
        .open(&part)
        .await
        .map_err(err)?;

    let mut received = offset;
    let mut last = Instant::now();
    let _ = on_progress.send(Progress { received, total });
    while let Some(chunk) = resp.chunk().await.map_err(err)? {
        file.write_all(&chunk).await.map_err(err)?;
        received += chunk.len() as u64;
        if last.elapsed() > Duration::from_millis(150) {
            last = Instant::now();
            let _ = on_progress.send(Progress { received, total });
        }
    }
    file.flush().await.map_err(err)?;
    drop(file);
    tokio::fs::rename(&part, &dest).await.map_err(err)?;
    let _ = on_progress.send(Progress {
        received,
        total: Some(received),
    });
    Ok(())
}

#[tauri::command]
pub async fn net_upload_chunk(
    url: String,
    headers: HashMap<String, String>,
    path: String,
    offset: u64,
    length: u64,
) -> CmdResult<UploadResult> {
    let bytes = tauri::async_runtime::spawn_blocking(move || read_range(&path, offset, length))
        .await
        .map_err(err)?
        .map_err(err)?;
    let mut req = client().put(url.as_str()).body(bytes);
    for (k, v) in &headers {
        req = req.header(k.as_str(), v.as_str());
    }
    let resp = req.send().await.map_err(err)?;
    let status = resp.status().as_u16();
    let body = resp.text().await.unwrap_or_default();
    Ok(UploadResult { status, body })
}
