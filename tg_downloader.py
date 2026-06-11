#!/usr/bin/env python3
"""
Telegram 受限内容下载器
原理: 通过 MTProto 协议用用户账号直接访问 Telegram 服务器获取文件内容，
      绕过频道"禁止转发/保存"限制。

前置准备:
  1. 安装依赖: pip install telethon tqdm
  2. 运行脚本，首次会要求输入手机号和验证码登录

用法:
  # 下载单条消息中的媒体
  python tg_restricted_downloader.py "https://t.me/channel_name/123"

  # 下载某个频道的最近 N 条消息中的媒体
  python tg_restricted_downloader.py --channel "@channel_name" --limit 50

  # 交互模式
  python tg_restricted_downloader.py --interactive
"""

import asyncio
import os
import re
import sys
import argparse
from pathlib import Path
from datetime import datetime, date
from telethon import TelegramClient, events
from telethon.tl.types import (
    MessageMediaPhoto,
    MessageMediaDocument,
    MessageMediaWebPage,
)
from tqdm import tqdm

# ======================== 配置区 ========================
# 使用 Telegram 官方桌面客户端开源代码中的 API 凭证（公开信息）
# 如果你有自己的 API_ID/API_HASH，替换掉即可
API_ID = 2040
API_HASH = "b18441a1ff607e10a989891a5462e627"
SESSION_NAME = "tg_user_session"  # 会话文件名，登录一次后无需重复登录
DOWNLOAD_DIR = Path(__file__).parent / "downloads"  # 下载保存目录（本程序所在目录下）

# ======================== 代理配置 ========================
# 国内用户必须配置代理才能连接 Telegram
# 根据你使用的代理软件选择对应端口:
#   Clash Verge:  socks5://127.0.0.1:7890  (常见)
#   Clash 旧版:   socks5://127.0.0.1:7891
#   V2RayN:       socks5://127.0.0.1:10808
#   Shadowsocks:  socks5://127.0.0.1:1080
#   HTTP 代理:    http://127.0.0.1:7890

# 将下面两行取消注释并填写你的代理地址

USE_PROXY = False
PROXY = ("socks5", "127.0.0.1", 7890)  # 需要代理时改为你的代理地址

# 如果上面这样配不行，试试 HTTP 代理:
# PROXY = ("http", "127.0.0.1", 7890)

# ======================== 核心逻辑 ========================

DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)


def detect_file_type(filepath):
    """通过文件头魔数检测真实文件类型，返回正确扩展名"""
    magic_map = {
        # (偏移, 魔数字节): 扩展名
        b"\xff\xd8\xff": ".jpg",
        b"\x89PNG\r\n\x1a\n": ".png",
        b"GIF8": ".gif",
        b"RIFF": (".avi", 8, b"AVI "),   # RIFF .... AVI
        b"\x1aE\xdf\xa3": ".mkv",        # EBML (MKV/WebM)
        b"\x00\x00\x00": (".mov", 4, b"ftyp"),  # QuickTime/MP4
        b"ftyp": (".mp4", 0, b"ftyp"),   # MP4 fragment
        b"%PDF": ".pdf",
        b"PK\x03\x04": ".zip",
        b"Rar!\x1a\x07": ".rar",
        b"ID3": ".mp3",
        b"\xff\xfb": ".mp3",
        b"\xff\xf3": ".mp3",
        b"\xff\xfa": ".mp3",
        b"\x00\x00\x01\xba": ".mpg",
        b"\x00\x00\x01\xb3": ".mpg",
    }

    try:
        with open(filepath, "rb") as f:
            header = f.read(16)
    except Exception:
        return None

    if len(header) < 4:
        return None

    # Check for MP4/MOV (starts with ftyp at offset 4)
    if header[4:8] == b"ftyp":
        brand = header[8:12]
        if brand in (b"mp42", b"isom", b"avc1", b"M4V "):
            return ".mp4"
        if brand in (b"qt  ", b"M4P "):
            return ".mov"
        return ".mp4"  # default

    # Check for WebM (EBML header with webm doctype)
    if header[:4] == b"\x1aE\xdf\xa3":
        # Try to find "webm" in header
        if b"webm" in header:
            return ".webm"
        return ".mkv"

    # Check for AVI (RIFF .... AVI)
    if header[:4] == b"RIFF" and header[8:12] == b"AVI ":
        return ".avi"

    # Check for WMV (ASF header)
    if header[:4] == b"0&\xb2u":
        return ".wmv"

    # Standard checks
    for magic, ext in [
        (b"\xff\xd8\xff", ".jpg"),
        (b"\x89PNG", ".png"),
        (b"GIF8", ".gif"),
        (b"%PDF", ".pdf"),
        (b"PK\x03\x04", ".zip"),
        (b"Rar!\x1a\x07", ".rar"),
    ]:
        if header[:len(magic)] == magic:
            return ext

    # MP3 checks
    if header[:3] in (b"ID3", b"\xff\xfb", b"\xff\xf3", b"\xff\xfa"):
        return ".mp3"

    # MPEG check
    if header[:4] in (b"\x00\x00\x01\xba", b"\x00\x00\x01\xb3"):
        return ".mpg"

    # OGG check
    if header[:4] == b"OggS":
        return ".ogg"

    return None


