//! sha256 をストリームで計算する（F02：アップロード前、F07：VRM の ID）。
//! 同じファイル（パス・サイズ・更新日時が同じ）は計算結果を再利用する

use super::{err, CmdResult};
use sha2::{Digest, Sha256};
use std::collections::HashMap;
use std::fs;
use std::io::Read;
use std::sync::{Mutex, OnceLock};
use tauri::ipc::Channel;

type Key = (String, u64, u128);

fn cache() -> &'static Mutex<HashMap<Key, String>> {
    static CACHE: OnceLock<Mutex<HashMap<Key, String>>> = OnceLock::new();
    CACHE.get_or_init(|| Mutex::new(HashMap::new()))
}

fn sha256_file(path: &str, on_progress: &dyn Fn(f64)) -> std::io::Result<String> {
    let meta = fs::metadata(path)?;
    let mtime = meta
        .modified()
        .ok()
        .and_then(|t| t.duration_since(std::time::UNIX_EPOCH).ok())
        .map(|d| d.as_nanos())
        .unwrap_or(0);
    let key: Key = (path.to_string(), meta.len(), mtime);
    if let Some(hit) = cache().lock().ok().and_then(|c| c.get(&key).cloned()) {
        on_progress(1.0);
        return Ok(hit);
    }

    let total = meta.len().max(1) as f64;
    let mut f = fs::File::open(path)?;
    let mut hasher = Sha256::new();
    let mut buf = vec![0u8; 1 << 20];
    let mut done = 0u64;
    let mut last = 0.0;
    loop {
        let n = f.read(&mut buf)?;
        if n == 0 {
            break;
        }
        hasher.update(&buf[..n]);
        done += n as u64;
        let r = done as f64 / total;
        if r - last >= 0.01 {
            on_progress(r);
            last = r;
        }
    }
    let hex = hex::encode(hasher.finalize());
    if let Ok(mut c) = cache().lock() {
        c.insert(key, hex.clone());
    }
    on_progress(1.0);
    Ok(hex)
}

#[tauri::command]
pub async fn hash_sha256(path: String, on_progress: Channel<f64>) -> CmdResult<String> {
    tauri::async_runtime::spawn_blocking(move || {
        sha256_file(&path, &|r| {
            let _ = on_progress.send(r);
        })
        .map_err(err)
    })
    .await
    .map_err(err)?
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn hashes_and_caches() {
        let p = std::env::temp_dir().join(format!("vvlog-hash-{}.bin", std::process::id()));
        fs::write(&p, b"abc").unwrap();
        let path = p.to_string_lossy().to_string();
        let h = sha256_file(&path, &|_| {}).unwrap();
        assert_eq!(h, "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
        assert_eq!(sha256_file(&path, &|_| {}).unwrap(), h);
        fs::remove_file(&p).unwrap();
    }
}
