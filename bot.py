import os
import asyncio
import discord
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

intents = discord.Intents.default()
intents.members = True
intents.message_content = True
intents.presences = True  # needed for status role detection

bot = commands.Bot(command_prefix="!", intents=intents)


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} ({bot.user.id})")
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} slash command(s)")
    except Exception as e:
        print(f"Slash sync failed: {e}")


async def main():
    if not TOKEN:
        print("ERROR: DISCORD_TOKEN not set. Add it as an environment variable / Railway secret.")
        return
    async with bot:
        await bot.load_extension("cogs.tickets")
        await bot.load_extension("cogs.roles")
        await bot.load_extension("cogs.moderation")
        await bot.load_extension("cogs.extras")
        await bot.load_extension("cogs.temp_roles")
        await bot.start(TOKEN)


if __name__ == "__main__":
    asyncio.run(main())
