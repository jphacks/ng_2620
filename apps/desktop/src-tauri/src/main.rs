// リリースビルドの Windows でコンソールを出さない
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    vvlog_desktop_lib::run()
}
