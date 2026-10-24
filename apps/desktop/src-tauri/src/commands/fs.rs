//! ファイル操作（infrastructure/fs/tauriFs.ts から呼ぶ）。
//! 大きなファイル（動画・成果物）は WebView に丸ごと載せず、範囲で読むか Rust 側で処理する

use super::{err, CmdResult};
use serde::Serialize;
use std::fs;
use std::io::{Read, Seek, SeekFrom, Write};
use std::path::Path;
use tauri::ipc::{InvokeBody, Request, Response};

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct FileStat {
    size: u64,
    mtime_ms: f64,
}

#[cfg(unix)]
fn set_private(p: &Path) -> std::io::Result<()> {
    use std::os::unix::fs::PermissionsExt;
    fs::set_permissions(p, fs::Permissions::from_mode(0o600))
}

#[cfg(not(unix))]
fn set_private(_p: &Path) -> std::io::Result<()> {
    Ok(())
}

/// 一時ファイルに書いて fsync してから rename する（保存中に落ちても元のファイルは壊れない、ADR-0007）。
/// rename は Windows でも既存のファイルを置き換える
pub fn write_atomic(path: &Path, bytes: &[u8], private: bool) -> std::io::Result<()> {
    if let Some(dir) = path.parent() {
        fs::create_dir_all(dir)?;
    }
    let name = path.file_name().map(|n| n.to_string_lossy().to_string()).unwrap_or_default();
    let tmp = path.with_file_name(format!(".{}.tmp-{}", name, std::process::id()));
    {
        let mut f = fs::File::create(&tmp)?;
        f.write_all(bytes)?;
        f.sync_all()?;
    }
    if private {
        set_private(&tmp)?;
    }
    fs::rename(&tmp, path)
}

/// offset から最大 length バイト（ファイル末尾で切る）
pub fn read_range(path: &str, offset: u64, length: u64) -> std::io::Result<Vec<u8>> {
    let mut f = fs::File::open(path)?;
    let size = f.metadata()?.len();
    let len = length.min(size.saturating_sub(offset)) as usize;
    f.seek(SeekFrom::Start(offset))?;
    let mut buf = vec![0u8; len];
    f.read_exact(&mut buf)?;
    Ok(buf)
}

/// encodeURIComponent の逆
fn percent_decode(s: &str) -> String {
    let b = s.as_bytes();
    let mut out = Vec::with_capacity(b.len());
    let mut i = 0;
    while i < b.len() {
        if b[i] == b'%' && i + 2 < b.len() {
            let hex = std::str::from_utf8(&b[i + 1..i + 3]).ok().and_then(|h| u8::from_str_radix(h, 16).ok());
            if let Some(v) = hex {
                out.push(v);
                i += 3;
                continue;
            }
        }
        out.push(b[i]);
        i += 1;
    }
    String::from_utf8_lossy(&out).into_owned()
}

#[tauri::command]
pub fn fs_exists(path: String) -> bool {
    Path::new(&path).exists()
}

#[tauri::command]
pub fn fs_stat(path: String) -> CmdResult<FileStat> {
    let m = fs::metadata(&path).map_err(err)?;
    let mtime_ms = m
        .modified()
        .ok()
        .and_then(|t| t.duration_since(std::time::UNIX_EPOCH).ok())
        .map(|d| d.as_millis() as f64)
        .unwrap_or(0.0);
    Ok(FileStat { size: m.len(), mtime_ms })
}

#[tauri::command]
pub fn fs_read_text(path: String) -> CmdResult<String> {
    fs::read_to_string(&path).map_err(err)
}

#[tauri::command]
pub fn fs_write_atomic(path: String, contents: String, private: Option<bool>) -> CmdResult<()> {
    write_atomic(Path::new(&path), contents.as_bytes(), private.unwrap_or(false)).map_err(err)
}

#[tauri::command]
pub fn fs_read_bytes(path: String) -> CmdResult<Response> {
    fs::read(&path).map(Response::new).map_err(err)
}