def auto_rename_if_needed(filepath):
    """如果文件扩展名不匹配实际类型，自动改名"""
    if not os.path.exists(filepath):
        return filepath

    current_ext = os.path.splitext(filepath)[1].lower()
    known_exts = {".jpg", ".png", ".gif", ".mp4", ".mkv", ".webm", ".mov",
                  ".avi", ".wmv", ".mpg", ".mp3", ".m4a", ".ogg", ".pdf",
                  ".zip", ".rar"}

    # If already has a known extension, skip
    if current_ext in known_exts:
        return filepath

    # Detect actual type from magic bytes
    detected = detect_file_type(filepath)
    if detected and detected != current_ext:
        new_path = os.path.splitext(filepath)[0] + detected
        # Avoid overwriting
        counter = 1
        while os.path.exists(new_path):
            stem = os.path.splitext(filepath)[0]
            new_path = f"{stem}({counter}){detected}"
            counter += 1
        os.rename(filepath, new_path)
        print(f"  [FIX] 自动识别文件类型 -> {detected}: {os.path.basename(new_path)}")
        return new_path

    return filepath


async def download_media_from_message(client, message, target_dir=DOWNLOAD_DIR):
    """从一条消息中下载媒体文件（绕过保存限制）"""
    if not message or not message.media:
        return None

    media = message.media

    # 处理网页预览中的媒体（有些受限频道用 WebPage 包装）
    if isinstance(media, MessageMediaWebPage):
        if hasattr(media.webpage, "photo") and media.webpage.photo:
            media = media.webpage.photo
        elif hasattr(media.webpage, "document") and media.webpage.document:
            media = media.webpage.document
        else:
            print("   网页预览中无可下载媒体")
            return None
    elif isinstance(media, MessageMediaPhoto):
        pass  # telethon 会直接处理
    elif isinstance(media, MessageMediaDocument):
        pass  # telethon 会直接处理
    else:
        print(f"   不支持的媒体类型: {type(media).__name__}")
        return None

    # 获取文件名 — 优先用原始文件名，否则从 MIME 类型推断
    file_name = None
    mime_ext_map = {
        "video/mp4": ".mp4",
        "video/x-matroska": ".mkv",
        "video/webm": ".webm",
        "video/quicktime": ".mov",
        "video/x-msvideo": ".avi",
        "video/x-ms-wmv": ".wmv",
        "video/mpeg": ".mpg",
        "image/gif": ".gif",
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
        "audio/mpeg": ".mp3",
        "audio/mp4": ".m4a",
        "audio/ogg": ".ogg",
        "application/pdf": ".pdf",
        "application/zip": ".zip",
        "application/x-rar-compressed": ".rar",
    }

    # 方法1: message.file.name
    if hasattr(message, "file") and message.file and message.file.name:
        file_name = message.file.name

    # 方法2: document 属性中的文件名
    if not file_name and hasattr(media, "document") and media.document:
        for attr in media.document.attributes:
            if hasattr(attr, "file_name") and attr.file_name:
                file_name = attr.file_name
                break

    # 方法3: 根据 MIME 类型推断扩展名
    if not file_name and hasattr(media, "document") and media.document:
        mime = media.document.mime_type
        ext = mime_ext_map.get(mime)
        if ext:
            file_name = f"{message.id}_{message.date.strftime('%Y%m%d_%H%M%S')}{ext}"
        else:
            # 未知 MIME，但至少知道是 document（很可能是视频）
            file_name = f"{message.id}_{message.date.strftime('%Y%m%d_%H%M%S')}.mp4"

    # 兜底
    if not file_name:
        ext = ".jpg" if isinstance(media, MessageMediaPhoto) else ".mp4"
        file_name = f"{message.id}_{message.date.strftime('%Y%m%d_%H%M%S')}{ext}"

    # 清理文件名中的非法字符
    file_name = re.sub(r'[\\/:*?"<>|]', "_", file_name)

    # 如果目录下有重名文件，加序号
    save_path = target_dir / file_name
    counter = 1
    while save_path.exists():
        stem, ext = os.path.splitext(file_name)
        save_path = target_dir / f"{stem}({counter}){ext}"
        counter += 1

    print(f"   正在下载: {file_name}")
    print(f"     大小: {message.file.size / 1024 / 1024:.2f} MB" if message.file and message.file.size else "")

    # 核心: 通过用户会话直接下载文件 — 完全绕过客户端的"禁止保存"标记
    with tqdm(
        total=message.file.size if message.file and message.file.size else None,
        unit="B",
        unit_scale=True,
        desc=f"    进度",
        ncols=75,
    ) as pbar:
        downloaded_path = await client.download_media(
            message,
            file=str(save_path),
            progress_callback=lambda c, t: progress_callback(c, t, pbar),
        )

    if downloaded_path:
        # 自动检测文件头的真实类型，纠正 .bin / 错误扩展名
        fixed_path = auto_rename_if_needed(downloaded_path)
        print(f"   下载完成: {fixed_path}")
        return str(fixed_path)
    else:
        print(f"   下载失败")
        return None


