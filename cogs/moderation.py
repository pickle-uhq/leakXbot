import re
from datetime import timedelta
import discord
from discord.ext import commands

import json_store


def parse_duration(text: str):
    match = re.match(r"^(\d+)([smhd])$", text.lower())
    if not match:
        return None
    amount, unit = int(match.group(1)), match.group(2)
    seconds = {"s": 1, "m": 60, "h": 3600, "d": 86400}[unit]
    return amount * seconds


async def log_action(guild: discord.Guild, text: str):
    config = json_store.get_guild_config(guild.id)
    channel_id = config.get("log_channel_id")
    if channel_id:
        channel = guild.get_channel(channel_id)
        if channel:
            await channel.send(text)


class Moderation(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="kick")
    @commands.has_permissions(kick_members=True)
    async def kick(self, ctx, member: discord.Member, *, reason: str = "No reason given"):
        await member.kick(reason=reason)
        await ctx.send(f"Kicked {member.mention} — {reason}")
        await log_action(ctx.guild, f"{ctx.author} kicked {member} — {reason}")

    @commands.command(name="ban")
    @commands.has_permissions(ban_members=True)
    async def ban(self, ctx, member: discord.Member, *, reason: str = "No reason given"):
        await member.ban(reason=reason)
        await ctx.send(f"Banned {member.mention} — {reason}")
        await log_action(ctx.guild, f"{ctx.author} banned {member} — {reason}")

    @commands.command(name="unban")
    @commands.has_permissions(ban_members=True)
    async def unban(self, ctx, user_id: int):
        user = await self.bot.fetch_user(user_id)
        await ctx.guild.unban(user)
        await ctx.send(f"Unbanned {user}")
        await log_action(ctx.guild, f"{ctx.author} unbanned {user}")

    @commands.command(name="timeout")
    @commands.has_permissions(moderate_members=True)
    async def timeout(self, ctx, member: discord.Member, duration: str, *, reason: str = "No reason given"):
        seconds = parse_duration(duration)
        if not seconds:
            await ctx.send("Duration format: 10s / 10m / 1h / 1d")
            return
        await member.timeout(discord.utils.utcnow() + timedelta(seconds=seconds), reason=reason)
        await ctx.send(f"Timed out {member.mention} for {duration} — {reason}")
        await log_action(ctx.guild, f"{ctx.author} timed out {member} for {duration} — {reason}")

    @commands.command(name="untimeout")
    @commands.has_permissions(moderate_members=True)
    async def untimeout(self, ctx, member: discord.Member):
        await member.timeout(None)
        await ctx.send(f"Removed timeout from {member.mention}")

    @commands.command(name="warn")
    @commands.has_permissions(moderate_members=True)
    async def warn(self, ctx, member: discord.Member, *, reason: str = "No reason given"):
        config = json_store.get_guild_config(ctx.guild.id)
        config.setdefault("warnings", {})
        config["warnings"].setdefault(str(member.id), [])
        config["warnings"][str(member.id)].append(reason)
        json_store.set_guild_config(ctx.guild.id, config)
        await ctx.send(f"Warned {member.mention} — {reason}")
        await log_action(ctx.guild, f"{ctx.author} warned {member} — {reason}")

    @commands.command(name="warnings")
    async def warnings_cmd(self, ctx, member: discord.Member):
        config = json_store.get_guild_config(ctx.guild.id)
        warns = config.get("warnings", {}).get(str(member.id), [])
        if not warns:
            await ctx.send(f"{member.mention} has no warnings.")
            return
        text = "\n".join(f"{i+1}. {w}" for i, w in enumerate(warns))
        await ctx.send(f"Warnings for {member.mention}:\n{text}")

    @commands.command(name="clearwarnings")
    @commands.has_permissions(moderate_members=True)
    async def clearwarnings(self, ctx, member: discord.Member):
        config = json_store.get_guild_config(ctx.guild.id)
        config.get("warnings", {}).pop(str(member.id), None)
        json_store.set_guild_config(ctx.guild.id, config)
        await ctx.send(f"Cleared warnings for {member.mention}")

    @commands.command(name="clear")
    @commands.has_permissions(manage_messages=True)
    async def clear(self, ctx, amount: int):
        deleted = await ctx.channel.purge(limit=amount + 1)
        msg = await ctx.send(f"Deleted {len(deleted) - 1} messages.")
        await msg.delete(delay=3)

    @commands.command(name="lock")
    @commands.has_permissions(manage_channels=True)
    async def lock(self, ctx):
        await ctx.channel.set_permissions(ctx.guild.default_role, send_messages=False)
        await ctx.send("Channel locked.")

    @commands.command(name="unlock")
    @commands.has_permissions(manage_channels=True)
    async def unlock(self, ctx):
        await ctx.channel.set_permissions(ctx.guild.default_role, send_messages=None)
        await ctx.send("Channel unlocked.")

    @commands.command(name="slowmode")
    @commands.has_permissions(manage_channels=True)
    async def slowmode(self, ctx, seconds: int):
        await ctx.channel.edit(slowmode_delay=seconds)
        await ctx.send(f"Slowmode set to {seconds}s.")

    @commands.command(name="say")
    @commands.has_permissions(manage_messages=True)
    async def say(self, ctx, *, message: str):
        await ctx.message.delete()
        await ctx.send(message)

    @commands.command(name="userinfo")
    async def userinfo(self, ctx, member: discord.Member = None):
        member = member or ctx.author
        embed = discord.Embed(title=str(member))
        embed.add_field(name="Joined server", value=discord.utils.format_dt(member.joined_at, "R"))
        embed.add_field(name="Account created", value=discord.utils.format_dt(member.created_at, "R"))
        embed.set_thumbnail(url=member.display_avatar.url)
        await ctx.send(embed=embed)

    @commands.command(name="serverinfo")
    async def serverinfo(self, ctx):
        guild = ctx.guild
        embed = discord.Embed(title=guild.name)
        embed.add_field(name="Members", value=guild.member_count)
        embed.add_field(name="Created", value=discord.utils.format_dt(guild.created_at, "R"))
        if guild.icon:
            embed.set_thumbnail(url=guild.icon.url)
        await ctx.send(embed=embed)

    @commands.command(name="setlogchannel")
    @commands.has_permissions(administrator=True)
    async def setlogchannel(self, ctx, channel: discord.TextChannel):
        config = json_store.get_guild_config(ctx.guild.id)
        config["log_channel_id"] = channel.id
        json_store.set_guild_config(ctx.guild.id, config)
        await ctx.send(f"Mod-action logs will go to {channel.mention}")

    @commands.command(name="announce")
    @commands.has_permissions(manage_messages=True)
    async def announce(self, ctx, channel: discord.TextChannel, *, message: str):
        await channel.send(message)
        await ctx.send(f"Announced in {channel.mention}")

    @commands.command(name="poll")
    async def poll(self, ctx, *, question: str):
        msg = await ctx.send(f"📊 {question}")
        await msg.add_reaction("👍")
        await msg.add_reaction("👎")


async def setup(bot):
    await bot.add_cog(Moderation(bot))
