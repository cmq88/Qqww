import os
import yt_dlp
import logging
import asyncio
import tempfile
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ParseMode
from telegram.ext import Updater, CommandHandler, CallbackQueryHandler, MessageHandler, Filters, CallbackContext
from telegram.error import NetworkError, TelegramError
import re
import json
from typing import Dict, List, Optional
import shutil
import math

# إعداد التسجيل
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ثوابت
MAX_FILE_SIZE = 300 * 1024 * 1024  # 300 MB
DOWNLOAD_PATH = tempfile.mkdtemp()  # استخدام مجلد مؤقت
TOKEN = os.environ.get('BOT_TOKEN')  # الحصول على التوكن من متغيرات البيئة

class YouTubeBot:
    def __init__(self):
        """تهيئة البوت"""
        self.active_downloads: Dict[int, bool] = {}
        self.download_stats = {'total': 0, 'successful': 0, 'failed': 0}
        
        # إنشاء مجلد التنزيلات المؤقت
        if not os.path.exists(DOWNLOAD_PATH):
            os.makedirs(DOWNLOAD_PATH)

    async def get_video_info(self, url: str) -> dict:
        """الحصول على معلومات الفيديو مع دعم الملفات الكبيرة"""
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'extract_flat': 'in_playlist'
        }
        
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = await asyncio.get_event_loop().run_in_executor(
                    None, 
                    lambda: ydl.extract_info(url, download=False)
                )
                
                if 'entries' in info:
                    return {
                        'type': 'playlist',
                        'title': info.get('title', 'قائمة تشغيل'),
                        'videos': [{
                            'title': video.get('title', 'بدون عنوان'),
                            'url': video.get('url', ''),
                            'duration': video.get('duration', 0)
                        } for video in info['entries'] if video][:10]
                    }
                
                formats = []
                for f in info.get('formats', []):
                    if f.get('vcodec') != 'none':  # تحقق من أن التنسيق يحتوي على فيديو
                        # إضافة جميع الجودات المتاحة حتى 300 ميجا
                        filesize = f.get('filesize', 0)
                        if filesize == 0 or filesize <= MAX_FILE_SIZE:
                            formats.append({
                                'format_id': f.get('format_id'),
                                'quality': f.get('height', 0),
                                'filesize': filesize,
                                'ext': f.get('ext', 'mp4')
                            })
                
                return {
                    'type': 'video',
                    'title': info.get('title', 'بدون عنوان'),
                    'duration': info.get('duration', 0),
                    'formats': sorted(formats, key=lambda x: x['quality'], reverse=True)
                }
        
        except Exception as e:
            logger.error(f"خطأ في استخراج معلومات الفيديو: {str(e)}")
            raise

    async def download_media(self, url: str, format_type: str, quality: Optional[int] = None) -> str:
        """تحميل الوسائط مع دعم الملفات الكبيرة"""
        file_name = f"download_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        output_path = os.path.join(DOWNLOAD_PATH, file_name)
        
        ydl_opts = {
            'outtmpl': f'{output_path}.%(ext)s',
            'quiet': True,
        }

        if format_type == 'audio':
            ydl_opts.update({
                'format': 'bestaudio/best',
                'postprocessors': [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'mp3',
                    'preferredquality': '192',
                }],
            })
        else:
            # تحديد أفضل جودة متاحة تحت الحد الأقصى للحجم
            ydl_opts['format'] = f'bestvideo[height<={quality}][filesize<{MAX_FILE_SIZE}]+bestaudio/best[filesize<{MAX_FILE_SIZE}]'
            ydl_opts['merge_output_format'] = 'mp4'

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: ydl.extract_info(url, download=True)
                )
                return ydl.prepare_filename(info)
        except Exception as e:
            logger.error(f"خطأ في تحميل الوسائط: {str(e)}")
            raise

    async def send_large_file(self, file_path: str, chat_id: int, message_id: int, is_audio: bool = False) -> None:
        """إرسال الملف الكبير مع إظهار التقدم"""
        file_size = os.path.getsize(file_path)
        sent_size = 0
        chunk_size = 1024 * 1024  # 1MB chunks for progress updates
        
        try:
            with open(file_path, 'rb') as file:
                if is_audio:
                    await context.bot.send_audio(
                        chat_id=chat_id,
                        audio=file,
                        caption="🎵 تم التحميل بواسطة @YourBot",
                        progress=self.upload_progress_callback,
                        progress_args=(message_id, chat_id)
                    )
                else:
                    await context.bot.send_video(
                        chat_id=chat_id,
                        video=file,
                        caption=f"🎥 تم التحميل بواسطة @YourBot",
                        progress=self.upload_progress_callback,
                        progress_args=(message_id, chat_id)
                    )
        except Exception as e:
            logger.error(f"Error sending large file: {str(e)}")
            raise

    async def upload_progress_callback(self, current, total, message_id, chat_id):
        """تحديث تقدم الرفع"""
        progress = (current / total) * 100
        try:
            if progress % 10 == 0:  # تحديث كل 10%
                await context.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=f"📤 جاري الرفع: {progress:.1f}%"
                )
        except Exception as e:
            logger.error(f"Error updating progress: {str(e)}")

    def create_video_keyboard(self, url: str, info: dict) -> List[List[InlineKeyboardButton]]:
        """إنشاء أزرار التحميل للفيديو مع دعم الملفات الكبيرة"""
        keyboard = [
            [InlineKeyboardButton("🎵 تحميل كصوت MP3", callback_data=f"audio|{url}")]
        ]
        
        for fmt in info['formats']:
            if fmt['quality'] >= 360:
                size = self.format_size(fmt['filesize'])
                # إضافة تحذير للملفات الكبيرة
                size_warning = "⚠️ " if fmt['filesize'] > 100 * 1024 * 1024 else ""
                keyboard.append([
                    InlineKeyboardButton(
                        f"{size_warning}🎥 {fmt['quality']}p - {size}",
                        callback_data=f"video|{fmt['quality']}|{url}"
                    )
                ])
        
        return keyboard

    @staticmethod
    def format_size(size: int) -> str:
        """تنسيق حجم الملف بشكل أفضل"""
        if size == 0:
            return "حجم غير معروف"
        size_names = ["B", "KB", "MB", "GB"]
        i = math.floor(math.log(size, 1024))
        p = math.pow(1024, i)
        s = round(size/p, 2)
        return f"{s} {size_names[i]}"

    async def handle_single_download(self, query, url: str, format_type: str, quality: Optional[int], status_message) -> None:
        """معالجة تحميل فيديو واحد مع دعم الملفات الكبيرة"""
        try:
            await status_message.edit_text("🔍 جاري تحليل الفيديو...")
            file_path = await self.download_media(url, format_type, quality)
            
            if not os.path.exists(file_path):
                raise FileNotFoundError("لم يتم العثور على الملف بعد التحميل")

            file_size = os.path.getsize(file_path)
            if file_size > MAX_FILE_SIZE:
                raise Exception(f"حجم الملف ({self.format_size(file_size)}) أكبر من الحد المسموح به ({self.format_size(MAX_FILE_SIZE)})")

            await status_message.edit_text("📤 جاري الرفع...")
            
            await self.send_large_file(
                file_path,
                query.message.chat_id,
                status_message.message_id,
                is_audio=(format_type == 'audio')
            )

            await status_message.edit_text("✅ تم التحميل بنجاح!")

        except Exception as e:
            raise Exception(f"فشل التحميل: {str(e)}")
        finally:
            # تنظيف الملفات المؤقتة
            if os.path.exists(file_path):
                os.remove(file_path)

def main():
    """تشغيل البوت"""
    # التحقق من وجود التوكن
    if not TOKEN:
        logger.error("لم يتم تعيين BOT_TOKEN في متغيرات البيئة")
        return

    # إنشاء البوت
    bot = YouTubeBot()
    updater = Updater(TOKEN, use_context=True)
    dp = updater.dispatcher

    # إضافة المعالجات
    dp.add_handler(CommandHandler("start", bot.start))
    dp.add_handler(MessageHandler(Filters.text & ~Filters.command, bot.handle_message))
    dp.add_handler(CallbackQueryHandler(bot.button_callback))

    # بدء البوت
    updater.start_polling()
    logger.info("تم بدء تشغيل البوت...")
    updater.idle()

if __name__ == '__main__':
    main()