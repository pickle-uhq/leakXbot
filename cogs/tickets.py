import asyncio
import discord
from discord import app_commands
from discord.ext import commands

import json_store

sessions = {}  # in-memory /setup session per admin

FIELD_LABELS = {
    "title": "Title",
    "desc": "Description",
    "field1": "Field 1",
    "author": "Author",
    "footer": "Footer",
    "image": "Image URL",
    "thumb": "Thumbnail URL",
    "color": "Embed color (hex, e.g. #378ADD)",
}


def build_embed(data: dict) -> discord.Embed:
    color_hex = data.get("color") or "#378ADD"
    try:
        color = discord.Color(int(color_hex.lstrip("#"), 16))
    except Exception:
        color = discord.Color.blurple()

    embed = discord.Embed(
        title=data.get("title") or "Need help?",
        description=data.get("desc") or "Click below to open a ticket.",
        color=color,
    )
    if data.get("field1"):
        embed.add_field(name="Info", value=data["field1"], inline=False)
    if data.get("author"):
        embed.set_author(name=data["author"])
    if data.get("footer"):
        embed.set_footer(text=data["footer"])
    if data.get("image"):
        embed.set_image(url=data["image"])
    if data.get("thumb"):
        embed.set_thumbnail(url=data["thumb"])
    return embed


def build_category_list_text(categories):
    if not categories:
        return "No categories added yet."
    return "\n".join(f"{c['emoji']} **{c['name']}** — {c['desc']} (role: <@&{c['role_id']}>)" for c in categories)


# ---------- /setup: embed builder ----------

class FieldEditModal(discord.ui.Modal):
    def __init__(self, field_key, current_value, message, user_id):
        super().__init__(title=f"Edit {FIELD_LABELS[field_key]}")
        self.field_key = field_key
        self.message = message
        self.user_id = user_id
        self.input = discord.ui.TextInput(
            label=FIELD_LABELS[field_key],
            default=current_value or "",
            required=False,
            style=discord.TextStyle.paragraph if field_key == "desc" else discord.TextStyle.short,
            max_length=1000,
        )
        self.add_item(self.input)

    async def on_submit(self, interaction: discord.Interaction):
        session = sessions.setdefault(self.user_id, {"embed": {}, "categories": []})
        session["embed"][self.field_key] = self.input.value
        await self.message.edit(embed=build_embed(session["embed"]))
        await interaction.response.defer()


class EmbedBuilderView(discord.ui.View):
    def __init__(self, user_id):
        super().__init__(timeout=900)
        self.user_id = user_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.user_id

    @discord.ui.select(
        placeholder="Choose a field to edit",
        options=[discord.SelectOption(label=v, value=k) for k, v in FIELD_LABELS.items()],
    )
    async def select_field(self, interaction: discord.Interaction, select: discord.ui.Select):
        key = select.values[0]
        session = sessions.setdefault(self.user_id, {"embed": {}, "categories": []})
        current = session["embed"].get(key, "")
        await interaction.response.send_modal(FieldEditModal(key, current, interaction.message, self.user_id))

    @discord.ui.button(label="Confirm embed", style=discord.ButtonStyle.primary)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(view=self)
        session = sessions.setdefault(self.user_id, {"embed": {}, "categories": []})
        await interaction.followup.send(
            content=f"**Ticket categories**\n{build_category_list_text(session['categories'])}",
            view=CategoryFlowView(self.user_id),
            ephemeral=True,
        )


# ---------- /setup: category builder ----------

class CategoryDetailsModal(discord.ui.Modal, title="New ticket category"):
    name = discord.ui.TextInput(label="Category name", max_length=100)
    desc = discord.ui.TextInput(label="Description", max_length=200)
    emoji = discord.ui.TextInput(label="Emoji", max_length=10, required=False, default="🎫")

    def __init__(self, user_id):
        super().__init__()
        self.user_id = user_id

    async def on_submit(self, interaction: discord.Interaction):
        session = sessions.setdefault(self.user_id, {"embed": {}, "categories": []})
        session["_pending"] = {"name": self.name.value, "desc": self.desc.value, "emoji": self.emoji.value or "🎫"}
        await interaction.response.send_message(
            "Pick the Discord category to create tickets in, and the support role:",
            view=CategoryLinkView(self.user_id),
            ephemeral=True,
        )


class CategoryLinkView(discord.ui.View):
    def __init__(self, user_id):
        super().__init__(timeout=900)
        self.user_id = user_id
        self.chosen_category_id = None
        self.chosen_role_id = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.user_id

    @discord.ui.select(cls=discord.ui.ChannelSelect, placeholder="Ticket channel category",
                        channel_types=[discord.ChannelType.category])
    async def pick_category(self, interaction: discord.Interaction, select: discord.ui.ChannelSelect):
        self.chosen_category_id = select.values[0].id
        await interaction.response.defer()

    @discord.ui.select(cls=discord.ui.RoleSelect, placeholder="Support role (can view tickets)")
    async def pick_role(self, interaction: discord.Interaction, select: discord.ui.RoleSelect):
        self.chosen_role_id = select.values[0].id
        await interaction.response.defer()

    @discord.ui.button(label="Add this category", style=discord.ButtonStyle.success)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.chosen_category_id or not self.chosen_role_id:
            await interaction.response.send_message("Pick both a category and a role first.", ephemeral=True)
            return
        session = sessions.setdefault(self.user_id, {"embed": {}, "categories": []})
        pending = session.pop("_pending", {})
        pending["ticket_category_id"] = self.chosen_category_id
        pending["role_id"] = self.chosen_role_id
        session["categories"].append(pending)
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(view=self)
        await interaction.followup.send(
            content=f"Added. **Ticket categories so far**\n{build_category_list_text(session['categories'])}",
            view=CategoryFlowView(self.user_id),
            ephemeral=True,
        )