async def download_from_link(client, link: str):
    """从消息链接下载媒体"""
    # 解析链接格式:
    # https://t.me/channel_name/message_id
    pattern = r"(?:https?://)?(?:www\.)?t\.me/([^/]+)/(\d+)(?:\?.*)?"
    match = re.match(pattern, link)
    if not match:
        print(f" 无效的链接格式: {link}")
        print("   支持的格式: https://t.me/channel_name/message_id")
        return

    channel_username, msg_id = match.groups()
    msg_id = int(msg_id)

    print(f" 正在获取消息: {link}")
    try:
        entity = await client.get_entity(channel_username)
        message = await client.get_messages(entity, ids=msg_id)
    except Exception as e:
        print(f" 获取消息失败: {e}")
        print("   请确认: 1) 你已加入该频道 2) 频道用户名/链接正确")
        return

    if not message:
        print(f" 未找到该消息")
        return

    print(f"   来自: {channel_username}")
    print(f"   日期: {message.date}")
    if message.text:
        preview = message.text[:100] + "..." if len(message.text) > 100 else message.text
        print(f"   内容: {preview}")

    await download_media_from_message(client, message)


def parse_date(date_str: str) -> datetime:
    """解析日期字符串，支持多种格式"""
    formats = [
        "%Y-%m-%d",
        "%Y/%m/%d",
        "%Y.%m.%d",
        "%Y%m%d",
        "%m-%d",       # 省略年份，默认今年
        "%m/%d",
    ]
    date_str = date_str.strip()
    for fmt in formats:
        try:
            dt = datetime.strptime(date_str, fmt)
            # 如果没给年份，补上当前年份
            if dt.year == 1900:
                dt = dt.replace(year=date.today().year)
            return dt
        except ValueError:
            continue
    raise ValueError(
        f"无法解析日期: '{date_str}'。支持的格式: 2025-01-15, 2025/01/15, 2025.01.15, 20250115, 01-15"
    )


