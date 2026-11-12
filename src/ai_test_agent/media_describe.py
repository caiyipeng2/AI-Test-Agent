"""Describe video keyframes as requirement evidence.

The bot extracts frames with ffmpeg and hands the directory here; this module
only owns the model call and the output contract. Audio is never transcribed.
"""

import hashlib
import json
from pathlib import Path

from .model_providers import ProviderError, load_provider

MAX_FRAMES = 8

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["summary", "scenes", "uncertain"],
    "properties": {
        "summary": {"type": "string"},
        "scenes": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["frame", "description"],
                "properties": {
                    "frame": {"type": "integer"},
                    "description": {"type": "string"},
                },
            },
        },
        "uncertain": {"type": "array", "items": {"type": "string"}},
    },
}

PROMPT = """你是需求评审助手。下面是从需求文档内嵌视频中均匀抽取的关键帧（按时间顺序）。
请只根据画面内容描述：每帧展示的界面、操作或效果，并总结视频想表达的需求意图。
不要臆测画面中不存在的信息；无法确认的内容写入 uncertain。
视频音频未转写，不要评价旁白或音效。"""


def _sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def describe_frames(frames_dir, provider_config, output_path, *, allow_model_access=False):
    if type(allow_model_access) is not bool:
        raise ValueError("allow_model_access must be an explicit boolean")
    if not allow_model_access:
        raise ProviderError("MODEL_ACCESS_NOT_AUTHORIZED", "Use --allow-model to authorize this media call")

    frames = sorted(Path(frames_dir).glob("frame-*.png"))[:MAX_FRAMES]
    if not frames:
        raise ValueError("no frame-*.png files were found")

    provider = load_provider(provider_config)
    if not getattr(provider, "supports_images", False):
        raise ProviderError("MODEL_IMAGES_UNSUPPORTED", "The configured provider cannot analyze video frames")

    images = [
        {"path": str(path.resolve()), "image_id": f"frame-{index + 1}", "sha256": _sha256(path)}
        for index, path in enumerate(frames)
    ]
    response = provider.generate(PROMPT, SCHEMA, images, Path(output_path).parent / "frames")
    payload = response.get("payload")
    if not isinstance(payload, dict):
        raise ValueError("provider returned no payload")
    if not isinstance(payload.get("summary"), str) or payload["summary"].strip() == "":
        raise ValueError("summary must be a non-empty string")
    scenes = payload.get("scenes")
    if not isinstance(scenes, list) or not scenes:
        raise ValueError("scenes must be a non-empty list")
    for scene in scenes:
        if not isinstance(scene, dict) or type(scene.get("frame")) is not int or not isinstance(scene.get("description"), str):
            raise ValueError("each scene must contain frame and description")
    uncertain = payload.get("uncertain")
    if not isinstance(uncertain, list) or any(not isinstance(item, str) for item in uncertain):
        raise ValueError("uncertain must be a list of strings")

    metadata = response.get("metadata") or {}
    expected = [image["image_id"] for image in images]
    if metadata.get("attached_image_ids") != expected:
        raise ProviderError(
            "MODEL_ATTACHMENT_EVIDENCE_MISMATCH",
            "Provider attachment evidence does not match the submitted frames",
        )

    result = {
        "summary": payload["summary"].strip(),
        "scenes": scenes,
        "uncertain": uncertain,
        "frames": expected,
        "provider_id": getattr(provider, "provider_id", "unknown"),
        "model_id": getattr(provider, "model_id", "unknown"),
    }
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


__all__ = ["describe_frames"]
