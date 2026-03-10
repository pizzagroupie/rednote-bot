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

# Telegram配置（从环境变量读取，安全起见不硬编码）
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

# RSS源配置：(名称, URL, 最低分数门槛)
# Reddit的RSS会在title里带 [score] 或者我们从content里提取
RSS_FEEDS = [
    {
        "name": "RoomPorn",
        "url": "https://www.reddit.com/r/RoomPorn/.rss",
        "min_score": 500,
        "type": "reddit"
    },
    {
        "name": "CozyPlaces",
        "url": "https://www.reddit.com/r/CozyPlaces/.rss",
        "min_score": 500,
        "type": "reddit"
    },
    {
        "name": "InteriorDesign",
        "url": "https://www.reddit.com/r/InteriorDesign/.rss",
        "min_score": 100,
        "type": "reddit"
    },
    {
        "name": "AmateurRoomPorn",
        "url": "https://www.reddit.com/r/AmateurRoomPorn/.rss",
        "min_score": 300,
        "type": "reddit"
    },
    {
        "name": "Dezeen Interiors",
        "url": "https://www.dezeen.com/interiors/feed/",
        "min_score": 0,
        "type": "blog"
    },
    {
        "name": "Apartment Therapy",
        "url": "https://www.apartmenttherapy.com/main.rss",
        "min_score": 0,
        "type": "blog"
    },
]

# 历史记录文件，避免重复推送
HISTORY_FILE = Path(__file__).parent.parent / "data" / "sent_history.json"

# 每次运行最多推送多少条（避免刷屏）
MAX_POSTS_PER_RUN = 15

# ============================================================
# 工具函数
# ============================================================

def load_history():
    """加载已推送的帖子ID"""
    if HISTORY_FILE.exists():
        with open(HISTORY_FILE, "r") as f:
            data = json.load(f)
            # 只保留最近7天的记录，防止文件无限增长
            cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
            return {k: v for k, v in data.items() if v.get("date", "") > cutoff}
    return {}


def save_history(history):
    """保存推送历史"""
    HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(HISTORY_FILE, "w") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


def extract_reddit_images(entry):
    """从Reddit RSS条目中提取图片URL"""
    images = []
    content = entry.get("content", [{}])[0].get("value", "")
    if not content:
        content = entry.get("summary", "")
    
    # 匹配图片URL
    img_patterns = [
        r'href="(https://i\.redd\.it/[^"]+)"',
        r'src="(https://preview\.redd\.it/[^"]+)"',
        r'href="(https://i\.imgur\.com/[^"]+)"',
        r'src="(https://i\.imgur\.com/[^"]+)"',
    ]
    
    for pattern in img_patterns:
        matches = re.findall(pattern, content)
        for url in matches:
            # 清理preview.redd.it的URL参数，获取原图
            clean_url = url.split("?")[0]
            if clean_url not in images:
                images.append(clean_url)
    
    return images


def extract_blog_images(entry):
    """从博客RSS条目中提取图片URL"""
    images = []
    content = entry.get("content", [{}])[0].get("value", "")
    if not content:
        content = entry.get("summary", "")
    
    img_pattern = r'<img[^>]+src="([^"]+)"'
    matches = re.findall(img_pattern, content)
    
    for url in matches:
        if any(ext in url.lower() for ext in ['.jpg', '.jpeg', '.png', '.webp']):
            if url not in images:
                images.append(url)
    
    return images[:3]  # 博客图片取前3张就够


def extract_reddit_score(entry):
    """尝试从Reddit RSS条目中提取分数"""
    # Reddit RSS的content里有时会包含score信息
    # 但更可靠的是通过Reddit JSON API
    # 这里我们用一个简单的方法：从RSS无法直接拿到准确分数
    # 所以对Reddit源，我们改用 .json 后缀获取
    return 0


