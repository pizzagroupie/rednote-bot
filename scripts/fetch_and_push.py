"""
小红书家装素材自动抓取 + Telegram推送
每天定时从Reddit等RSS源抓取高质量家装图片，推送到Telegram
"""

import feedparser
import json
import os
import re
import time
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, timezone, timedelta
from pathlib import Path

# ============================================================
# 配置区域
# ============================================================

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

RSS_FEEDS = [
    {
        "name": "RoomPorn",
        "url": "https://www.reddit.com/r/RoomPorn/.rss?limit=25",
        "type": "reddit"
    },
    {
        "name": "CozyPlaces",
        "url": "https://www.reddit.com/r/CozyPlaces/.rss?limit=25",
        "type": "reddit"
    },
    {
        "name": "InteriorDesign",
        "url": "https://www.reddit.com/r/InteriorDesign/.rss?limit=25",
        "type": "reddit"
    },
    {
        "name": "AmateurRoomPorn",
        "url": "https://www.reddit.com/r/AmateurRoomPorn/.rss?limit=25",
        "type": "reddit"
    },
    {
        "name": "Dezeen Interiors",
        "url": "https://www.dezeen.com/interiors/feed/",
        "type": "blog"
    },
    {
        "name": "Apartment Therapy",
        "url": "https://www.apartmenttherapy.com/main.rss",
        "type": "blog"
    },
]

HISTORY_FILE = Path(__file__).parent.parent / "data" / "sent_history.json"
MAX_POSTS_PER_RUN = 15

# ============================================================
# 工具函数
# ============================================================

def load_history():
    if HISTORY_FILE.exists():
        with open(HISTORY_FILE, "r") as f:
            data = json.load(f)
            cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
            return {k: v for k, v in data.items() if v.get("date", "") > cutoff}
    return {}


def save_history(history):
    HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(HISTORY_FILE, "w") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


def fetch_rss(url):
    """用浏览器UA获取RSS，避免被封"""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
    }
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            raw = response.read()
            return feedparser.parse(raw)
    except Exception as e:
        print(f"  ✗ RSS请求失败 ({url}): {e}")
        # 回退：让feedparser自己请求
        try:
            return feedparser.parse(url)
        except Exception as e2:
            print(f"  ✗ 回退也失败: {e2}")
            return None


def extract_images_from_html(html):
    """从HTML内容中提取图片URL"""
    images = []
    
    patterns = [
        r'href="(https://i\.redd\.it/[^"]+)"',
        r'src="(https://preview\.redd\.it/[^"]+)"',
        r'href="(https://i\.imgur\.com/[^"]+)"',
        r'src="(https://i\.imgur\.com/[^"]+)"',
        r'<img[^>]+src="([^"]+\.(?:jpg|jpeg|png|webp)(?:\?[^"]*)?)"',
    ]
    
    for pattern in patterns:
        matches = re.findall(pattern, html, re.IGNORECASE)
        for url in matches:
            clean = url.split("?")[0] if "redd.it" in url else url
            if any(skip in clean.lower() for skip in ['icon', 'logo', 'button', 'avatar', 'emoji']):
                continue
            if clean not in images:
                images.append(clean)
    
    return images


def fetch_reddit_posts(feed_config):
    """通过RSS获取Reddit帖子"""
    posts = []
    feed = fetch_rss(feed_config["url"])
    
    if not feed or not feed.entries:
        return posts
    
    subreddit = feed_config["name"]
    
    for entry in feed.entries:
        title = entry.get("title", "")
        if any(skip in title.lower() for skip in ['megathread', 'announcement', 'rules', 'modpost']):
            continue
        
        content = ""
        if entry.get("content"):
            content = entry["content"][0].get("value", "")
        elif entry.get("summary"):
            content = entry["summary"]
        
        images = extract_images_from_html(content)
        
        if not images:
            continue
        
        post_id = entry.get("id", entry.get("link", ""))
        
        posts.append({
            "id": post_id,
            "title": title,
            "url": entry.get("link", ""),
            "permalink": entry.get("link", ""),
            "subreddit": subreddit,
            "images": images[:5],
            "source_type": "reddit",
        })
    
    return posts


def fetch_blog_posts(feed_config):
    """获取博客RSS内容"""
    posts = []
    feed = fetch_rss(feed_config["url"])
    
    if not feed or not feed.entries:
        return posts
    
    for entry in feed.entries[:15]:
        content = ""
        if entry.get("content"):
            content = entry["content"][0].get("value", "")
        elif entry.get("summary"):
            content = entry["summary"]
        
        images = extract_images_from_html(content)
        
        if not images:
            continue
        
        posts.append({
            "id": entry.get("id", entry.get("link", "")),
            "title": entry.get("title", ""),
            "url": entry.get("link", ""),
            "permalink": entry.get("link", ""),
            "subreddit": feed_config["name"],
            "images": images[:3],
            "source_type": "blog",
        })
    
    return posts


# ============================================================
# Telegram 发送
# ============================================================

