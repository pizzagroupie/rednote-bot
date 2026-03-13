"""
小红书家装素材自动抓取 + Telegram推送
带风格关键词过滤、商业内容排除、质量筛选
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
# 配置区域 — 所有过滤规则都在这里改
# ============================================================

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

# ---- 内容源 ----
# Reddit用 /top/ 而不是默认的 /hot/，这样拿到的都是高赞内容
# t=day 表示过去24小时的top帖，t=week 表示过去一周
RSS_FEEDS = [
    # ---- Reddit ----
    {
        "name": "RoomPorn",
        "url": "https://www.reddit.com/r/RoomPorn/top/.rss?t=year&limit=25",
        "type": "reddit"
    },
    {
        "name": "CozyPlaces",
        "url": "https://www.reddit.com/r/CozyPlaces/top/.rss?t=year&limit=25",
        "type": "reddit"
    },
    {
        "name": "InteriorDesign",
        "url": "https://www.reddit.com/r/InteriorDesign/top/.rss?t=year&limit=25",
        "type": "reddit"
    },
    {
        "name": "AmateurRoomPorn",
        "url": "https://www.reddit.com/r/AmateurRoomPorn/top/.rss?t=year&limit=25",
        "type": "reddit"
    },
    # ---- 博客 ----
    {
        "name": "Apartment Therapy",
        "url": "https://www.apartmenttherapy.com/main.rss",
        "type": "blog"
    },
    {
        "name": "Yellowtrace",
        "url": "https://www.yellowtrace.com.au/feed/",
        "type": "blog"
    },
    {
        "name": "Remodelista",
        "url": "https://www.remodelista.com/rss",
        "type": "blog"
    },
    {
        "name": "The Nordroom",
        "url": "https://www.thenordroom.com/feed/",
        "type": "blog"
    },
    {
        "name": "My Scandinavian Home",
        "url": "https://www.myscandinavianhome.com/feeds/posts/default?alt=rss",
        "type": "blog"
    },
    {
        "name": "COCO LAPINE DESIGN",
        "url": "https://cocolapinedesign.com/feed/",
        "type": "blog"
    },
]

# Dezeen已删除 — 内容偏建筑/商业空间，不适合小红书家装方向

# ---- 风格关键词过滤（白名单模式，仅对Reddit生效）----

STYLE_KEYWORDS = [
    # 北欧/极简系
    "minimalist", "minimal", "scandinavian", "nordic", "muji", "zen", "clean lines",
    # 日式/侘寂系
    "japandi", "japanese", "wabi-sabi", "wabi sabi",
    # 温馨系
    "cozy", "cosy", "hygge", "warm", "cottage", "rustic", "farmhouse", "cabin",
    # 复古/中古系
    "mid-century", "mid century", "vintage", "retro", "art deco", "bohemian", "boho",
    # 奶油/法式系
    "french", "parisian", "cream", "neutral", "elegant",
    # 工业风
    "industrial", "loft", "exposed brick",
    # 自然系
    "earthy", "natural", "rattan", "linen", "marble",
    # 现代轻奢
    "modern luxury", "contemporary", "modern",
    # 小户型相关
    "small space", "studio apartment", "tiny", "renovation", "makeover", "before and after",
]

# ---- 排除关键词（黑名单）----
# 标题含有这些词的帖子会被直接过滤掉
EXCLUDE_KEYWORDS = [
    # 商业/广告内容
    "sponsored", "ad ", " ad", "[ad]", "affiliate",
    "giveaway", "discount", "coupon", "promo",
    "buy now", "shop now", "limited time", "sale",
    "use code", "link in bio",
    
    # 非家装内容
    "meme", "memes", "funny",
    "rate my", "roast my",
    "help me choose", "what color",
    "where to buy", "where can i find",
    "id on", "id request",
    
    # 版务/公告
    "megathread", "announcement", "rules",
    "modpost", "mod post", "meta",
    "survey", "census",
]

# ---- 商业空间排除（仅对博客源生效）----
# 博客源标题含这些词的跳过，聚焦住宅室内设计
COMMERCIAL_KEYWORDS = [
    "restaurant", "bar ", " bar", "hotel", "office",
    "store", "shop ", " shop", "museum", "gallery",
    "spa ", " spa", "café", "cafe", "showroom",
    "exhibition", "retail", "workspace", "co-working",
    "coworking", "pavilion", "installation",
    "church", "library", "school", "hospital",
    "airport", "station", "theater", "theatre",
    "cinema", "nightclub", "club ", " club",
    "brewery", "winery", "distillery",
]

# ---- 推送设置 ----
HISTORY_FILE = Path(__file__).parent.parent / "data" / "sent_history.json"
MAX_POSTS_PER_RUN = 20  # 每次最多推多少条

# ============================================================
# 过滤逻辑
# ============================================================

def matches_style_filter(title, content=""):
    """检查是否匹配风格关键词（仅对Reddit生效）"""
    text = (title + " " + content).lower()
    return any(kw.lower() in text for kw in STYLE_KEYWORDS)


def matches_exclude_filter(title):
    """检查是否命中排除关键词（所有源生效）"""
    title_lower = title.lower()
    return any(kw.lower() in title_lower for kw in EXCLUDE_KEYWORDS)


def matches_commercial_filter(title):
    """检查是否命中商业空间关键词（仅对博客源生效）"""
    title_lower = title.lower()
    return any(kw.lower() in title_lower for kw in COMMERCIAL_KEYWORDS)


def filter_posts(posts, source_type):
    """应用过滤规则。source_type: 'reddit' 或 'blog'"""
    original_count = len(posts)
    
    # 所有源：排除黑名单内容
    posts = [p for p in posts if not matches_exclude_filter(p["title"])]
    excluded = original_count - len(posts)
    
    # 仅Reddit：风格关键词白名单过滤
    style_filtered = 0
    if source_type == "reddit":
        before_style = len(posts)
        posts = [p for p in posts if matches_style_filter(p["title"])]
        style_filtered = before_style - len(posts)
    
    # 仅博客：排除商业空间
    commercial_filtered = 0
    if source_type == "blog":
        before_commercial = len(posts)
        posts = [p for p in posts if not matches_commercial_filter(p["title"])]
        commercial_filtered = before_commercial - len(posts)
    
    if excluded > 0:
        print(f"  ⊘ 排除 {excluded} 条（广告/无关内容）")
    if style_filtered > 0:
        print(f"  ⊘ 过滤 {style_filtered} 条（不匹配风格关键词）")
    if commercial_filtered > 0:
        print(f"  ⊘ 排除 {commercial_filtered} 条（商业空间）")
    
    return posts


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
    """用浏览器UA获取RSS"""
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
        print(f"  ✗ RSS请求失败: {e}")
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
            if any(skip in clean.lower() for skip in ['icon', 'logo', 'button', 'avatar', 'emoji', 'flair']):
                continue
            if clean not in images:
                images.append(clean)
    
    return images


def fetch_reddit_posts(feed_config):
    """通过RSS获取Reddit帖子（使用/top/排序保证质量）"""
    posts = []
    feed = fetch_rss(feed_config["url"])
    
    if not feed or not feed.entries:
        return posts
    
    subreddit = feed_config["name"]
    
    for entry in feed.entries:
        title = entry.get("title", "")
        
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
    
    # 打印当前过滤配置
    print("⚙️  当前过滤配置：")
    print(f"   风格关键词过滤: ✓ 开启（仅Reddit，{len(STYLE_KEYWORDS)}个关键词）")
    print(f"   广告排除: ✓ 开启（{len(EXCLUDE_KEYWORDS)}个排除词）")
    print(f"   商业空间排除: ✓ 开启（仅博客，{len(COMMERCIAL_KEYWORDS)}个排除词）")
    print(f"   Reddit排序: /top/（按热度）")
    print(f"   内容源: {len(RSS_FEEDS)} 个（{sum(1 for f in RSS_FEEDS if f['type']=='reddit')} Reddit + {sum(1 for f in RSS_FEEDS if f['type']=='blog')} 博客）")
    print(f"   每次最多推送: {MAX_POSTS_PER_RUN} 条\n")
    
    # 测试Telegram
    print("📡 测试Telegram连接...")
    test = send_message("🔧 连接测试成功！开始抓取...")
    if not test or not test.get("ok"):
        print("❌ Telegram连接失败")
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
        
        print(f"  ✓ 抓取 {len(posts)} 条（含图片）")
        
        # 应用过滤规则
        posts = filter_posts(posts, feed["type"])
        print(f"  ✓ 过滤后 {len(posts)} 条")
        
        all_posts.extend(posts)
        time.sleep(2)
    
    # 去重
    new_posts = [p for p in all_posts if p["id"] not in history]
    
    print(f"\n{'='*50}")
    print(f"📊 汇总")
    print(f"   抓取总数: {len(all_posts)} 条")
    print(f"   新内容: {len(new_posts)} 条")
    print(f"   将推送: {min(len(new_posts), MAX_POSTS_PER_RUN)} 条")
    print(f"{'='*50}\n")
    
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
    summary += f"\n⚙️ 过滤: 广告排除 + Reddit风格关键词 + 博客商业空间排除"
    
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
