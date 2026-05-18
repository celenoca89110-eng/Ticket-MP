"""
Panneaux Discord pour /botconfig (édition de config.json).
"""
from __future__ import annotations

import json
import re
from typing import Any, Callable

import discord


def ticket_client_overwrite() -> discord.PermissionOverwrite:
    """Demandeurs : lisent le salon, écrivent uniquement via MP avec le bot."""
    return discord.PermissionOverwrite(
        view_channel=True,
        read_messages=True,
        send_messages=False,
        attach_files=False,
        embed_links=False,
        add_reactions=False,
        mention_everyone=False,
    )


def can_use_bot_panel(member: discord.Member, cfg: dict) -> bool:
    raw = cfg.get("panel_admin_ids")
    ids = {str(x) for x in raw} if isinstance(raw, list) else set()
    uid = str(member.id)
    if uid in ids:
        return True
    if member.guild_permissions.administrator:
        return True
    if member.id == member.guild.owner_id:
        return True
    return False


class ConfigController:
    """Référence mutable vers bot.config + sauvegarde disque."""

    def __init__(
        self,
        bot: discord.Client,
        config_dict: dict,
        config_path: str,
        load_fn: Callable[[str], dict],
    ):
        self.bot = bot
        self.config = config_dict
        self.path = config_path
        self.load_fn = load_fn

    def save(self) -> None:
        if "guilds" not in self.config:
            self.config["guilds"] = {}
        if "panel_admin_ids" not in self.config:
            self.config["panel_admin_ids"] = []
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self.config, f, indent=4, ensure_ascii=False)
        new = self.load_fn(self.path)
        self.config.clear()
        self.config.update(new)


def ensure_guild_entry(cfg: dict, gid: str, guild: discord.Guild | None) -> dict:
    guilds = cfg.setdefault("guilds", {})
    if gid not in guilds:
        guilds[gid] = {
            "name": guild.name if guild else "Serveur",
            "ticket": {
                "category_id": None,
                "log_channel_id": None,
                "max_open_per_user": 1,
                "cooldown_seconds": 30,
            },
            "admin_roles": [],
            "categories": {
                "support": {"label": "🛠️ Support", "emoji": "🛠️"},
            },
        }
    return guilds[gid]


# --- Modals ---


class AddGuildByIdModal(discord.ui.Modal, title="Ajouter un serveur"):
    gid_field = discord.ui.TextInput(
        label="ID du serveur Discord",
        placeholder="Ex : 1495229993624801340",
        max_length=22,
        required=True,
    )

    def __init__(self, ctrl: ConfigController):
        super().__init__()
        self.ctrl = ctrl

    async def on_submit(self, interaction: discord.Interaction):
        raw = str(self.gid_field.value).strip()
        try:
            gid_int = int(raw)
        except ValueError:
            return await interaction.response.send_message(
                "❌ ID invalide.", ephemeral=True
            )
        gid = str(gid_int)
        g = self.ctrl.bot.get_guild(gid_int)
        if not g:
            return await interaction.response.send_message(
                "❌ Le bot n’est pas **sur ce serveur**. Invite-le d’abord, puis réessaie.",
                ephemeral=True,
            )
        ensure_guild_entry(self.ctrl.config, gid, g)
        self.ctrl.config["guilds"][gid]["name"] = g.name
        self.ctrl.save()
        await interaction.response.send_message(
            f"✅ Serveur ajouté à la config : **{g.name}** (`{gid}`).\n"
            "Configure la catégorie tickets et les salons avant usage.",
            ephemeral=True,
        )


class PanelAdminsModal(discord.ui.Modal, title="IDs autorisés pour /botconfig"):
    ids_field = discord.ui.TextInput(
        label="IDs Discord (virgule ou ligne)",
        style=discord.TextStyle.paragraph,
        placeholder="123...\n456...\n(Laisse vide + valider = liste vide : seuls Admin Discord / Owner)",
        required=False,
        max_length=1800,
    )

    def __init__(self, ctrl: ConfigController):
        super().__init__()
        self.ctrl = ctrl

    async def on_submit(self, interaction: discord.Interaction):
        text = self.ids_field.value or ""
        parts = re.split(r"[\s,;]+", text.strip())
        out: list[str] = []
        for p in parts:
            if not p:
                continue
            if p.isdigit():
                out.append(p)
        self.ctrl.config["panel_admin_ids"] = out
        self.ctrl.save()
        await interaction.response.send_message(
            f"✅ Liste mise à jour ({len(out)} ID(s)). Les **Administrateurs Discord** "
            "et le **propriétaire du serveur** restent toujours autorisés.",
            ephemeral=True,
        )


