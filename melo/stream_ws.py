import asyncio
import json
import uuid
from typing import Dict, Tuple

from websockets.server import serve, WebSocketServerProtocol

from .api import TTS

# Protocol constants
VERSION = 0b0001


class MsgType:
    FULL_RESPONSE = 0
    AUDIO_ONLY = 1


class Event:
    START_CONNECTION = 1
    CONNECTION_STARTED = 2
    START_SESSION = 100
    SESSION_STARTED = 101
    TASK_REQUEST = 200
    TTS_RESPONSE = 96
    FINISH_SESSION = 102
    SESSION_FINISHED = 152
    FINISH_CONNECTION = 3
    CONNECTION_FINISHED = 4
    ERROR = 999


MAX_CONCURRENCY = 100
_tts_semaphore = asyncio.Semaphore(MAX_CONCURRENCY)
_tts_model = TTS(language="ZH")
_speaker_ids = _tts_model.hps.data.spk2id


def pack_frame(msg_type: int, event_type: int, payload: Dict, audio_data: bytes = b"") -> bytes:
    """Pack a frame according to the custom binary protocol."""
    header = (VERSION << 12) | (msg_type << 8) | event_type
    header_bytes = header.to_bytes(2, "big")
    payload_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    payload_len = len(payload_bytes).to_bytes(4, "big")
    if msg_type == MsgType.AUDIO_ONLY:
        audio_len = len(audio_data).to_bytes(4, "big")
        return header_bytes + payload_len + payload_bytes + audio_len + audio_data
    return header_bytes + payload_len + payload_bytes


def unpack_frame(data: bytes) -> Tuple[int, int, int, Dict]:
    header = int.from_bytes(data[:2], "big")
    version = (header >> 12) & 0xF
    msg_type = (header >> 8) & 0xF
    event_type = header & 0xFF
    payload_len = int.from_bytes(data[2:6], "big")
    payload_bytes = data[6:6 + payload_len]
    payload = json.loads(payload_bytes.decode("utf-8")) if payload_len else {}
    return version, msg_type, event_type, payload


def pcm16(audio) -> bytes:
    """Convert float numpy array to 16-bit PCM bytes."""
    import numpy as np

    audio = (audio * 32767).astype(np.int16)
    return audio.tobytes()


async def synthesize(text: str, speaker_id: int) -> bytes:
    loop = asyncio.get_running_loop()
    audio = await loop.run_in_executor(None, lambda: _tts_model.tts_to_file(text, speaker_id))
    pcm = pcm16(audio)
    del audio  # release numpy array promptly
    return pcm


async def handle_connection(ws: WebSocketServerProtocol):
    session_id = str(uuid.uuid4())
    speaker_id = None
    try:
        async for message in ws:
            version, msg_type, event_type, payload = unpack_frame(message)
            if event_type == Event.START_CONNECTION:
                await ws.send(
                    pack_frame(
                        MsgType.FULL_RESPONSE,
                        Event.CONNECTION_STARTED,
                        {"session_id": session_id, "message": "连接已建立"},
                    )
                )
            elif event_type == Event.START_SESSION:
                role_name = payload.get("role_name")
                if role_name not in _speaker_ids:
                    await ws.send(
                        pack_frame(
                            MsgType.FULL_RESPONSE,
                            Event.ERROR,
                            {"code": 1002, "message": "角色不存在"},
                        )
                    )
                    continue
                speaker_id = _speaker_ids[role_name]
                await ws.send(
                    pack_frame(
                        MsgType.FULL_RESPONSE,
                        Event.SESSION_STARTED,
                        {"session_id": session_id, "role_name": role_name},
                    )
                )
            elif event_type == Event.TASK_REQUEST:
                if speaker_id is None:
                    await ws.send(
                        pack_frame(
                            MsgType.FULL_RESPONSE,
                            Event.ERROR,
                            {"code": 1001, "message": "会话未启动"},
                        )
                    )
                    continue
                text = payload.get("text", "")
                async with _tts_semaphore:
                    try:
                        audio_bytes = await synthesize(text, speaker_id)
                    except Exception as exc:  # pragma: no cover - log error
                        await ws.send(
                            pack_frame(
                                MsgType.FULL_RESPONSE,
                                Event.ERROR,
                                {"code": 999, "message": str(exc)},
                            )
                        )
                        continue
                await ws.send(
                    pack_frame(
                        MsgType.AUDIO_ONLY,
                        Event.TTS_RESPONSE,
                        {"session_id": session_id},
                        audio_bytes,
                    )
                )
                del audio_bytes  # free audio buffer
            elif event_type == Event.FINISH_SESSION:
                await ws.send(
                    pack_frame(
                        MsgType.FULL_RESPONSE,
                        Event.SESSION_FINISHED,
                        {"session_id": session_id},
                    )
                )
                speaker_id = None
            elif event_type == Event.FINISH_CONNECTION:
                await ws.send(
                    pack_frame(
                        MsgType.FULL_RESPONSE,
                        Event.CONNECTION_FINISHED,
                        {"session_id": session_id},
                    )
                )
                break
    finally:
        try:
            await ws.close()
            await ws.wait_closed()
        except Exception:
            pass


async def main(host: str = "0.0.0.0", port: int = 8010):
    async with serve(handle_connection, host, port, max_size=None):
        await asyncio.Future()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="MeloTTS streaming WebSocket service")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8010)
    args = parser.parse_args()

    asyncio.run(main(args.host, args.port))
