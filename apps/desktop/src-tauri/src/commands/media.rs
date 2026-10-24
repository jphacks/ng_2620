//! 動画のメタ情報（F02 の事前検証）。同梱の ffprobe サイドカー（実行ファイルの隣）→ PATH の ffprobe の順に探す。
//! 使えなければエラーを返し、JS 側が <video> の loadedmetadata で代用する

use super::{err, CmdResult};
use serde::Serialize;
use serde_json::Value;
use std::path::PathBuf;
use std::process::Command;

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ProbeResult {
    duration_sec: f64,
    width: u64,
    height: u64,
    rotation: i64,
    codec: Option<String>,
    fps: Option<f64>,
    has_audio: bool,
    size_bytes: u64,
}

fn ffprobe_path() -> PathBuf {
    let name = if cfg!(windows) { "ffprobe.exe" } else { "ffprobe" };
    if let Ok(exe) = std::env::current_exe() {
        if let Some(dir) = exe.parent() {
            let sidecar = dir.join(name);
            if sidecar.exists() {
                return sidecar;
            }
        }
    }
    PathBuf::from(name)
}

#[cfg(windows)]
fn hide_console(cmd: &mut Command) {
    use std::os::windows::process::CommandExt;
    const CREATE_NO_WINDOW: u32 = 0x0800_0000;
    cmd.creation_flags(CREATE_NO_WINDOW);
}

#[cfg(not(windows))]
fn hide_console(_cmd: &mut Command) {}

/// "30000/1001" → 29.97
fn parse_rate(v: &Value) -> Option<f64> {
    let s = v.as_str()?;
    let (n, d) = s.split_once('/').unwrap_or((s, "1"));
    let (n, d) = (n.parse::<f64>().ok()?, d.parse::<f64>().ok()?);
    if d == 0.0 || n == 0.0 {
        None
    } else {
        Some(n / d)
    }
}

fn parse_f64(v: &Value) -> Option<f64> {
    v.as_f64().or_else(|| v.as_str().and_then(|s| s.parse().ok()))
}

fn probe_blocking(path: &str) -> CmdResult<ProbeResult> {
    let mut cmd = Command::new(ffprobe_path());
    cmd.args(["-v", "error", "-print_format", "json", "-show_format", "-show_streams", path]);
    hide_console(&mut cmd);
    let out = cmd.output().map_err(|e| format!("ffprobe を起動できません: {e}"))?;
    if !out.status.success() {
        return Err(String::from_utf8_lossy(&out.stderr).into_owned());
    }
    let v: Value = serde_json::from_slice(&out.stdout).map_err(err)?;
    let streams = v["streams"].as_array().cloned().unwrap_or_default();
    let video = streams
        .iter()
        .find(|s| s["codec_type"] == "video")
        .ok_or_else(|| "映像トラックがありません".to_string())?;
    let has_audio = streams.iter().any(|s| s["codec_type"] == "audio");

    // 回転：古い形式は tags.rotate、新しい ffprobe は side_data_list[].rotation（反時計回りが正）
    let rotation = video["tags"]["rotate"]
        .as_str()
        .and_then(|s| s.parse::<i64>().ok())
        .or_else(|| {
            video["side_data_list"]
                .as_array()
                .and_then(|l| l.iter().find_map(|d| parse_f64(&d["rotation"])))
                .map(|r| -(r.round() as i64))
        })
        .unwrap_or(0)
        .rem_euclid(360);

    let duration_sec = parse_f64(&v["format"]["duration"]).or_else(|| parse_f64(&video["duration"])).unwrap_or(0.0);
    let fps = parse_rate(&video["avg_frame_rate"]).or_else(|| parse_rate(&video["r_frame_rate"]));
    let size_bytes = std::fs::metadata(path).map(|m| m.len()).unwrap_or(0);

    Ok(ProbeResult {
        duration_sec,
        width: video["width"].as_u64().unwrap_or(0),
        height: video["height"].as_u64().unwrap_or(0),
        rotation,
        codec: video["codec_name"].as_str().map(str::to_string),
        fps,
        has_audio,
        size_bytes,
    })
}

#[tauri::command]
pub async fn media_probe(path: String) -> CmdResult<ProbeResult> {
    tauri::async_runtime::spawn_blocking(move || probe_blocking(&path)).await.map_err(err)?
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn parses_frame_rates() {
        assert!((parse_rate(&json!("30000/1001")).unwrap() - 29.97).abs() < 0.01);
        assert_eq!(parse_rate(&json!("30/1")), Some(30.0));
        assert_eq!(parse_rate(&json!("0/0")), None);
        assert_eq!(parse_f64(&json!("9.80")), Some(9.8));
    }
}
