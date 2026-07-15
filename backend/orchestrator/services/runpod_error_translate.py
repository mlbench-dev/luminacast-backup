"""Shared translation of localized RunPod error messages to English.

RunPod workers occasionally return Korean error strings (e.g. when a Korean
team's container is processing the job). Normalize at every surface the error
hits — both the synchronous `wait_for_completion` failure path and the async
webhook callback — so users see readable English in toasts/logs.
"""

# Korean → English. Add new entries here as we observe them in production.
_TRANSLATIONS = {
    "비디오를 찾을 수 없습니다": "Video not found",
    "오디오 파일을 찾을 수 없습니다": "Audio file not found",
    "입력 이미지를 찾을 수 없습니다": "Input image not found",
    "처리 중 오류가 발생했습니다": "Processing error occurred",
    "메모리 부족": "Out of memory",
    "시간 초과": "Timeout",
}


def normalize_error_message(msg: str) -> str:
    """Translate known localized error strings to English.

    Returns the original message if no translation matches. Safe to call on
    None or empty strings.
    """
    if not msg:
        return msg
    for source, target in _TRANSLATIONS.items():
        if source in msg:
            msg = msg.replace(source, target)
    return msg