async def download_from_channel(
    client,
    channel: str,
    limit: int = 50,
    offset_id: int = 0,
    from_date: str = None,
    to_date: str = None,
):
    """从频道下载消息中的所有媒体（支持日期过滤）"""
    # 解析日期
    dt_from = parse_date(from_date) if from_date else None
    dt_to = parse_date(to_date).replace(hour=23, minute=59, second=59) if to_date else None

    # 构建提示信息
    range_info = ""
    if dt_from:
        range_info += f" 从 {dt_from.strftime('%Y-%m-%d')}"
    if dt_to:
        range_info += f" 到 {dt_to.strftime('%Y-%m-%d')}"
    if limit:
        range_info += f" (最多 {limit} 条)"

    print(f" 正在获取 {channel}{range_info} 的消息...")

    # 处理数字频道ID（私密频道）
    target = channel
    if isinstance(channel, str):
        stripped = channel.lstrip("@")
        if stripped.startswith("-100") and stripped[4:].isdigit():
            target = int(stripped)
        elif stripped.lstrip("-").isdigit():
            target = int(stripped)

    try:
        entity = await client.get_entity(target)
    except Exception as e:
        print(f" 获取频道失败: {e}")
        return

    # 分页拉取所有匹配日期的消息
    all_media_messages = []
    batch_size = min(limit, 100) if limit else 100
    offset_id = 0
    reached_end = False

    while True:
        remaining = (limit - len(all_media_messages)) if limit else batch_size
        if remaining <= 0:
            break

        fetch_count = min(batch_size, remaining)

        if dt_to:
            # 有截止日期时，从截止日期开始往前翻
            messages = await client.get_messages(
                entity,
                limit=fetch_count,
                offset_date=dt_to,
                offset_id=offset_id,
            )
        else:
            messages = await client.get_messages(
                entity,
                limit=fetch_count,
                offset_id=offset_id,
            )

        if not messages:
            break

        filtered = []
        for m in messages:
            if not m.media:
                continue

            msg_date = m.date.replace(tzinfo=None)  # 去掉时区以便比较

            # 检查日期范围
            if dt_from and msg_date < dt_from:
                # 早于起始日期，后面更老的消息也不用看了
                reached_end = True
                break
            if dt_to and msg_date > dt_to:
                # 晚于截止日期，跳过（往前翻时会遇到）
                continue

            filtered.append(m)

        all_media_messages.extend(filtered)

        # 检查是否够数了
        if limit and len(all_media_messages) >= limit:
            all_media_messages = all_media_messages[:limit]
            break

        if reached_end:
            break

        # 用最后一条消息的 id 继续翻页
        if len(messages) < fetch_count:
            break  # 没更多消息了
        offset_id = messages[-1].id

    if not all_media_messages:
        print(" 未找到符合日期条件的媒体消息")
        return

    # 按时间正序排列
    all_media_messages.sort(key=lambda m: m.date)

    print(f"\n 找到 {len(all_media_messages)} 条含媒体的消息，开始下载...\n")

    success_count = 0
    for i, msg in enumerate(all_media_messages, 1):
        print(f"[{i}/{len(all_media_messages)}] 消息 ID: {msg.id}, 日期: {msg.date}")
        result = await download_media_from_message(client, msg)
        if result:
            success_count += 1
        print()

    print(f"\n 下载完成! 成功: {success_count}/{len(all_media_messages)}")
    print(f" 文件保存在: {DOWNLOAD_DIR.resolve()}")


