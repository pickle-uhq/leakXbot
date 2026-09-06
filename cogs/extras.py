import random
import asyncio
from datetime import datetime, timedelta, timezone

import discord
from discord.ext import commands, tasks

import json_store
from cogs.moderation import parse_duration


class Extras(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.check_giveaways.start()

    def cog_unload(self):
        self.check_giveaways.cancel()

    # ---------- custom commands ----------

    @commands.command(name="addcmd")
    @commands.has_permissions(manage_guild=True)
    async def addcmd(self, ctx, name: str, *, response: str):
        name = name.lower()
        if name in self.bot.all_commands:
            await ctx.send("That name clashes with a built-in command.")
            return
        config = json_store.get_guild_config(ctx.guild.id)
        config.setdefault("custom_commands", {})
        config["custom_commands"][name] = response
        json_store.set_guild_config(ctx.guild.id, config)
        await ctx.send(f"Custom command `!{name}` added.")

    @commands.command(name="delcmd")
    @commands.has_permissions(manage_guild=True)
    async def delcmd(self, ctx, name: str):
        config = json_store.get_guild_config(ctx.guild.id)
        config.get("custom_commands", {}).pop(name.lower(), None)
        json_store.set_guild_config(ctx.guild.id, config)
        await ctx.send(f"Custom command `!{name}` removed.")

    @commands.command(name="cmds")
    async def cmds(self, ctx):
        config = json_store.get_guild_config(ctx.guild.id)
        names = list(config.get("custom_commands", {}).keys())
        await ctx.send("Custom commands: " + (", ".join(f"!{n}" for n in names) if names else "none yet"))

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild or not message.content.startswith("!"):
            return
        word = message.content[1:].split(" ")[0].lower()
        if word in self.bot.all_commands:
            return  # let the real command run
        config = json_store.get_guild_config(message.guild.id)
        response = config.get("custom_commands", {}).get(word)
        if response:
            await message.channel.send(response)

    # ---------- giveaways ----------

    @commands.command(name="gstart")
    @commands.has_permissions(manage_guild=True)
    async def gstart(self, ctx, duration: str, winners: int, *, prize: str):
        seconds = parse_duration(duration)
        if not seconds:
            await ctx.send("Duration format: 10s / 10m / 1h / 1d")
            return
        end_time = datetime.now(timezone.utc) + timedelta(seconds=seconds)
        embed = discord.Embed(
            title="🎉 Giveaway",
            description=f"**Prize:** {prize}\n**Winners:** {winners}\nReact with 🎉 to enter!\nEnds: {discord.utils.format_dt(end_time, 'R')}",
        )
        msg = await ctx.send(embed=embed)
        await msg.add_reaction("🎉")

        config = json_store.get_guild_config(ctx.guild.id)
        config.setdefault("giveaways", {})
        config["giveaways"][str(msg.id)] = {
            "channel_id": ctx.channel.id,
            "end_time": end_time.isoformat(),
            "winners": winners,
            "prize": prize,
            "ended": False,
        }
        json_store.set_guild_config(ctx.guild.id, config)

    async def _pick_winners(self, guild: discord.Guild, giveaway_id: str, data: dict):
        channel = guild.get_channel(data["channel_id"])
        if not channel:
            return
        message = await channel.fetch_message(int(giveaway_id))
        reaction = discord.utils.get(message.reactions, emoji="🎉")
        users = [u async for u in reaction.users() if not u.bot] if reaction else []
        if not users:
            await channel.send("Giveaway ended — nobody entered.")
            return
        winners = random.sample(users, min(data["winners"], len(users)))
        mentions = ", ".join(w.mention for w in winners)
        await channel.send(f"🎉 Congrats {mentions}! You won **{data['prize']}**")

    @commands.command(name="gend")
    @commands.has_permissions(manage_guild=True)
    async def gend(self, ctx, message_id: int):
        config = json_store.get_guild_config(ctx.guild.id)
        data = config.get("giveaways", {}).get(str(message_id))
        if not data:
            await ctx.send("No giveaway with that message ID.")
            return
        await self._pick_winners(ctx.guild, str(message_id), data)
        data["ended"] = True
        json_store.set_guild_config(ctx.guild.id, config)

    @commands.command(name="greroll")
    @commands.has_permissions(manage_guild=True)
    async def greroll(self, ctx, message_id: int):
        config = json_store.get_guild_config(ctx.guild.id)
        data = config.get("giveaways", {}).get(str(message_id))
        if not data:
            await ctx.send("No giveaway with that message ID.")
            return
        await self._pick_winners(ctx.guild, str(message_id), data)

    @commands.command(name="glist")
    async def glist(self, ctx):
        config = json_store.get_guild_config(ctx.guild.id)
        active = [gid for gid, d in config.get("giveaways", {}).items() if not d["ended"]]
        await ctx.send("Active giveaways: " + (", ".join(active) if active else "none"))

    @tasks.loop(seconds=30)
    async def check_giveaways(self):
        data = json_store.load()
        for guild_id, config in data.items():
            guild = self.bot.get_guild(int(guild_id))
            if not guild:
                continue
            for gid, giveaway in config.get("giveaways", {}).items():
                if giveaway["ended"]:
                    continue
                end_time = datetime.fromisoformat(giveaway["end_time"])
                if datetime.now(timezone.utc) >= end_time:
                    await self._pick_winners(guild, gid, giveaway)
                    giveaway["ended"] = True
            json_store.set_guild_config(int(guild_id), config)

    @check_giveaways.before_loop
    async def before_check_giveaways(self):
        await self.bot.wait_until_ready()


async def setup(bot):
    await bot.add_cog(Extras(bot))

