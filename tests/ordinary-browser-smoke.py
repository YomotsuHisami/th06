#!/usr/bin/env python3
"""Browser gates for the ordinary TH06 Replay and Music Room paths."""

from __future__ import annotations

import argparse
import functools
import http.server
import socketserver
import threading
import time
from pathlib import Path

from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[1]
class FixtureHandler(http.server.SimpleHTTPRequestHandler):
    fixture: Path
    runtime_directory: Path
    font: Path

    def translate_path(self, path: str) -> str:
        if path.startswith("/__runtime/"):
            previous = self.directory
            try:
                self.directory = str(self.runtime_directory)
                return super().translate_path(path[len("/__runtime"):])
            finally:
                self.directory = previous
        return super().translate_path(path)

    def do_GET(self) -> None:
        if self.path.split("?", 1)[0] == "/__ordinary_font":
            data = self.font.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "font/otf")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        if self.path.split("?", 1)[0] == "/__ordinary_replay_fixture.rpy":
            data = self.fixture.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        super().do_GET()

    def log_message(self, _format: str, *_args: object) -> None:
        pass


def validate_fixture(path: Path) -> None:
    data = path.read_bytes()
    if data[:4] != b"T6RP":
        raise RuntimeError(f"fixture has invalid magic: {data[:4]!r}")


def pulse_select(page) -> None:
    page.evaluate(
        """() => {
          const runtime = document.getElementById('runtime')?.contentWindow;
          if (!runtime) return;
          const init = { key: 'z', code: 'KeyZ', bubbles: true, cancelable: true };
          runtime.dispatchEvent(new KeyboardEvent('keydown', init));
          setTimeout(() => runtime.dispatchEvent(new KeyboardEvent('keyup', init)), 80);
        }"""
    )


def runtime_state(page) -> dict:
    return page.evaluate(
        """() => {
          const runtime = document.getElementById('runtime')?.contentWindow;
          return {
            failure: globalThis.__th06OrdinarySmokeFailure || '',
            launched: !!globalThis.__th06OrdinarySmokeLaunched,
            replayObserved: !!runtime?.__eaglerOrdinaryReplayPlaybackObserved,
            replayFrame: runtime?.__eaglerOrdinaryReplayPlaybackFrame ?? -1,
            replayInput: runtime?.__eaglerOrdinaryReplayPlaybackInput ?? -1,
            replayStage: runtime?.__eaglerOrdinaryReplayPlaybackStage ?? -1,
            musicRoom: !!runtime?.__eaglerMusicRoomEntered,
            descriptors: runtime?.__eaglerMusicRoomDescriptorCount ?? -1,
            files: (() => { try { return runtime.FS.readdir('/replay'); } catch { return []; } })(),
            options: runtime?.Module?.eaglerOptions || null,
          };
        }"""
    )


def run_gate(
    mode: str,
    fixture: Path,
    port: int,
    headed: bool,
    timeout: float,
    runtime_directory: Path,
    font: Path,
) -> None:
    handler = functools.partial(FixtureHandler, directory=str(ROOT))
    FixtureHandler.fixture = fixture
    FixtureHandler.runtime_directory = runtime_directory
    FixtureHandler.font = font
    with socketserver.TCPServer(("127.0.0.1", port), handler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=not headed)
                page = browser.new_page(viewport={"width": 900, "height": 700})
                console_messages = []
                page.on("console", lambda message: console_messages.append(message.text))
                page.on("pageerror", lambda error: console_messages.append(f"pageerror: {error}"))
                page.goto(
                    f"http://127.0.0.1:{port}/tests/ordinary-browser-host.html?mode={mode}",
                    wait_until="domcontentloaded",
                )
                deadline = time.time() + timeout
                next_pulse = 0.0
                state = {}
                while time.time() < deadline:
                    state = runtime_state(page)
                    if state["failure"]:
                        raise RuntimeError(state["failure"])
                    if mode == "replay":
                        if state["replayObserved"] and state["replayFrame"] >= 300:
                            break
                        if state["launched"] and time.time() >= next_pulse:
                            pulse_select(page)
                            next_pulse = time.time() + 1.0
                    elif state["musicRoom"] and state["descriptors"] >= 17:
                        break
                    time.sleep(0.1)
                else:
                    output = ROOT / "artifacts/ordinary-browser-smoke"
                    output.mkdir(parents=True, exist_ok=True)
                    page.screenshot(path=str(output / f"{mode}-timeout.png"), full_page=True)
                    raise RuntimeError(f"{mode} timeout; state={state}; console={console_messages[-20:]}")
                browser.close()
        finally:
            server.shutdown()

    if mode == "replay":
        print(
            "TH06 ordinary Replay browser smoke: PASS "
            f"frame={state['replayFrame']} stage={state['replayStage']} input={state['replayInput']}"
        )
    else:
        print(f"TH06 Music Room browser smoke: PASS descriptors={state['descriptors']}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("replay", "music-room"))
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--runtime-directory", type=Path, default=ROOT / "build/web/normal")
    parser.add_argument("--font", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8136)
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--timeout", type=float, default=90.0)
    args = parser.parse_args()
    validate_fixture(args.fixture)
    if not (args.runtime_directory / "th06.html").is_file() or not args.font.is_file():
        parser.error("需要已构建 Runtime 目录和显式字体文件")
    run_gate(
        args.mode,
        args.fixture.resolve(),
        args.port,
        args.headed,
        args.timeout,
        args.runtime_directory.resolve(),
        args.font.resolve(),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