def fetch_reddit_json(subreddit, limit=25):
    """用Reddit JSON API获取帖子（比RSS更好，能拿到分数）"""
    url = f"https://www.reddit.com/r/{subreddit}/hot.json?limit={limit}"
    headers = {
        "User-Agent": "XiaohongshuBot/1.0 (content curation)"
    }
    
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            data = json.loads(response.read().decode())
            posts = []
            for child in data.get("data", {}).get("children", []):
                post = child.get("data", {})
                # 只要图片帖子
                if post.get("post_hint") == "image" or post.get("url", "").endswith(('.jpg', '.jpeg', '.png')):
                    posts.append({
                        "id": post.get("id", ""),
                        "title": post.get("title", ""),
                        "score": post.get("score", 0),
                        "url": post.get("url", ""),
                        "permalink": f"https://www.reddit.com{post.get('permalink', '')}",
                        "subreddit": subreddit,
                        "images": [post.get("url", "")],
                        "source_type": "reddit",
                    })
                # Gallery帖子（多图）
                elif post.get("is_gallery"):
                    gallery_images = []
                    media_metadata = post.get("media_metadata", {})
                    for media_id, media_info in media_metadata.items():
                        if media_info.get("status") == "valid":
                            # 取最大尺寸的图
                            source = media_info.get("s", {})
                            img_url = source.get("u", "").replace("&amp;", "&")
                            if img_url:
                                gallery_images.append(img_url)
                    if gallery_images:
                        posts.append({
                            "id": post.get("id", ""),
                            "title": post.get("title", ""),
                            "score": post.get("score", 0),
                            "url": post.get("url", ""),
                            "permalink": f"https://www.reddit.com{post.get('permalink', '')}",
                            "subreddit": subreddit,
                            "images": gallery_images[:5],  # 最多5张
                            "source_type": "reddit",
                        })
            return posts
    except Exception as e:
        print(f"  ✗ 获取 r/{subreddit} 失败: {e}")
        return []


def fetch_blog_rss(feed_config):
    """获取博客RSS内容"""
    posts = []
    try:
        feed = feedparser.parse(feed_config["url"])
        for entry in feed.entries[:15]:
            images = extract_blog_images(entry)
            if images:  # 只要有图的
                posts.append({
                    "id": entry.get("id", entry.get("link", "")),
                    "title": entry.get("title", ""),
                    "score": 999,  # 博客不按分数过滤
                    "url": entry.get("link", ""),
                    "permalink": entry.get("link", ""),
                    "subreddit": feed_config["name"],
                    "images": images,
                    "source_type": "blog",
                })
    except Exception as e:
        print(f"  ✗ 获取 {feed_config['name']} 失败: {e}")
    return posts


def send_telegram_message(text, parse_mode="HTML"):
    """发送Telegram文本消息"""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    data = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": False,
    }
    
    payload = json.dumps(data).encode("utf-8")
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            return json.loads(response.read().decode())
    except Exception as e:
        print(f"  ✗ Telegram发送失败: {e}")
        return None


def send_telegram_photo(photo_url, caption=""):
    """发送Telegram图片消息"""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
    data = {
        "chat_id": TELEGRAM_CHAT_ID,
        "photo": photo_url,
        "caption": caption[:1024],  # Telegram caption限制1024字符
        "parse_mode": "HTML",
    }
    
    payload = json.dumps(data).encode("utf-8")
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            return json.loads(response.read().decode())
    except Exception as e:
        print(f"  ✗ Telegram图片发送失败: {e}")
        # 图片发送失败时退回到文本+链接
        return send_telegram_message(caption)


def send_telegram_media_group(images, caption=""):
    """发送Telegram多图消息（相册模式）"""
    if not images:
        return None
    
    if len(images) == 1:
        return send_telegram_photo(images[0], caption)
    
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMediaGroup"
    media = []
    for i, img_url in enumerate(images[:10]):  # Telegram最多10张
        item = {"type": "photo", "media": img_url}
        if i == 0:
            item["caption"] = caption[:1024]
            item["parse_mode"] = "HTML"
        media.append(item)
    
    data = {
        "chat_id": TELEGRAM_CHAT_ID,
        "media": media,
    }
    
    payload = json.dumps(data).encode("utf-8")
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            return json.loads(response.read().decode())
    except Exception as e:
        print(f"  ✗ Telegram相册发送失败: {e}")
        # 退回到单图模式
        return send_telegram_photo(images[0], caption)