async def list_dialogs(client, keyword=None, private_only=False):
    """列出所有已加入的频道/群组"""
    dialogs = await client.get_dialogs()

    if private_only:
        print(f"\n 私密频道/群组 (无公开用户名):\n")
    elif keyword:
        print(f"\n 搜索包含 '{keyword}' 的对话...\n")
    else:
        print("\n 所有已加入的对话:\n")

    public_channels = []
    private_channels = []
    groups = []
    bots = []
    users = []

    for d in dialogs:
        name = d.name or "(无名称)"
        uname = d.entity.username or ""
        entity_id = d.entity.id

        if keyword and keyword.lower() not in name.lower() and keyword.lower() not in uname.lower():
            continue

        has_username = hasattr(d.entity, "username") and d.entity.username
        if has_username:
            handle = f"@{d.entity.username}"
        else:
            handle = f"ID: {entity_id}"

        line = f"  {name:<35} {handle}"
        if d.is_channel:
            if has_username:
                public_channels.append(line)
            else:
                private_channels.append(line)
        elif d.is_group:
            groups.append(line)
        elif hasattr(d.entity, "bot") and d.entity.bot:
            bots.append(line)
        else:
            users.append(line)

    if private_only:
        # 仅显示私密频道/群组
        if private_channels:
            print(f"--- 私密频道 ({len(private_channels)} 个) ---")
            for c in private_channels:
                print(c)
        if groups:
            # 群组里找没有用户名的
            private_groups = [g for g in groups if "ID:" in g]
            if private_groups:
                print(f"\n--- 私密群组 ({len(private_groups)} 个) ---")
                for g in private_groups:
                    print(g)
        total = len(private_channels)
        if not total:
            print("  没有找到私密频道")
        else:
            print(f"\n总计: {total} 个私密频道")
        print()
        return

    if public_channels:
        print(f"--- 公开频道 ({len(public_channels)} 个) ---")
        for c in public_channels:
            print(c)
    if private_channels:
        print(f"\n--- 私密频道 ({len(private_channels)} 个) ---")
        for c in private_channels:
            print(c)
    if groups:
        print(f"\n--- 群组 ({len(groups)} 个) ---")
        for g in groups:
            print(g)
    if bots:
        print(f"\n--- Bot ({len(bots)} 个) ---")
        for b in bots[:10]:
            print(b)
    if users:
        print(f"\n--- 私聊 ({len(users)} 个) ---")
        for u in users[:10]:
            print(u)
        if len(users) > 10:
            print(f"  ... 还有 {len(users) - 10} 个")

    total = len(public_channels) + len(private_channels) + len(groups) + len(bots) + len(users)
    print(f"\n总计: {len(public_channels)} 公开频道, {len(private_channels)} 私密频道, "
          f"{len(groups)} 群组, {len(bots)} Bot, {len(users)} 私聊")
    print()


