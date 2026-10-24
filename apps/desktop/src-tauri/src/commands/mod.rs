pub mod fs;
pub mod hash;
pub mod media;
pub mod net;

/// コマンドのエラーは文字列で JS に返す
pub type CmdResult<T> = Result<T, String>;

pub fn err<E: std::fmt::Display>(e: E) -> String {
    e.to_string()
}
