"""视频录制（依赖 Browser Use 底层 Playwright）"""

from pathlib import Path

OUTPUT_DIR = Path("output")


def get_video_dir() -> Path:
    """返回视频保存目录"""
    d = OUTPUT_DIR / "videos"
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_screenshot_dir(case_name: str, timestamp: str) -> Path:
    """返回截图保存目录"""
    d = OUTPUT_DIR / "screenshots" / f"{case_name}_{timestamp}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def list_videos() -> list[Path]:
    """列出所有已录制的视频"""
    d = get_video_dir()
    return sorted(d.glob("*.webm")) + sorted(d.glob("*.mp4"))
