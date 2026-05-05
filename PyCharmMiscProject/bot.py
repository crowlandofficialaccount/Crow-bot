import discord
from discord.ext import commands, tasks
from discord import app_commands
import random
import json
import aiohttp
import io
import asyncio
from datetime import datetime, time, timezone
from dotenv import load_dotenv
import os

# ---- Credentials & Setup ----
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = int(os.getenv("GUILD_ID"))

CONFIG_FILE = "config.json"

def load_data():
    try:
        with open(CONFIG_FILE, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"crow_channel": None, "birthday_channel": None, "crow_images": [], "birthdays": {}}

def save_data(data_to_save):
    with open(CONFIG_FILE, "w") as f:
        json.dump(data_to_save, f, indent=4)

data = load_data()
crow_image_cache = []

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='/', intents=intents)

CROW_SUBREDDITS = ["crows", "corvids", "corvidgifs", "crowbro"]

# ---- Lenient AI filter — only rejects memes/screenshots/text ----
async def refresh_crow_cache(session):
    """Fetches subreddits one at a time with a delay to avoid Reddit rate limiting."""
    global crow_image_cache
    headers = {"User-Agent": "CrowBot/1.0 (by /u/crowbotdev)"}
    candidates = []

    for subreddit in CROW_SUBREDDITS:
        for sort in ["hot", "top"]:
            url = f"https://www.reddit.com/r/{subreddit}/{sort}.json?limit=100&t=all"
            try:
                async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    print(f"[DEBUG] r/{subreddit}/{sort} status: {resp.status}")
                    if resp.status == 200:
                        result = await resp.json()
                        posts = result["data"]["children"]
                        images = [
                            p["data"]["url"] for p in posts
                            if p["data"].get("url", "").endswith((".jpg", ".jpeg", ".png"))
                            and not p["data"].get("over_18", False)
                        ]
                        candidates.extend(images)
                        print(f"[DEBUG] r/{subreddit}/{sort}: {len(images)} images found")
                    elif resp.status == 429:
                        print(f"[DEBUG] Rate limited on r/{subreddit}/{sort}, waiting 10s...")
                        await asyncio.sleep(10)
            except Exception as e:
                print(f"[DEBUG] Failed r/{subreddit}/{sort}: {e}")
            await asyncio.sleep(2)  # 2 second delay between each request

    candidates = list(set(candidates))
    random.shuffle(candidates)
    crow_image_cache = candidates[:150]
    print(f"[DEBUG] ✅ Cache ready: {len(crow_image_cache)} images")


# ---- Image Helper ----
async def send_crow(target):
    global crow_image_cache

    async with aiohttp.ClientSession() as session:
        if not crow_image_cache:
            await refresh_crow_cache(session)

        image_pool = data.get("crow_images") or crow_image_cache

        if not image_pool:
            msg = "🐦‍⬛ The crows are hiding right now, try again later!"
            if isinstance(target, discord.Interaction):
                await target.followup.send(msg)
            else:
                await target.send(msg)
            return

        urls = random.sample(image_pool, min(len(image_pool), 10))
        for image_url in urls:
            try:
                async with session.get(image_url, headers={"User-Agent": "CrowBot/1.0"}, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    if resp.status == 200:
                        image_bytes = await resp.read()
                        ext = image_url.split(".")[-1].lower()
                        with io.BytesIO(image_bytes) as f:
                            discord_file = discord.File(fp=f, filename=f"crow.{ext}")
                            if isinstance(target, discord.Interaction):
                                await target.followup.send(file=discord_file)
                            else:
                                await target.send(file=discord_file)
                        return
            except Exception as e:
                print(f"[DEBUG] Send failed {image_url}: {e}")
                continue

    msg = "🐦‍⬛ The crows are hiding right now, try again later!"
    if isinstance(target, discord.Interaction):
        await target.followup.send(msg)
    else:
        await target.send(msg)


# ---- Events ----
@bot.event
async def on_ready():
    guild = discord.Object(id=GUILD_ID)
    bot.tree.copy_global_to(guild=guild)
    await bot.tree.sync(guild=guild)
    print(f"✅ Commands synced to guild {GUILD_ID}")

    async with aiohttp.ClientSession() as session:
        await refresh_crow_cache(session)

    if not auto_crow.is_running():
        auto_crow.start()
    if not birthday_check.is_running():
        birthday_check.start()
    if not refresh_cache_task.is_running():
        refresh_cache_task.start()

    print(f"Logged in as {bot.user}")


# ---- Commands ----
@bot.tree.command(name="crow", description="Send a random crow image")
async def crow(interaction: discord.Interaction):
    await interaction.response.defer()
    await send_crow(interaction)

@bot.tree.command(name="setbirthday", description="Set your birthday (MM-DD)")
async def set_birthday(interaction: discord.Interaction, date: str):
    if len(date) != 5 or date[2] != "-":
        return await interaction.response.send_message("❌ Use **MM-DD** format.", ephemeral=True)
    data["birthdays"][str(interaction.user.id)] = date
    save_data(data)
    await interaction.response.send_message("🎂 Birthday saved!")

@bot.tree.command(name="setcrowchannel", description="Set channel for auto crow posts")
@app_commands.checks.has_permissions(manage_channels=True)
async def set_crow_channel(interaction: discord.Interaction, channel: discord.TextChannel):
    data["crow_channel"] = channel.id
    save_data(data)
    await interaction.response.send_message(f"✅ Crow channel set to {channel.mention}")

@bot.tree.command(name="setbirthdaychannel", description="Set channel for birthday announcements")
@app_commands.checks.has_permissions(manage_channels=True)
async def set_birthday_channel(interaction: discord.Interaction, channel: discord.TextChannel):
    data["birthday_channel"] = channel.id
    save_data(data)
    await interaction.response.send_message(f"✅ Birthday channel set to {channel.mention}")


# ---- Tasks ----
@tasks.loop(hours=2)
async def auto_crow():
    if data.get("crow_channel"):
        channel = bot.get_channel(data["crow_channel"])
        if channel:
            await send_crow(channel)

@tasks.loop(hours=12)
async def refresh_cache_task():
    async with aiohttp.ClientSession() as session:
        await refresh_crow_cache(session)

@tasks.loop(time=time(hour=0, minute=0, tzinfo=timezone.utc))
async def birthday_check():
    if not data.get("birthday_channel"):
        return
    channel = bot.get_channel(data["birthday_channel"])
    if not channel:
        return
    today = datetime.now(tz=timezone.utc).strftime("%m-%d")
    birthday_people = [uid for uid, bday in data["birthdays"].items() if bday == today]
    if birthday_people:
        mentions = ", ".join([f"<@{uid}>" for uid in birthday_people])
        await channel.send(f"🎂 Happy Birthday {mentions}! 🎉")

@birthday_check.before_loop
@auto_crow.before_loop
@refresh_cache_task.before_loop
async def before_tasks():
    await bot.wait_until_ready()

bot.run(TOKEN)