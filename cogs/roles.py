import discord
from discord.ext import commands

import json_store


class Roles(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # ---------- auto join role ----------

    @commands.command(name="setautorole")
    @commands.has_permissions(administrator=True)
    async def setautorole(self, ctx, role: discord.Role):
        config = json_store.get_guild_config(ctx.guild.id)
        config["autorole_id"] = role.id
        json_store.set_guild_config(ctx.guild.id, config)
        await ctx.send(f"New members will now get {role.mention} automatically.")

    @commands.command(name="autoroleoff")
    @commands.has_permissions(administrator=True)
    async def autoroleoff(self, ctx):
        config = json_store.get_guild_config(ctx.guild.id)
        config.pop("autorole_id", None)
        json_store.set_guild_config(ctx.guild.id, config)
        await ctx.send("Auto join role turned off.")

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        config = json_store.get_guild_config(member.guild.id)
        role_id = config.get("autorole_id")
        if role_id:
            role = member.guild.get_role(role_id)
            if role:
                try:
                    await member.add_roles(role, reason="Auto join role")
                except discord.Forbidden:
                    pass

    # ---------- status role (self-editable keyword -> role) ----------

    @commands.command(name="setstatusrole")
    @commands.has_permissions(administrator=True)
    async def setstatusrole(self, ctx, keyword: str, role: discord.Role):
        config = json_store.get_guild_config(ctx.guild.id)
        config.setdefault("status_roles", {})
        config["status_roles"][keyword.lower()] = role.id
        json_store.set_guild_config(ctx.guild.id, config)
        await ctx.send(f"Members whose status contains \"{keyword}\" will now get {role.mention}.")

    @commands.command(name="removestatusrole")
    @commands.has_permissions(administrator=True)
    async def removestatusrole(self, ctx, keyword: str):
        config = json_store.get_guild_config(ctx.guild.id)
        config.get("status_roles", {}).pop(keyword.lower(), None)
        json_store.set_guild_config(ctx.guild.id, config)
        await ctx.send(f"Status role rule for \"{keyword}\" removed.")

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        config = json_store.get_guild_config(after.guild.id)
        rules = config.get("status_roles", {})
        if not rules:
            return

        custom_status = ""
        for activity in after.activities:
            if isinstance(activity, discord.CustomActivity) and activity.name:
                custom_status = activity.name.lower()
                break

        for keyword, role_id in rules.items():
            role = after.guild.get_role(role_id)
            if not role:
                continue
            has_role = role in after.roles
            matches = keyword in custom_status
            try:
                if matches and not has_role:
                    await after.add_roles(role, reason="Status role match")
                elif not matches and has_role:
                    await after.remove_roles(role, reason="Status role no longer matches")
            except discord.Forbidden:
                pass

    # ---------- manual toggle role ----------

    @commands.command(name="role")
    @commands.has_permissions(manage_roles=True)
    async def role(self, ctx, member: discord.Member, role: discord.Role):
        if role in member.roles:
            await member.remove_roles(role)
            await ctx.send(f"Removed {role.mention} from {member.mention}.")
        else:
            await member.add_roles(role)
            await ctx.send(f"Gave {role.mention} to {member.mention}.")


async def setup(bot):
    await bot.add_cog(Roles(bot))
