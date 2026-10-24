//! vvlog デスクトップアプリの Rust 側（ADR-0001：薄く保ち、OS 機能だけをコマンドとして公開する）
//!   commands::fs    プロジェクトフォルダの原子的書き込み、読み出し、zip 展開、ゴミ箱
//!   commands::media 動画のメタ情報（ffprobe）
//!   commands::hash  sha256（ストリーム、進捗つき）
//!   commands::net   成果物のダウンロード（Range 再開）とファイルからの分割アップロード
//! ローカル GPU サーバーの起動（server.rs）とキーチェーン（secrets.rs）は P1

mod commands;

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_http::init())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_notification::init())
        .plugin(tauri_plugin_opener::init())
        .invoke_handler(tauri::generate_handler![
            commands::fs::fs_exists,
            commands::fs::fs_stat,
            commands::fs::fs_read_text,
            commands::fs::fs_write_atomic,
            commands::fs::fs_read_bytes,
            commands::fs::fs_read_chunk,
            commands::fs::fs_write_bytes,
            commands::fs::fs_copy_file,
            commands::fs::fs_ensure_dir,
            commands::fs::fs_list_dir,
            commands::fs::fs_remove,
            commands::fs::fs_move_to_trash,
            commands::fs::fs_unzip,
            commands::media::media_probe,
            commands::hash::hash_sha256,
            commands::net::net_download,
            commands::net::net_upload_chunk,
        ])
        .run(tauri::generate_context!())
        .expect("vvlog の起動に失敗しました");
}