# ============================================================
# 主流程
# ============================================================

def format_post_caption(post):
    """格式化推送消息"""
    source = post["subreddit"]
    score = post["score"]
    title = post["title"]
    link = post["permalink"]
    img_count = len(post.get("images", []))
    
    if post["source_type"] == "reddit":
        caption = (
            f"🏠 <b>{title}</b>\n"
            f"\n"
            f"📍 r/{source} | ⬆️ {score} upvotes | 🖼 {img_count}张图\n"
            f"\n"
            f"🔗 <a href=\"{link}\">查看原帖</a>"
        )
    else:
        caption = (
            f"🏠 <b>{title}</b>\n"
            f"\n"
            f"📍 {source} | 🖼 {img_count}张图\n"
            f"\n"
            f"🔗 <a href=\"{link}\">查看原文</a>"
        )
    
    return caption


def main():
    print(f"\n{'='*50}")
    print(f"🏠 小红书家装素材抓取 - {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*50}\n")
    
    # 检查配置
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("❌ 错误：请设置 TELEGRAM_BOT_TOKEN 和 TELEGRAM_CHAT_ID 环境变量")
        print("   详见 README.md 的配置指南")
        return
    
    # 加载历史记录
    history = load_history()
    print(f"📋 历史记录：已推送 {len(history)} 条\n")
    
    # 收集所有帖子
    all_posts = []
    
    for feed in RSS_FEEDS:
        print(f"📡 正在获取 {feed['name']}...")
        
        if feed["type"] == "reddit":
            subreddit = feed["url"].split("/r/")[1].split("/")[0]
            posts = fetch_reddit_json(subreddit)
            # 按分数过滤
            posts = [p for p in posts if p["score"] >= feed["min_score"]]
            print(f"  ✓ 获取 {len(posts)} 条（分数 >= {feed['min_score']}）")
        else:
            posts = fetch_blog_rss(feed)
            print(f"  ✓ 获取 {len(posts)} 条")
        
        all_posts.extend(posts)
        time.sleep(1)  # 礼貌性延迟，避免被封
    
    # 过滤已推送的
    new_posts = [p for p in all_posts if p["id"] not in history]
    print(f"\n📊 汇总：共 {len(all_posts)} 条，新内容 {len(new_posts)} 条\n")
    
    if not new_posts:
        print("✅ 没有新内容，结束")
        send_telegram_message("📭 今日暂无新的高质量家装内容")
        return
    
    # 按分数排序，推送最好的
    new_posts.sort(key=lambda x: x["score"], reverse=True)
    to_send = new_posts[:MAX_POSTS_PER_RUN]
    
    # 发送汇总消息
    summary = f"🏠 <b>今日家装素材 - {len(to_send)}条新内容</b>\n\n"
    source_counts = {}
    for p in to_send:
        src = p["subreddit"]
        source_counts[src] = source_counts.get(src, 0) + 1
    for src, count in source_counts.items():
        summary += f"  • {src}: {count}条\n"
    
    send_telegram_message(summary)
    time.sleep(1)
    
    # 逐条推送
    sent_count = 0
    for post in to_send:
        caption = format_post_caption(post)
        images = post.get("images", [])
        
        print(f"📤 推送: {post['title'][:50]}...")
        
        if images:
            result = send_telegram_media_group(images, caption)
        else:
            result = send_telegram_message(caption)
        
        if result:
            history[post["id"]] = {
                "title": post["title"],
                "date": datetime.now(timezone.utc).isoformat(),
            }
            sent_count += 1
        
        time.sleep(2)  # Telegram API限速，每条间隔2秒
    
    # 保存历史
    save_history(history)
    
    print(f"\n✅ 完成！推送了 {sent_count} 条内容到 Telegram")


if __name__ == "__main__":
    main()