async def show_usage_guide():
    """显示完整使用指南"""
    guide = r"""

         Telegram 受限内容下载器 — 使用指南                    


【 程序原理 】
  本程序通过 MTProto 协议以你的用户身份直连 Telegram 服务器，
  绕过频道管理员设置的"禁止转发/保存"限制，将文件下载到本地。

  支持: 图片、视频、GIF、文件、语音、网页预览中的所有媒体。
  限制: 只能下载你已加入的频道/群组内容。
  安全: 所有数据仅保存在你本地，不上传任何服务器。


【 三种下载方式 】


  方式一：单条下载（消息链接）
    输入格式: https://t.me/频道名/消息ID
    获取方法: Telegram App 中长按消息  复制链接
            （受限频道可能无法复制链接，见下方说明）
    示例:
       https://t.me/my_channel/12345

                          

  方式二：批量下载（频道用户名 + 日期）
    输入格式: @频道名 [from 日期] [to 日期]
    日期格式: 2025-06-01 / 2025/06/01 / 2025.06.01 / 20250601
    示例:
       @my_channel from 2025-06-01 to 2025-06-30   下载6月全部
       @my_channel from 2025-06-01                 6月1日起至今
       @my_channel to 2025-12-31                    今年截至12月31日
       @my_channel                                  最近N条
    然后程序会询问下载条数:
      - 有日期过滤: 默认500条，输入0=不限制
      - 无日期过滤: 默认50条

                          

  方式三：私密频道下载（频道ID）
    有些频道没有公开用户名(@xxx)，只有数字ID。
    先用 /list 或 /search 找到频道ID，然后输入:
       -1002435148744 from 2025-06-01 to 2025-06-30


【 辅助命令 】


  /list          列出你所有已加入的频道/群组，含公开和私密频道
  /search 关键词  按名称搜索频道（支持模糊匹配）
  /logout        登出当前账户，下次运行重新登录
  /quit          退出程序


【 常见问题 】


  Q: 受限频道无法复制消息链接怎么办？
  A: 用方式二的日期批量下载代替。或者用 /list 找到频道后
     记下频道ID，用手动拼接链接。

  Q: 去哪里找频道用户名？
  A: 用 /list 列出所有频道，或用 /search 搜索关键词。

  Q: 下载的文件保存在哪？
  A: 本程序所在目录下的 telegram_downloads 文件夹。

  Q: 如何切换 Telegram 账户？
  A: 输入 /logout 登出，下次运行重新登录新手机号。
     或退出后用: python tg_restricted_downloader.py --session 新账户名 -i

  Q: 代理连不上怎么办？
  A: 编辑脚本顶部配置区的 PROXY 行，修改代理端口。


【 开始使用 】


  现在你可以:
   粘贴消息链接下载单条
   输入 @频道名 + 日期批量下载
   输入 /list 浏览所有频道

  当前已登录账户的所有操作均基于此账号权限。

"""
    print(guide)
    input("  按 Enter 键开始使用...")
    print()


async def interactive_mode(client, skip_guide=False):
    """交互模式"""
    if not skip_guide:
        await show_usage_guide()
    print("="*60)
    print("  Telegram 受限内容下载器 - 交互模式")
    print("="*60)
    print()
    print("  快捷提示:")
    print("    链接  下载单条     @频道  批量下载")
    print("    /list  频道列表    /search  搜索频道")
    print("    /logout  登出      /quit  退出")
    print()

    while True:
        try:
            user_input = input(" 请输入命令: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n 再见!")
            break

        if not user_input:
            continue

        if user_input.lower() == "/logout":
            # 删除 session 文件，下次运行重新登录
            session_file = Path(f"{SESSION_NAME}.session")
            if session_file.exists():
                session_file.unlink()
                print(f" 已登出，session 文件已删除。")
            else:
                print(" 当前未登录或 session 文件不存在。")
            print(" 请重新运行程序登录新账户。")
            sys.exit(0)

        if user_input.lower() in ("/quit", "/exit", "quit", "exit"):
            print(" 再见!")
            break

        # ----- 列出所有对话 -----
        if user_input.lower() == "/list":
            print("\n 正在获取你的所有对话...\n")
            dialogs = await client.get_dialogs()
            # 分类显示
            channels = []
            groups = []
            users = []
            for d in dialogs:
                name = d.name or "(无名称)"
                entity_id = d.entity.id
                if hasattr(d.entity, "username") and d.entity.username:
                    handle = f"@{d.entity.username}"
                else:
                    handle = f"ID: {entity_id}"
                line = f"  {name:<30} {handle}"
                if d.is_channel:
                    channels.append(line)
                elif d.is_group:
                    groups.append(line)
                else:
                    users.append(line)

            if channels:
                print(f"--- 频道 ({len(channels)} 个) ---")
                for c in channels:
                    print(c)
            if groups:
                print(f"\n--- 群组 ({len(groups)} 个) ---")
                for g in groups:
                    print(g)
            if users:
                print(f"\n--- 私聊/Bot ({len(users)} 个) ---")
                for u in users[:20]:  # 只显示前20个私聊
                    print(u)
                if len(users) > 20:
                    print(f"  ... 还有 {len(users) - 20} 个私聊")
            print(f"\n总计: {len(channels)} 个频道, {len(groups)} 个群组, {len(users)} 个私聊")
            print()
            continue

        # ----- 搜索对话 -----
        if user_input.lower().startswith("/search"):
            keyword = user_input[7:].strip()
            if not keyword:
                print("用法: /search 关键词")
                continue
            print(f"\n 搜索包含 '{keyword}' 的对话...\n")
            dialogs = await client.get_dialogs()
            found = False
            for d in dialogs:
                name = d.name or ""
                uname = d.entity.username or ""
                if keyword.lower() in name.lower() or keyword.lower() in uname.lower():
                    entity_id = d.entity.id
                    if hasattr(d.entity, "username") and d.entity.username:
                        handle = f"@{d.entity.username}"
                    else:
                        handle = f"ID: {entity_id}"
                    tag = "[频道]" if d.is_channel else ("[群组]" if d.is_group else "[私聊]")
                    print(f"  {tag} {name}")
                    print(f"       {handle}")
                    found = True
            if not found:
                print(f"  未找到包含 '{keyword}' 的对话")
            print()
            continue

        # 判断是链接还是频道用户名
        if "t.me/" in user_input:
            await download_from_link(client, user_input)
        elif user_input.startswith("@") or user_input.startswith("-100"):
            # 解析频道名/ID 和日期参数
            parts = user_input.split()
            channel_name = parts[0]
            from_date = None
            to_date = None

            i = 1
            while i < len(parts):
                if parts[i].lower() in ("from", "f") and i + 1 < len(parts):
                    from_date = parts[i + 1]
                    i += 2
                elif parts[i].lower() in ("to", "t") and i + 1 < len(parts):
                    to_date = parts[i + 1]
                    i += 2
                else:
                    i += 1

            # 次数限制
            if from_date or to_date:
                limit_input = input("   下载最多多少条? (0=不限制, 默认500): ").strip()
                limit = int(limit_input) if limit_input else 500
            else:
                try:
                    limit = int(input("   下载最近多少条? (默认50): ").strip() or "50")
                except ValueError:
                    limit = 50

            await download_from_channel(
                client, channel_name, limit=limit,
                from_date=from_date, to_date=to_date,
            )
        else:
            print(" 请输入 t.me 链接、@频道名、以 -100 开头的频道ID，或输入 /list /search 等命令")


