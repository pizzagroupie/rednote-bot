# 🏠 小红书家装素材自动抓取 + Telegram推送

每天自动从Reddit、Dezeen、Apartment Therapy等平台抓取高质量家装图片，通过Telegram推送到你手机。

## 工作原理

```
GitHub Actions 定时触发（每天早8点+晚8点）
        ↓
从 Reddit / Dezeen / Apartment Therapy 抓取新内容
        ↓
按分数过滤（Reddit帖子只推高赞的）
        ↓
去重（不会重复推送）
        ↓
通过 Telegram Bot 推送到你的手机
```

## 配置步骤（只需做一次，大约15分钟）

### 第一步：创建 Telegram Bot

1. 打开 Telegram，搜索 `@BotFather`
2. 发送 `/newbot`
3. 给bot起个名字，比如 `我的家装灵感`
4. 给bot起个用户名，比如 `my_homedecor_bot`（必须以bot结尾）
5. BotFather 会回复你一个 **token**，类似 `7123456789:AAHxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx`
6. **保存这个token**，后面要用

### 第二步：获取你的 Chat ID

1. 在Telegram里搜索你刚创建的bot，点进去，发送任意消息（比如 `hello`）
2. 在浏览器打开这个地址（把TOKEN换成你的）：
   ```
   https://api.telegram.org/botTOKEN/getUpdates
   ```
3. 在返回的JSON里找到 `"chat":{"id": 123456789}`，这个数字就是你的 **Chat ID**
4. **保存这个Chat ID**

### 第三步：Fork这个仓库

1. 点击GitHub页面右上角的 **Fork** 按钮
2. Fork到你自己的账号下

### 第四步：配置 Secrets

1. 进入你Fork后的仓库页面
2. 点击 **Settings** → 左侧栏 **Secrets and variables** → **Actions**
3. 点击 **New repository secret**，添加两个：

| Name | Value |
|------|-------|
| `TELEGRAM_BOT_TOKEN` | 第一步拿到的bot token |
| `TELEGRAM_CHAT_ID` | 第二步拿到的chat id |

### 第五步：启用 GitHub Actions

1. 进入仓库的 **Actions** 标签页
2. 如果有提示，点击 **I understand my workflows, go ahead and enable them**
3. 搞定！脚本会在每天北京时间早8点和晚8点自动运行

### 测试运行

不想等到定时触发？可以手动跑一次：

1. 进入 **Actions** 标签页
2. 左侧选择 **小红书家装素材抓取**
3. 点击 **Run workflow** → **Run workflow**
4. 等1-2分钟，检查你的Telegram有没有收到消息

## 自定义配置

### 调整内容源

编辑 `scripts/fetch_and_push.py` 里的 `RSS_FEEDS` 列表：

```python
RSS_FEEDS = [
    {
        "name": "RoomPorn",
        "url": "https://www.reddit.com/r/RoomPorn/.rss",
        "min_score": 500,    # 只推500赞以上的
        "type": "reddit"
    },
    # 添加新的subreddit：
    {
        "name": "Mid Century Modern",
        "url": "https://www.reddit.com/r/Mid_Century/.rss",
        "min_score": 200,
        "type": "reddit"
    },
]
```

### 调整推送时间

编辑 `.github/workflows/fetch.yml` 里的 cron 表达式：

```yaml
schedule:
  - cron: '0 0 * * *'    # UTC 0:00 = 北京时间 8:00
  - cron: '0 12 * * *'   # UTC 12:00 = 北京时间 20:00
  - cron: '0 6 * * *'    # 加一个：UTC 6:00 = 北京时间 14:00
```

### 调整每次推送数量

修改 `scripts/fetch_and_push.py` 里的 `MAX_POSTS_PER_RUN`：

```python
MAX_POSTS_PER_RUN = 15   # 每次最多推15条
```

## 推送效果示例

每条推送包含：
- 🏠 帖子标题
- 📍 来源和分数
- 🖼 图片（自动以相册形式展示）
- 🔗 原帖链接（方便你下载高清原图）

## 成本

**完全免费：**
- GitHub Actions 对公开仓库免费
- Telegram Bot API 免费
- Reddit JSON API 免费

## 常见问题

**Q: GitHub Actions没有自动运行？**
A: 如果仓库超过60天没有活动，GitHub会暂停Actions。进去手动Run一次就会恢复。

**Q: Reddit返回429错误？**
A: 请求太频繁了。脚本已经内置了延迟，正常情况不会触发。如果还是429，把cron改成一天一次。

**Q: 想加Instagram/Pinterest？**
A: 这两个平台没有公开API/RSS。需要部署RSSHub（https://docs.rsshub.app），把生成的RSS地址加到RSS_FEEDS里就行。

**Q: 图片发送失败？**
A: 有些图片URL可能过期或被防盗链。脚本会自动退回到发送文本+链接的模式。