#[tauri::command]
pub fn fs_read_chunk(path: String, offset: u64, length: u64) -> CmdResult<Response> {
    read_range(&path, offset, length).map(Response::new).map_err(err)
}

/// 本体はバイト列のまま受け取る（JSON に変換しない）。パスは x-path ヘッダー（URI エンコード）
#[tauri::command]
pub fn fs_write_bytes(request: Request<'_>) -> CmdResult<()> {
    let raw = request
        .headers()
        .get("x-path")
        .and_then(|v| v.to_str().ok())
        .ok_or_else(|| "x-path ヘッダーがありません".to_string())?;
    let path = percent_decode(raw);
    let InvokeBody::Raw(bytes) = request.body() else {
        return Err("本体がバイト列ではありません".into());
    };
    write_atomic(Path::new(&path), bytes, false).map_err(err)
}

#[tauri::command]
pub fn fs_copy_file(src: String, dest: String) -> CmdResult<()> {
    if let Some(dir) = Path::new(&dest).parent() {
        fs::create_dir_all(dir).map_err(err)?;
    }
    fs::copy(&src, &dest).map(|_| ()).map_err(err)
}

#[tauri::command]
pub fn fs_ensure_dir(path: String) -> CmdResult<()> {
    fs::create_dir_all(&path).map_err(err)
}

#[tauri::command]
pub fn fs_list_dir(path: String) -> CmdResult<Vec<String>> {
    let p = Path::new(&path);
    if !p.exists() {
        return Ok(vec![]);
    }
    let mut names = vec![];
    for e in fs::read_dir(p).map_err(err)? {
        let e = e.map_err(err)?;
        names.push(e.file_name().to_string_lossy().to_string());
    }
    Ok(names)
}

#[tauri::command]
pub fn fs_remove(path: String) -> CmdResult<()> {
    let p = Path::new(&path);
    if p.is_dir() {
        fs::remove_dir_all(p).map_err(err)
    } else if p.exists() {
        fs::remove_file(p).map_err(err)
    } else {
        Ok(())
    }
}

#[tauri::command]
pub fn fs_move_to_trash(path: String) -> CmdResult<()> {
    trash::delete(&path).map_err(err)
}

/// zip（成果物は無圧縮 store、contracts §4）を dest_dir に展開する。中のパスは zip が検査する
#[tauri::command]
pub async fn fs_unzip(zip_path: String, dest_dir: String) -> CmdResult<()> {
    tauri::async_runtime::spawn_blocking(move || -> CmdResult<()> {
        let f = fs::File::open(&zip_path).map_err(err)?;
        let mut archive = zip::ZipArchive::new(f).map_err(err)?;
        fs::create_dir_all(&dest_dir).map_err(err)?;
        archive.extract(&dest_dir).map_err(err)
    })
    .await
    .map_err(err)?
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn decodes_percent_encoding() {
        assert_eq!(percent_decode("%2FUsers%2Fme%2F%E5%8B%95%E7%94%BB.mp4"), "/Users/me/動画.mp4");
        assert_eq!(percent_decode("C%3A%5Cx%5Cy"), "C:\\x\\y");
        assert_eq!(percent_decode("plain"), "plain");
        assert_eq!(percent_decode("bad%zz"), "bad%zz");
    }

    #[test]
    fn writes_atomically_and_reads_ranges() {
        let dir = std::env::temp_dir().join(format!("vvlog-test-{}", std::process::id()));
        let file = dir.join("a").join("project.json");
        write_atomic(&file, b"hello world", true).unwrap();
        write_atomic(&file, b"0123456789", false).unwrap();
        assert_eq!(fs::read_to_string(&file).unwrap(), "0123456789");
        let p = file.to_string_lossy().to_string();
        assert_eq!(read_range(&p, 8, 8).unwrap(), b"89");
        assert_eq!(read_range(&p, 20, 8).unwrap(), b"");
        fs::remove_dir_all(&dir).unwrap();
    }
}