class CategoryFlowView(discord.ui.View):
    def __init__(self, user_id):
        super().__init__(timeout=900)
        self.user_id = user_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.user_id

    @discord.ui.button(label="Add category", style=discord.ButtonStyle.secondary, emoji="➕")
    async def add_category(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(CategoryDetailsModal(self.user_id))

    @discord.ui.button(label="Save & post panel", style=discord.ButtonStyle.primary)
    async def save(self, interaction: discord.Interaction, button: discord.ui.Button):
        session = sessions.get(self.user_id, {"embed": {}, "categories": []})
        if not session["categories"]:
            await interaction.response.send_message("Add at least one category first.", ephemeral=True)
            return
        embed = build_embed(session["embed"])
        await interaction.channel.send(embed=embed, view=TicketPanelView(session["categories"]))
        config = json_store.get_guild_config(interaction.guild_id)
        config["embed"] = session["embed"]
        config["categories"] = session["categories"]
        json_store.set_guild_config(interaction.guild_id, config)
        sessions.pop(self.user_id, None)
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(view=self)
        await interaction.followup.send("Panel posted.", ephemeral=True)


# ---------- ticket creation / closing ----------

def find_or_create_target_category(guild, base_category):
    if base_category is not None and len(base_category.channels) < 50:
        return base_category
    base_name = base_category.name if base_category else "Tickets"
    n = 2
    candidate = f"{base_name}-{n}"
    existing = discord.utils.get(guild.categories, name=candidate)
    while existing and len(existing.channels) >= 50:
        n += 1
        candidate = f"{base_name}-{n}"
        existing = discord.utils.get(guild.categories, name=candidate)
    return existing  # None means caller should create `candidate`


async def create_ticket_channel(interaction: discord.Interaction, category_data: dict):
    guild = interaction.guild
    base_category = guild.get_channel(category_data["ticket_category_id"])
    target = find_or_create_target_category(guild, base_category)

    if target is None:
        base_name = base_category.name if base_category else "Tickets"
        n = 2
        name = f"{base_name}-{n}"
        while discord.utils.get(guild.categories, name=name):
            n += 1
            name = f"{base_name}-{n}"
        target = await guild.create_category(name)

    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        interaction.user: discord.PermissionOverwrite(view_channel=True, send_messages=True),
    }
    role = guild.get_role(category_data["role_id"])
    if role:
        overwrites[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True)

    channel = await guild.create_text_channel(f"ticket-{interaction.user.name}", category=target, overwrites=overwrites)

    config = json_store.get_guild_config(guild.id)
    config.setdefault("tickets", {})
    config["tickets"][str(channel.id)] = {"user_id": interaction.user.id, "category_name": category_data["name"]}
    json_store.set_guild_config(guild.id, config)

    welcome = discord.Embed(
        title=f"{category_data['emoji']} {category_data['name']}",
        description=f"Thanks {interaction.user.mention}, support will be with you shortly.",
    )
    await channel.send(embed=welcome, view=CloseTicketView())
    await interaction.response.send_message(f"Ticket created: {channel.mention}", ephemeral=True)


class TicketOpenSelect(discord.ui.Select):
    def __init__(self, categories):
        options = [
            discord.SelectOption(label=c["name"], description=c["desc"][:100], emoji=c["emoji"], value=str(i))
            for i, c in enumerate(categories)
        ]
        super().__init__(placeholder="Open a ticket...", options=options, custom_id="ticket_panel_select")
        self.categories = categories

    async def callback(self, interaction: discord.Interaction):
        config = json_store.get_guild_config(interaction.guild_id)
        categories = config.get("categories", self.categories)
        await create_ticket_channel(interaction, categories[int(self.values[0])])


class TicketPanelView(discord.ui.View):
    def __init__(self, categories):
        super().__init__(timeout=None)
        self.add_item(TicketOpenSelect(categories))


class CloseTicketView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Close ticket", style=discord.ButtonStyle.danger, custom_id="close_ticket", emoji="🔒")
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button):
        config = json_store.get_guild_config(interaction.guild_id)
        config.get("tickets", {}).pop(str(interaction.channel_id), None)
        json_store.set_guild_config(interaction.guild_id, config)
        await interaction.response.send_message("Closing this ticket in 5 seconds...")
        await asyncio.sleep(5)
        await interaction.channel.delete()


class Tickets(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        data = json_store.load()
        for config in data.values():
            categories = config.get("categories")
            if categories:
                self.bot.add_view(TicketPanelView(categories))
        self.bot.add_view(CloseTicketView())

    @app_commands.command(name="setup", description="Build and post the ticket panel")
    @app_commands.checks.has_permissions(administrator=True)
    async def setup(self, interaction: discord.Interaction):
        sessions[interaction.user.id] = {"embed": {}, "categories": []}
        await interaction.response.send_message(
            embed=build_embed({}), view=EmbedBuilderView(interaction.user.id), ephemeral=True
        )

    @commands.command(name="addticket")
    @commands.has_permissions(manage_channels=True)
    async def addticket(self, ctx: commands.Context, member: discord.Member):
        config = json_store.get_guild_config(ctx.guild.id)
        if str(ctx.channel.id) not in config.get("tickets", {}):
            await ctx.send("This isn't a ticket channel.")
            return
        await ctx.channel.set_permissions(member, view_channel=True, send_messages=True)
        await ctx.send(f"{member.mention} added to this ticket.")


async def setup(bot):
    await bot.add_cog(Tickets(bot))