def telegram_request(method, data):
    """统一的Telegram API请求"""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/{method}"
    payload = json.dumps(data).encode("utf-8")
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            result = json.loads(response.read().decode())
            if not result.get("ok"):
                print(f"  ✗ Telegram API错误: {result.get('description', 'unknown')}")
            return result
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode()
        except:
            pass
        print(f"  ✗ Telegram {method} 失败: HTTP {e.code} - {body}")
        return None
    except Exception as e:
        print(f"  ✗ Telegram {method} 异常: {e}")
        return None


def send_message(text):
    return telegram_request("sendMessage", {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    })


def send_photo(photo_url, caption=""):
    result = telegram_request("sendPhoto", {
        "chat_id": TELEGRAM_CHAT_ID,
        "photo": photo_url,
        "caption": caption[:1024],
        "parse_mode": "HTML",
    })
    if not result or not result.get("ok"):
        return send_message(caption)
    return result


def send_media_group(images, caption=""):
    if not images:
        return None
    if len(images) == 1:
        return send_photo(images[0], caption)
    
    media = []
    for i, img_url in enumerate(images[:10]):
        item = {"type": "photo", "media": img_url}
        if i == 0:
            item["caption"] = caption[:1024]
            item["parse_mode"] = "HTML"
        media.append(item)
    
    result = telegram_request("sendMediaGroup", {
        "chat_id": TELEGRAM_CHAT_ID,
        "media": media,
    })
    if not result or not result.get("ok"):
        return send_photo(images[0], caption)
    return result


# ============================================================
# 主流程
# ============================================================

def format_caption(post):
    source = post["subreddit"]
    title = post["title"]
    link = post["permalink"]
    img_count = len(post.get("images", []))
    
    return (
        f"🏠 <b>{title}</b>\n"
        f"\n"
        f"📍 {source} | 🖼 {img_count}张图\n"
        f"\n"
        f"🔗 <a href=\"{link}\">查看原帖</a>"
    )


def main():
    print(f"\n{'='*50}")
    print(f"🏠 小红书家装素材抓取 - {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*50}\n")
    
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("❌ 错误：请设置 TELEGRAM_BOT_TOKEN 和 TELEGRAM_CHAT_ID 环境变量")
        return
    
    # 调试信息
    token_preview = TELEGRAM_BOT_TOKEN[:10] if len(TELEGRAM_BOT_TOKEN) > 10 else "TOO_SHORT"
    print(f"🔑 Bot Token: {token_preview}...（前10位）")
    print(f"💬 Chat ID: {TELEGRAM_CHAT_ID}")
    print(f"🔑 Token长度: {len(TELEGRAM_BOT_TOKEN)} 字符\n")
    
    # 测试Telegram连接
    print("📡 测试Telegram连接...")
    test = send_message("🔧 连接测试成功！脚本开始运行...")
    if not test or not test.get("ok"):
        print("❌ Telegram连接失败！请检查：")
        print("   1. TELEGRAM_BOT_TOKEN 是否正确（无多余空格/换行）")
        print("   2. TELEGRAM_CHAT_ID 是否正确")
        print("   3. 你是否已经在Telegram里给bot发过消息（点了Start）")
        return
    print("  ✓ Telegram连接正常\n")
    
    # 加载历史
    history = load_history()
    print(f"📋 历史记录：已推送 {len(history)} 条\n")
    
    # 收集帖子
    all_posts = []
    
    for feed in RSS_FEEDS:
        print(f"📡 正在获取 {feed['name']}...")
        
        if feed["type"] == "reddit":
            posts = fetch_reddit_posts(feed)
        else:
            posts = fetch_blog_posts(feed)
        
        print(f"  ✓ 获取 {len(posts)} 条（含图片）")
        all_posts.extend(posts)
        time.sleep(2)
    
    # 去重
    new_posts = [p for p in all_posts if p["id"] not in history]
    print(f"\n📊 汇总：共 {len(all_posts)} 条，新内容 {len(new_posts)} 条\n")
    
    if not new_posts:
        print("✅ 没有新内容，结束")
        send_message("📭 今日暂无新的家装内容")
        return
    
    to_send = new_posts[:MAX_POSTS_PER_RUN]
    
    # 汇总消息
    source_counts = {}
    for p in to_send:
        src = p["subreddit"]
        source_counts[src] = source_counts.get(src, 0) + 1
    
    summary = f"🏠 <b>今日家装素材 - {len(to_send)}条新内容</b>\n\n"
    for src, count in source_counts.items():
        summary += f"  • {src}: {count}条\n"
    
    send_message(summary)
    time.sleep(1)
    
    # 逐条推送
    sent_count = 0
    for post in to_send:
        caption = format_caption(post)
        images = post.get("images", [])
        
        print(f"📤 推送: {post['title'][:50]}...")
        
        result = send_media_group(images, caption) if images else send_message(caption)
        
        if result:
            # sendMediaGroup成功时返回的result可能是ok:true或者是list
            success = result.get("ok", False) if isinstance(result, dict) else True
            if success or isinstance(result, dict):
                history[post["id"]] = {
                    "title": post["title"],
                    "date": datetime.now(timezone.utc).isoformat(),
                }
                sent_count += 1
        
        time.sleep(2)
    
    save_history(history)
    print(f"\n✅ 完成！推送了 {sent_count} 条内容到 Telegram")


if __name__ == "__main__":
    main()