class AddCategoryModal(discord.ui.Modal, title="Entrée menu ticket"):
    key_field = discord.ui.TextInput(
        label="Clé (sans espace)",
        placeholder="support",
        max_length=40,
        required=True,
    )
    label_field = discord.ui.TextInput(
        label="Libellé bouton",
        placeholder="🛠️ Support",
        max_length=80,
        required=True,
    )
    emoji_field = discord.ui.TextInput(
        label="Emoji (optionnel)",
        placeholder="🛠️",
        max_length=10,
        required=False,
    )

    def __init__(self, ctrl: ConfigController, gid: str):
        super().__init__()
        self.ctrl = ctrl
        self.gid = gid

    async def on_submit(self, interaction: discord.Interaction):
        key = re.sub(r"\s+", "_", self.key_field.value.strip().lower())
        if not key:
            return await interaction.response.send_message(
                "❌ Clé invalide.", ephemeral=True
            )
        gcfg = ensure_guild_entry(self.ctrl.config, self.gid, interaction.guild)
        cats = gcfg.setdefault("categories", {})
        em = (self.emoji_field.value or "").strip() or None
        cats[key] = {"label": self.label_field.value.strip(), "emoji": em}
        self.ctrl.save()
        await interaction.response.send_message(
            f"✅ Catégorie `{key}` ajoutée.", ephemeral=True
        )


# --- Views channel / role ---


class PickCategoryView(discord.ui.View):
    def __init__(self, ctrl: ConfigController, gid: str):
        super().__init__(timeout=180)
        self.ctrl = ctrl
        self.gid = gid
        sel = discord.ui.ChannelSelect(
            placeholder="Catégorie parent des tickets",
            channel_types=[discord.ChannelType.category],
            min_values=1,
            max_values=1,
        )

        async def _cb(interaction: discord.Interaction):
            cid = int(sel.values[0].id)
            gcfg = ensure_guild_entry(self.ctrl.config, self.gid, interaction.guild)
            gcfg.setdefault("ticket", {})["category_id"] = cid
            self.ctrl.save()
            await interaction.response.send_message(
                f"✅ `ticket.category_id` = `{cid}`", ephemeral=True
            )

        sel.callback = _cb
        self.add_item(sel)


class PickLogChannelView(discord.ui.View):
    def __init__(self, ctrl: ConfigController, gid: str):
        super().__init__(timeout=180)
        self.ctrl = ctrl
        self.gid = gid
        sel = discord.ui.ChannelSelect(
            placeholder="Salon texte pour les logs",
            channel_types=[discord.ChannelType.text],
            min_values=1,
            max_values=1,
        )

        async def _cb(interaction: discord.Interaction):
            cid = int(sel.values[0].id)
            gcfg = ensure_guild_entry(self.ctrl.config, self.gid, interaction.guild)
            gcfg.setdefault("ticket", {})["log_channel_id"] = cid
            self.ctrl.save()
            await interaction.response.send_message(
                f"✅ `ticket.log_channel_id` = `{cid}`", ephemeral=True
            )

        sel.callback = _cb
        self.add_item(sel)


class PickStaffRolesView(discord.ui.View):
    def __init__(self, ctrl: ConfigController, gid: str):
        super().__init__(timeout=180)
        self.ctrl = ctrl
        self.gid = gid
        sel = discord.ui.RoleSelect(
            placeholder="Rôles staff (accès tickets)",
            min_values=0,
            max_values=25,
        )

        async def _cb(interaction: discord.Interaction):
            rids = [r.id for r in sel.values]
            gcfg = ensure_guild_entry(self.ctrl.config, self.gid, interaction.guild)
            gcfg["admin_roles"] = rids
            self.ctrl.save()
            await interaction.response.send_message(
                f"✅ {len(rids)} rôle(s) enregistré(s).", ephemeral=True
            )

        sel.callback = _cb
        self.add_item(sel)


class RemoveCategoryView(discord.ui.View):
    def __init__(self, ctrl: ConfigController, gid: str):
        super().__init__(timeout=180)
        self.ctrl = ctrl
        self.gid = gid
        gcfg = ctrl.config.get("guilds", {}).get(gid, {})
        cats = gcfg.get("categories") or {}
        opts = [
            discord.SelectOption(label=k[:100], value=k, description=v.get("label", "")[:100])
            for k, v in list(cats.items())[:25]
        ]
        sel = discord.ui.Select(placeholder="Clé à supprimer", options=opts)

        async def _cb(interaction: discord.Interaction):
            k = sel.values[0]
            cats2 = (
                self.ctrl.config.get("guilds", {}).get(self.gid, {}).get("categories") or {}
            )
            cats2.pop(k, None)
            self.ctrl.save()
            await interaction.response.send_message(
                f"✅ Clé `{k}` supprimée.", ephemeral=True
            )

        sel.callback = _cb
        self.add_item(sel)


