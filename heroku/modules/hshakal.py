import asyncio
import logging
import tempfile
from pathlib import Path

from PIL import Image
from herokutl.tl.types import (
    DocumentAttributeImageSize,
    DocumentAttributeSticker,
    InputStickerSetEmpty,
    Message,
)

from .. import loader, utils

logger = logging.getLogger(__name__)


@loader.tds
class HShakalMod(loader.Module):
    """Reduces the quality of replied photos, videos and static stickers."""

    strings = {
        "name": "h:shakal",
        "no_reply": "<b>Reply to a photo, video or static sticker.</b>",
        "unsupported": "<b>This media type is not supported.</b>",
        "too_large": "<b>The file is too large. Maximum size: 100 MB.</b>",
        "invalid_level": "<b>Quality reduction level must be from 1 to 50.</b>",
        "downloading": "<b>Downloading media...</b>",
        "processing": "<b>Reducing quality...</b>",
        "failed": "<b>Media processing failed.</b>",
        "busy": "<b>Too many media jobs are already running. Try again later.</b>",
        "_cmd_doc_shakal": "[1-50] <reply> - reduce media quality",
    }

    strings_ru = {
        "no_reply": "<b>Ответь на фото, видео или статичный стикер.</b>",
        "unsupported": "<b>Этот тип медиа пока не поддерживается.</b>",
        "too_large": "<b>Файл слишком большой. Максимальный размер: 100 МБ.</b>",
        "invalid_level": "<b>Уровень ухудшения должен быть от 1 до 50.</b>",
        "downloading": "<b>Скачиваю медиа...</b>",
        "processing": "<b>Ухудшаю качество...</b>",
        "failed": "<b>Не удалось обработать медиа.</b>",
        "busy": "<b>Уже выполняется слишком много обработок. Попробуй позже.</b>",
        "_cmd_doc_shakal": "[1-50] <реплай> - ухудшить качество медиа",
    }

    _MAX_MEDIA_SIZE = 100 * 1024 * 1024
    _PROCESS_TIMEOUT = 180

    def __init__(self):
        self._jobs = asyncio.Semaphore(2)

    @loader.command()
    async def ffmpeg(self, message: Message):
        """Show the ffmpeg installation command."""
        await utils.answer(
            message,
            "команда для установки ffmpeg\n"
            '<pre><code class="language-bash">.terminal sudo apt update &amp;&amp; '
            "sudo apt install ffmpeg libavcodec-dev libavutil-dev libavformat-dev "
            "libswscale-dev libavdevice-dev -y</code></pre>",
        )

    @loader.command(alias="шакал")
    async def shakal(self, message: Message):
        """[1-50] <reply> - reduce media quality"""
        reply = await message.get_reply_message()
        if not reply:
            await utils.answer(message, self.strings["no_reply"])
            return

        media_file = getattr(reply, "file", None)
        mime_type = getattr(media_file, "mime_type", "") or ""
        is_sticker = bool(getattr(reply, "sticker", None))
        is_photo = bool(getattr(reply, "photo", None)) or mime_type.startswith("image/")
        is_video = mime_type.startswith("video/")
        is_static_sticker = is_sticker and not is_video

        if is_sticker and mime_type == "application/x-tgsticker":
            await utils.answer(message, self.strings["unsupported"])
            return

        if not any((is_sticker, is_photo, is_video)):
            await utils.answer(message, self.strings["unsupported"])
            return

        if getattr(media_file, "size", 0) > self._MAX_MEDIA_SIZE:
            await utils.answer(message, self.strings["too_large"])
            return

        level = 25
        if raw_level := utils.get_args_raw(message).strip():
            try:
                level = int(raw_level)
            except ValueError:
                level = 0

        if not 1 <= level <= 50:
            await utils.answer(message, self.strings["invalid_level"])
            return

        if self._jobs.locked():
            await utils.answer(message, self.strings["busy"])
            return

        async with self._jobs:
            status = await utils.answer(message, self.strings["downloading"])
            try:
                with tempfile.TemporaryDirectory(prefix="ratko-shakal-") as temp_dir:
                    temp_path = Path(temp_dir)
                    suffix = Path(getattr(media_file, "name", "") or "").suffix
                    input_path = temp_path / f"input{suffix or self._suffix(mime_type)}"

                    downloaded = await reply.download_media(file=str(input_path))
                    if not downloaded or not input_path.is_file():
                        raise RuntimeError("Telegram returned no downloaded media")

                    status = await utils.answer(status, self.strings["processing"])
                    if is_video:
                        output_path = temp_path / "output.mp4"
                        await self._process_video(input_path, output_path, level)
                    elif is_static_sticker:
                        output_path = temp_path / "output.webp"
                        dimensions = await asyncio.to_thread(
                            self._process_image,
                            input_path,
                            output_path,
                            level,
                            True,
                        )
                    else:
                        output_path = temp_path / "output.jpg"
                        await asyncio.to_thread(
                            self._process_image,
                            input_path,
                            output_path,
                            level,
                            False,
                        )

                    if not output_path.is_file() or output_path.stat().st_size == 0:
                        raise RuntimeError("Media processor produced an empty file")

                    await self._client.send_file(
                        message.peer_id,
                        str(output_path),
                        reply_to=reply.id,
                        force_document=False,
                        **(
                            {
                                "mime_type": "image/webp",
                                "attributes": [
                                    DocumentAttributeSticker(
                                        alt="",
                                        stickerset=InputStickerSetEmpty(),
                                    ),
                                    DocumentAttributeImageSize(
                                        w=dimensions[0],
                                        h=dimensions[1],
                                    ),
                                ],
                            }
                            if is_static_sticker
                            else {}
                        ),
                    )
                    await status.delete()
            except Exception:
                logger.exception("Failed to reduce media quality")
                await utils.answer(status, self.strings["failed"])

    @staticmethod
    def _suffix(mime_type: str) -> str:
        if mime_type.startswith("video/"):
            return ".mp4"
        if mime_type == "image/webp":
            return ".webp"
        return ".jpg"

    @staticmethod
    def _process_image(
        input_path: Path,
        output_path: Path,
        level: int,
        webp: bool,
    ):
        with Image.open(input_path) as source:
            image = source.convert("RGBA" if webp else "RGB")
            scale = max(0.08, 1 - (level - 1) * 0.92 / 49)
            width = max(16, round(image.width * scale))
            height = max(16, round(image.height * scale))
            image = image.resize((width, height), Image.Resampling.LANCZOS)
            quality = max(2, round(70 - (level - 1) * 68 / 49))

            if webp:
                image.save(output_path, "WEBP", quality=quality, method=4)
            else:
                image.save(output_path, "JPEG", quality=quality, optimize=True)

            return image.size

    async def _process_video(
        self,
        input_path: Path,
        output_path: Path,
        level: int,
    ):
        bitrate = max(30, round(1200 - (level - 1) * 1170 / 49))
        audio_bitrate = max(8, round(96 - (level - 1) * 88 / 49))
        fps = max(5, round(30 - (level - 1) * 25 / 49))
        width = max(120, round(720 - (level - 1) * 600 / 49))
        width -= width % 2

        process = await asyncio.create_subprocess_exec(
            "ffmpeg",
            "-nostdin",
            "-y",
            "-i",
            str(input_path),
            "-vf",
            f"scale={width}:-2",
            "-r",
            str(fps),
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-b:v",
            f"{bitrate}k",
            "-c:a",
            "aac",
            "-b:a",
            f"{audio_bitrate}k",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-movflags",
            "+faststart",
            str(output_path),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            _, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=self._PROCESS_TIMEOUT,
            )
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            raise RuntimeError("ffmpeg timed out") from None

        if process.returncode:
            details = stderr.decode(errors="replace")[-1000:]
            raise RuntimeError(f"ffmpeg exited with {process.returncode}: {details}")
