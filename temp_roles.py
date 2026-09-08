import re
import time
from typing import Optional

import aiosqlite
import discord
from discord import app_commands
from discord.ext import commands, tasks

DB_PATH = "data/temproles.db"

DURATION_RE = re.compile(r"^(\d+)([hd])$", re.IGNORECASE)
UNIT_SECONDS = {"h": 3600, "d": 86400}


def parse_duration(duration: str) -> Optional[int]:
    """'1h', '7d', '365d' -> seconds. Returns None if the format is invalid."""
    match = DURATION_RE.match(duration.strip())
    if not match:
        return None
    amount, unit = int(match.group(1)), match.group(2).lower()
    if amount <= 0:
        return None
    return amount * UNIT_SECONDS[unit]


def error_embed(description: str) -> discord.Embed:
    return discord.Embed(title="❌ Error", description=description, color=discord.Color.red(),
                          timestamp=discord.utils.utcnow())


class TempRoles(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.db: Optional[aiosqlite.Connection] = None

    async def cog_load(self):
        self.db = await aiosqlite.connect(DB_PATH)
        await self.db.execute("""
            CREATE TABLE IF NOT EXISTS temp_roles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                role_id INTEGER NOT NULL,
                moderator_id INTEGER NOT NULL,
                duration TEXT NOT NULL,
                expiry_timestamp INTEGER NOT NULL,
                reason TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                created_at INTEGER NOT NULL
            )
        """)
        await self.db.execute("""
            CREATE TABLE IF NOT EXISTS guild_config (
                guild_id INTEGER PRIMARY KEY,
                log_channel_id INTEGER
            )
        """)
        await self.db.commit()
        self.check_expired_roles.start()

    async def cog_unload(self):
        self.check_expired_roles.cancel()
        if self.db:
            await self.db.close()

    # ---------- helpers ----------

    async def has_active_temp_role(self, guild_id: int, user_id: int, role_id: int) -> bool:
        cursor = await self.db.execute(
            "SELECT 1 FROM temp_roles WHERE guild_id=? AND user_id=? AND role_id=? AND status='active'",
            (guild_id, user_id, role_id),
        )
        row = await cursor.fetchone()
        await cursor.close()
        return row is not None

    async def get_log_channel(self, guild: discord.Guild) -> Optional[discord.TextChannel]:
        cursor = await self.db.execute("SELECT log_channel_id FROM guild_config WHERE guild_id=?", (guild.id,))
        row = await cursor.fetchone()
        await cursor.close()
        if not row or not row[0]:
            return None
        return guild.get_channel(row[0])

    def hierarchy_ok(self, guild: discord.Guild, actor: discord.Member, role: discord.Role) -> Optional[str]:
        """Returns an error message if the assignment/removal would break Discord's role hierarchy, else None."""
        if role >= guild.me.top_role:
            return "I can't manage a role that's higher than or equal to my own top role."
        if not actor.guild_permissions.administrator and role >= actor.top_role:
            return "You can't manage a role that's higher than or equal to your own top role."
        return None

    # ---------- /temprole ----------

    @app_commands.command(name="temprole", description="Temporarily give a member a role")
    @app_commands.describe(
        user="Member to give the role to",
        role="Role to assign",
        duration="e.g. 1h, 1d, 7d, 30d, 365d",
        reason="Optional reason",
    )
    @app_commands.checks.has_permissions(manage_roles=True)
    async def temprole(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        role: discord.Role,
        duration: str,
        reason: Optional[str] = None,
    ):
        await interaction.response.defer(ephemeral=True)
        reason = reason or "No reason provided"

        seconds = parse_duration(duration)
        if seconds is None:
            await interaction.followup.send(
                embed=error_embed("Invalid duration format. Use e.g. `1h`, `1d`, `7d`, `30d`, `365d`."),
                ephemeral=True,
            )
            return

        hierarchy_error = self.hierarchy_ok(interaction.guild, interaction.user, role)
        if hierarchy_error:
            await interaction.followup.send(embed=error_embed(hierarchy_error), ephemeral=True)
            return

        if await self.has_active_temp_role(interaction.guild_id, user.id, role.id):
            await interaction.followup.send(
                embed=error_embed(f"{user.mention} already has an active temporary **{role.name}** role."),
                ephemeral=True,
            )
            return

        try:
            await user.add_roles(role, reason=f"Temprole by {interaction.user} — {reason}")
        except discord.Forbidden:
            await interaction.followup.send(
                embed=error_embed("I don't have permission to assign that role."), ephemeral=True
            )
            return

        now = int(time.time())
        expiry = now + seconds
        await self.db.execute(
            """INSERT INTO temp_roles
               (guild_id, user_id, role_id, moderator_id, duration, expiry_timestamp, reason, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?)""",
            (interaction.guild_id, user.id, role.id, interaction.user.id, duration, expiry, reason, now),
        )
        await self.db.commit()

        embed = discord.Embed(
            title="✅ Temporary Role Assigned",
            color=discord.Color.green(),
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name="User", value=user.mention, inline=True)
        embed.add_field(name="Role", value=role.mention, inline=True)
        embed.add_field(name="Duration", value=duration, inline=True)
        embed.add_field(name="Expires", value=f"<t:{expiry}:R>", inline=True)
        embed.add_field(name="Moderator", value=interaction.user.mention, inline=True)
        embed.add_field(name="Reason", value=reason, inline=False)
        await interaction.followup.send(embed=embed, ephemeral=True)

    @temprole.error
    async def temprole_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message(
                embed=error_embed("You need the **Manage Roles** permission to use this."), ephemeral=True
            )

    # ---------- /set-temprole-logs ----------

    @app_commands.command(name="set-temprole-logs", description="Set the channel for temp-role expiry logs")
    @app_commands.describe(channel="Text channel to send expiry logs to")
    @app_commands.checks.has_permissions(administrator=True)
    async def set_temprole_logs(self, interaction: discord.Interaction, channel: discord.TextChannel):
        await self.db.execute(
            "INSERT INTO guild_config (guild_id, log_channel_id) VALUES (?, ?) "
            "ON CONFLICT(guild_id) DO UPDATE SET log_channel_id=excluded.log_channel_id",
            (interaction.guild_id, channel.id),
        )
        await self.db.commit()
        embed = discord.Embed(
            description=f"Temp-role expiry logs will now be sent to {channel.mention}.",
            color=discord.Color.green(),
            timestamp=discord.utils.utcnow(),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @set_temprole_logs.error
    async def set_temprole_logs_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message(
                embed=error_embed("Only administrators can set the log channel."), ephemeral=True
            )

    # ---------- expiry loop ----------

    @tasks.loop(minutes=1)
    async def check_expired_roles(self):
        now = int(time.time())
        cursor = await self.db.execute(
            "SELECT id, guild_id, user_id, role_id, moderator_id, reason FROM temp_roles "
            "WHERE status='active' AND expiry_timestamp <= ?",
            (now,),
        )
        expired_rows = await cursor.fetchall()
        await cursor.close()

        for row_id, guild_id, user_id, role_id, moderator_id, reason in expired_rows:
            await self.db.execute("UPDATE temp_roles SET status='completed' WHERE id=?", (row_id,))

            guild = self.bot.get_guild(guild_id)
            if not guild:
                continue

            role = guild.get_role(role_id)
            member = guild.get_member(user_id) or await self._safe_fetch_member(guild, user_id)

            if member and role and role in member.roles:
                if role < guild.me.top_role:
                    try:
                        await member.remove_roles(role, reason="Temporary role expired")
                    except discord.Forbidden:
                        pass

            await self._send_expiry_log(guild, user_id, role_id, moderator_id, reason)

        await self.db.commit()

    async def _safe_fetch_member(self, guild: discord.Guild, user_id: int):
        try:
            return await guild.fetch_member(user_id)
        except discord.NotFound:
            return None

    async def _send_expiry_log(self, guild, user_id, role_id, moderator_id, reason):
        log_channel = await self.get_log_channel(guild)
        if not log_channel:
            return

        user_display = f"<@{user_id}>"
        role_display = f"<@&{role_id}>" if guild.get_role(role_id) else f"`{role_id}` (deleted)"
        moderator_display = f"<@{moderator_id}>"

        embed = discord.Embed(
            title="⏰ Temporary Role Expired",
            color=discord.Color.orange(),
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name="User", value=user_display, inline=True)
        embed.add_field(name="Role", value=role_display, inline=True)
        embed.add_field(name="Expired At", value=discord.utils.format_dt(discord.utils.utcnow(), "F"), inline=False)
        embed.add_field(name="Action", value="Role Removed Automatically", inline=False)
        embed.add_field(name="Moderator", value=moderator_display, inline=True)
        embed.add_field(name="Reason", value=reason, inline=False)

        try:
            await log_channel.send(embed=embed)
        except discord.Forbidden:
            pass

    @check_expired_roles.before_loop
    async def before_check_expired_roles(self):
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    await bot.add_cog(TempRoles(bot))