# ======================== 批量克隆频道（下载+重上传） ========================

async def clone_to_own_channel(client, source_channel: str, target_channel: str, limit: int = 100):
    """将受限频道内容克隆到自己的频道（下载重上传 模式，绕过转发限制）"""
    print(f" 正在获取频道 @{source_channel} 的消息...")

    source = await client.get_entity(source_channel)
    target = await client.get_entity(target_channel)
    messages = await client.get_messages(source, limit=limit)

    print(f" 获取到 {len(messages)} 条消息\n")

    for i, msg in enumerate(reversed(messages), 1):  # 倒序保持原始顺序
        print(f"[{i}/{len(messages)}] 消息 ID: {msg.id}")
        try:
            if msg.media:
                # 先下载到临时文件
                temp_path = await client.download_media(msg, file=str(DOWNLOAD_DIR / "temp_clone"))
                if temp_path:
                    # 再作为新消息上传（不是转发，是新建消息）
                    caption = msg.text if msg.text else ""
                    await client.send_file(
                        target,
                        temp_path,
                        caption=caption,
                        parse_mode="html" if msg.text else None,
                    )
                    # 删除临时文件
                    os.remove(temp_path)
                    print(f"   已复制到目标频道")
            else:
                # 纯文本消息直接发送
                if msg.text:
                    await client.send_message(target, msg.text, parse_mode="html")
                    print(f"   文本已复制")
        except Exception as e:
            print(f"   此条失败: {e}")

        await asyncio.sleep(1)  # 避免触发频率限制

    print(f"\n 克隆完成! {len(messages)} 条消息已处理")


# ======================== 入口 ========================