class ConfigRootView(discord.ui.View):
    def __init__(self, ctrl: ConfigController, gid: str, theme: discord.Color):
        super().__init__(timeout=900)
        self.ctrl = ctrl
        self.gid = gid
        self._theme = theme

    def build_embed(self) -> discord.Embed:
        cfg = self.ctrl.config
        gcfg = cfg.get("guilds", {}).get(self.gid, {})
        tick = gcfg.get("ticket") or {}
        cat_id = tick.get("category_id")
        log_id = tick.get("log_channel_id")
        roles = gcfg.get("admin_roles") or []
        cats = list((gcfg.get("categories") or {}).keys())
        admins = cfg.get("panel_admin_ids")
        emb = discord.Embed(
            title="⚙️ Configuration TicketMP",
            description=(
                "**Demandeur** : écrit **uniquement en MP** → le bot poste dans le salon.\n"
                "**Staff** : répond dans le salon → le bot renvoie en **MP** au demandeur."
            ),
            color=self._theme,
        )
        emb.add_field(name="Serveur", value=f"`{self.gid}` · {gcfg.get('name', '—')}", inline=False)
        emb.add_field(
            name="Catégorie tickets",
            value=f"`{cat_id}`" if cat_id else "⚠️ *à définir*",
            inline=True,
        )
        emb.add_field(
            name="Salon logs",
            value=f"`{log_id}`" if log_id else "⚠️ *à définir*",
            inline=True,
        )
        emb.add_field(
            name="Rôles staff",
            value=", ".join(f"<@&{r}>" for r in roles) if roles else "*aucun*",
            inline=False,
        )
        emb.add_field(
            name="Clés menu",
            value=", ".join(f"`{c}`" for c in cats) or "—",
            inline=False,
        )
        emb.add_field(
            name="Panneau /botconfig",
            value=(
                ", ".join(f"`{a}`" for a in admins)
                if isinstance(admins, list) and admins
                else "*Admins Discord + owner*"
            ),
            inline=False,
        )
        return emb

    @discord.ui.button(label="📁 Catégorie tickets", style=discord.ButtonStyle.primary, row=0)
    async def b_cat(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            "Choisis la catégorie Discord :",
            view=PickCategoryView(self.ctrl, self.gid),
            ephemeral=True,
        )

    @discord.ui.button(label="📋 Salon logs", style=discord.ButtonStyle.primary, row=0)
    async def b_log(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            "Choisis le salon texte des logs :",
            view=PickLogChannelView(self.ctrl, self.gid),
            ephemeral=True,
        )

    @discord.ui.button(label="👮 Rôles staff", style=discord.ButtonStyle.secondary, row=0)
    async def b_roles(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            "Sélectionne les rôles :",
            view=PickStaffRolesView(self.ctrl, self.gid),
            ephemeral=True,
        )

    @discord.ui.button(label="➕ Entrée menu", style=discord.ButtonStyle.success, row=1)
    async def b_add_menu(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(AddCategoryModal(self.ctrl, self.gid))

    @discord.ui.button(label="🗑 Retirer entrée", style=discord.ButtonStyle.danger, row=1)
    async def b_rm_menu(self, interaction: discord.Interaction, button: discord.ui.Button):
        cats = (
            self.ctrl.config.get("guilds", {}).get(self.gid, {}).get("categories") or {}
        )
        if not cats:
            return await interaction.response.send_message(
                "Aucune entrée menu à supprimer.", ephemeral=True
            )
        await interaction.response.send_message(
            "Clé à supprimer :",
            view=RemoveCategoryView(self.ctrl, self.gid),
            ephemeral=True,
        )

    @discord.ui.button(label="🌐 Nouveau serveur (ID)", style=discord.ButtonStyle.secondary, row=2)
    async def b_guild(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(AddGuildByIdModal(self.ctrl))

    @discord.ui.button(label="🔑 Qui peut /botconfig", style=discord.ButtonStyle.secondary, row=2)
    async def b_admins(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(PanelAdminsModal(self.ctrl))

    @discord.ui.button(label="🔄 Rafraîchir le panneau", style=discord.ButtonStyle.secondary, row=3)
    async def b_refresh(self, interaction: discord.Interaction, button: discord.ui.Button):
        emb = self.build_embed()
        await interaction.response.edit_message(embed=emb, view=self)
