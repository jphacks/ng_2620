pub mod fs;
pub mod hash;
pub mod media;
pub mod net;
pub mod server;

use std::process::Command;

/// コマンドのエラーは文字列で JS に返す
pub type CmdResult<T> = Result<T, String>;

pub fn err<E: std::fmt::Display>(e: E) -> String {
    e.to_string()
}

/// 子プロセスのコンソール窓を出さない（Windows）
#[cfg(windows)]
pub fn hide_console(cmd: &mut Command) {
    use std::os::windows::process::CommandExt;
    const CREATE_NO_WINDOW: u32 = 0x0800_0000;
    cmd.creation_flags(CREATE_NO_WINDOW);
}

#[cfg(not(windows))]
pub fn hide_console(_cmd: &mut Command) {}