async def main():
    parser = argparse.ArgumentParser(
        description="Telegram 受限内容下载器 - 绕过频道禁止转发/保存限制",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  %(prog)s "https://t.me/example_channel/1234"
  %(prog)s --channel "@example_channel" --limit 100
  %(prog)s --channel "@example_channel" --from 2025-06-01 --to 2025-06-30
  %(prog)s --channel "@example_channel" --from 2025-06-01 --limit 0
  %(prog)s --interactive
  %(prog)s --clone "@source" "@mytarget" --limit 500
        """,
    )
    parser.add_argument("link", nargs="?", help="Telegram 消息链接 (t.me/xxx/123)")
    parser.add_argument("--channel", "-c", help="频道用户名 (如 @channel_name)")
    parser.add_argument("--limit", "-l", type=int, default=50, help="批量下载条数 (默认50, 0=不限制)")
    parser.add_argument("--from", dest="from_date", help="起始日期，只下载此日期之后的消息 (如 2025-06-01)")
    parser.add_argument("--to", dest="to_date", help="截止日期，只下载此日期之前的消息 (如 2025-06-30)")
    parser.add_argument("--interactive", "-i", action="store_true", help="交互模式")
    parser.add_argument("--skip-guide", action="store_true", help="跳过使用指南，直接进入交互界面")
    parser.add_argument("--list", "-L", nargs="?", const="", metavar="KEYWORD",
                        help="列出所有已加入的频道/群组 (可选搜索关键词)")
    parser.add_argument("--private", action="store_true",
                        help="配合 --list 使用，只列出私密频道 (无用户名的频道)")
    parser.add_argument("--logout", action="store_true",
                        help="删除本地 session 文件，登出当前账户")
    parser.add_argument("--session", "-s", default="tg_user_session",
                        help="指定 session 文件名，用于多账户切换 (如 --session account2)")
    parser.add_argument("--clone", nargs=2, metavar=("SOURCE", "TARGET"),
                        help="克隆频道到自己的频道 (需要你已加入两个频道)")
    args = parser.parse_args()

    # 处理登出
    if args.logout:
        session_file = Path(f"{args.session}.session")
        if session_file.exists():
            session_file.unlink()
            print(f" 已登出，session 文件 '{session_file}' 已删除。")
            print("   下次运行程序时将需要重新登录。")
        else:
            print(f" session 文件 '{session_file}' 不存在，无需登出。")
        return

    # 使用指定的 session 名
    session_name = args.session

    # 创建客户端（用户模式，支持代理）
    proxy_config = None
    try:
        if USE_PROXY and PROXY:
            proxy_config = PROXY
    except NameError:
        pass  # 未配置代理则直连

    if proxy_config:
        proxy_type, proxy_host, proxy_port = proxy_config
        client = TelegramClient(
            session_name, API_ID, API_HASH,
            proxy=(proxy_type, proxy_host, proxy_port)
        )
        print(f"  使用代理: {proxy_type}://{proxy_host}:{proxy_port}")
    else:
        print("  提示: 如连接失败请在脚本配置区设置代理")
        client = TelegramClient(session_name, API_ID, API_HASH)

    print(" 正在连接 Telegram...")
    await client.start()

    me = await client.get_me()
    session_file = Path(f"{session_name}.session")
    if session_file.exists():
        print(f" 已登录: {me.first_name} (@{me.username}) | 手机: {me.phone or '未知'} | Session: {session_name}")
    else:
        print(f" 首次登录成功: {me.first_name} | Session 已保存: {session_name}")
    print()

    try:
        if args.list is not None:
            keyword = args.list.strip() if args.list else None
            await list_dialogs(client, keyword=keyword, private_only=args.private)
        elif args.clone:
            await clone_to_own_channel(client, args.clone[0], args.clone[1], args.limit)
        elif args.interactive:
            await interactive_mode(client, skip_guide=args.skip_guide)
        elif args.link:
            await download_from_link(client, args.link)
        elif args.channel:
            await download_from_channel(
                client, args.channel, args.limit,
                from_date=args.from_date, to_date=args.to_date,
            )
        else:
            # 无参数时进入交互模式
            await interactive_mode(client, skip_guide=args.skip_guide)
    finally:
        await client.disconnect()
        print("\n 已断开连接")


if __name__ == "__main__":
    asyncio.run(main())
