import asyncio
import io
import json
import os
import re
import time

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None  # type: ignore[assignment,misc]

import discord
from discord import app_commands
from discord.ext import commands

import store

from config_ui import (
    ConfigController,
    ConfigRootView,
    can_use_bot_panel,
    ensure_guild_entry,
    ticket_client_overwrite,
)

# =========================
# CHEMINS & CONFIG FICHIERS
# =========================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if load_dotenv:
    _env_path = os.path.join(BASE_DIR, ".env")
    # override=True : le fichier .env prime sur une vieille DISCORD_TOKEN dans Windows
    # utf-8-sig : évite une clé « invisible » si le fichier est en UTF-8 avec BOM (Bloc-notes)
    load_dotenv(_env_path, encoding="utf-8-sig", override=True)
else:
    print(
        "⚠️ Module « python-dotenv » introuvable — le fichier .env ne sera pas chargé.\n"
        "   Installe-le avec : python -m pip install python-dotenv\n"
        "   (ou : pip install -r requirements.txt)"
    )
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")
TRANSCRIPTS_DIR = os.path.join(BASE_DIR, "transcripts")

store.init_db()


def load_cfg_file(path: str) -> dict:
    try:
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if not content:
                    return {}
                return json.loads(content)
    except Exception as e:
        print(f"❌ ERREUR LOAD {path}:", e)
    return {}


config: dict = load_cfg_file(CONFIG_FILE)
THEME = discord.Color.blurple()
COLOR_RELAY_USER = discord.Color.from_rgb(88, 101, 242)
COLOR_RELAY_STAFF = discord.Color.from_rgb(67, 181, 129)
cooldown: dict[str, float] = {}


def channel_name_slug(username: str, prefix: str) -> str:
    slug = "".join(
        c if c.isalnum() or c in "_-" else "-" for c in (username or "user").lower()
    )
    slug = re.sub(r"-+", "-", slug).strip("-") or "user"
    return f"{prefix}-{slug}"[:95]


def can_staff_close(member: discord.Member, cfg: dict | None) -> bool:
    if member.guild_permissions.manage_channels or member.guild_permissions.manage_guild:
        return True
    if not cfg:
        return False
    admin_roles = cfg.get("admin_roles") or []
    for rid in admin_roles:
        role = member.guild.get_role(int(rid))
        if role and role in member.roles:
            return True
    return False


async def build_transcript(channel: discord.TextChannel, limit: int = 450) -> str:
    lines = [
        f"Transcript du salon #{channel.name}",
        f"ID salon : {channel.id} | Serveur : {channel.guild.name} ({channel.guild.id})",
        "",
    ]
    async for m in channel.history(limit=limit, oldest_first=True):
        ts = m.created_at.strftime("%Y-%m-%d %H:%M:%S UTC")
        author = f"{m.author} ({m.author.id})"
        chunks: list[str] = []
        if m.content:
            chunks.append(m.content)
        for a in m.attachments:
            chunks.append(f"[fichier: {a.filename} | {a.url}]")
        for e in m.embeds:
            et = e.title or e.description or "embed"
            chunks.append(f"[embed: {et[:240]}]")
        body = " ".join(chunks) if chunks else "(sans contenu texte)"
        lines.append(f"[{ts}] {author}: {body}")
    return "\n".join(lines)


async def execute_ticket_close(
    bot: discord.Client,
    channel: discord.TextChannel,
    actor: discord.abc.User,
    *,
    interaction: discord.Interaction | None = None,
    skip_permission_check: bool = False,
) -> None:
    guild = channel.guild
    gid = str(guild.id)
    cfg = config.get("guilds", {}).get(gid)
    actor_member = guild.get_member(actor.id) if isinstance(actor, discord.User) else None

    if not skip_permission_check and isinstance(actor_member, discord.Member) and cfg:
        if not can_staff_close(actor_member, cfg):
            if interaction and interaction.guild:
                if not interaction.response.is_done():
                    await interaction.response.send_message(
                        "Tu n’as pas la permission de fermer ce ticket.",
                        ephemeral=True,
                    )
                else:
                    await interaction.followup.send(
                        "Tu n’as pas la permission de fermer ce ticket.",
                        ephemeral=True,
                    )
            return

    pair = store.get_ticket_by_channel(channel.id)
    uid = pair[0] if pair else None
    meta = pair[1] if pair else {}

    os.makedirs(TRANSCRIPTS_DIR, exist_ok=True)
    transcript = await build_transcript(channel)
    filename = f"ticket-{channel.id}-{int(time.time())}.txt"
    path = os.path.join(TRANSCRIPTS_DIR, filename)
    with open(path, "w", encoding="utf-8") as f:
        f.write(transcript)

    log_id = (cfg or {}).get("ticket", {}).get("log_channel_id")
    log_ch = guild.get_channel(int(log_id)) if log_id else None
    if isinstance(log_ch, discord.TextChannel):
        embed = discord.Embed(
            title="🗂 Ticket fermé",
            description=(
                f"Salon : `#{channel.name}`\n"
                f"Fermé par : {actor.mention}\n"
                + (f"Utilisateur ticket : <@{uid}>" if uid else "")
            ),
            color=THEME,
        )
        try:
            await log_ch.send(
                embed=embed,
                file=discord.File(path, filename=filename),
            )
        except discord.HTTPException:
            await log_ch.send(embed=embed)

    if uid:
        store.recently_closed_add(uid, gid, str(meta.get("category", "")))
        store.delete_ticket(uid)
        store.stats_inc_closed()

    # Répondre AVANT de supprimer le salon : après delete(), followup → Unknown Channel (10003)
    if interaction is not None and interaction.response.is_done():
        try:
            await interaction.followup.send("✅ Ticket fermé.", ephemeral=True)
        except discord.HTTPException:
            pass

    try:
        await channel.delete(reason="Fermeture ticket")
    except discord.Forbidden:
        if interaction and not interaction.response.is_done():
            await interaction.response.send_message(
                "Impossible de supprimer le salon (permissions Discord).",
                ephemeral=True,
            )


class CloseTicketButton(discord.ui.Button):
    def __init__(self, user_id: int, channel_id: int):
        super().__init__(
            label="❌ Fermer",
            style=discord.ButtonStyle.danger,
            custom_id=f"ticketmp_close:{channel_id}",
        )
        self.ticket_user_id = user_id
        self.channel_id = channel_id

    async def callback(self, interaction: discord.Interaction):
        ch = interaction.channel
        if not isinstance(ch, discord.TextChannel):
            return await interaction.response.send_message(
                "Ce n’est pas un salon texte.", ephemeral=True
            )
        if ch.id != self.channel_id:
            return await interaction.response.send_message(
                "Bouton invalide pour ce salon.", ephemeral=True
            )
        member = interaction.user
        if not isinstance(member, discord.Member):
            return await interaction.response.send_message(
                "Action réservée aux membres du serveur.", ephemeral=True
            )
        cfg = config.get("guilds", {}).get(str(ch.guild.id))
        if not can_staff_close(member, cfg):
            return await interaction.response.send_message(
                "Refusé : rôle / permission staff requis.", ephemeral=True
            )
        await interaction.response.defer(ephemeral=True)
        await execute_ticket_close(
            interaction.client, ch, member, interaction=interaction
        )


class TicketAdminPanel(discord.ui.View):
    def __init__(self, user_id: int, channel_id: int):
        super().__init__(timeout=None)
        self.add_item(CloseTicketButton(user_id, channel_id))


class TicketBot(commands.Bot):
    async def setup_hook(self) -> None:
        self.add_view(PanelView())
        for uid, t in store.get_tickets_dict().items():
            try:
                self.add_view(TicketAdminPanel(int(uid), int(t["channel_id"])))
            except (TypeError, ValueError):
                continue
        await self.tree.sync()


intents = discord.Intents.all()
bot = TicketBot(command_prefix="!", intents=intents)

cfg_ctrl = ConfigController(bot, config, CONFIG_FILE, load_cfg_file)


def _embed_trim(text: str, limit: int = 3900) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


async def relay_dm_to_ticket_channel(
    message: discord.Message, channel: discord.TextChannel
) -> None:
    """MP utilisateur → salon ticket (embed)."""
    desc = (
        _embed_trim(message.content.strip())
        if message.content and message.content.strip()
        else "*_(sans texte — pièces jointes ci-dessous si besoin)_*"
    )
    embed = discord.Embed(
        description=desc,
        color=COLOR_RELAY_USER,
        timestamp=message.created_at,
    )
    embed.set_author(
        name=f"{message.author.display_name}",
        icon_url=message.author.display_avatar.url,
    )
    embed.set_footer(
        text="📩 Demandeur · écrit uniquement en MP · le staff répond dans ce salon"
    )
    await channel.send(embed=embed)
    if message.attachments:
        files: list[discord.File] = []
        for a in message.attachments[:10]:
            try:
                raw = await a.read()
                files.append(
                    discord.File(io.BytesIO(raw), filename=a.filename or "fichier")
                )
            except Exception:
                await channel.send(f"📎 {a.url}")
        if files:
            await channel.send(files=files)
    if message.embeds and not message.content and not message.attachments:
        for e in message.embeds[:3]:
            try:
                await channel.send(embed=discord.Embed.from_dict(e.to_dict()))
            except Exception:
                await channel.send("*_(embed non relayé)_*")


async def relay_ticket_channel_to_dm(
    message: discord.Message, ticket_owner_id: int
) -> None:
    """Salon ticket → MP du demandeur (embed)."""
    user = await bot.fetch_user(ticket_owner_id)
    chunks: list[str] = []
    if message.content and message.content.strip():
        chunks.append(message.content.strip())
    for e in message.embeds[:3]:
        title = e.title or ""
        desc = e.description or ""
        ex = " | ".join(x for x in (title, desc) if x).strip()
        if ex:
            chunks.append(f"📎 {ex}")
    body = "\n\n".join(chunks) if chunks else "*_(message du staff)_*"
    embed = discord.Embed(
        description=_embed_trim(body),
        color=COLOR_RELAY_STAFF,
        timestamp=message.created_at,
    )
    embed.set_author(
        name=f"{message.author.display_name} · Staff",
        icon_url=message.author.display_avatar.url,
    )
    if message.guild:
        embed.set_footer(text=f"{message.guild.name} · #{message.channel.name}")
    files: list[discord.File] = []
    for a in message.attachments[:10]:
        try:
            raw = await a.read()
            files.append(
                discord.File(io.BytesIO(raw), filename=a.filename or "fichier")
            )
        except Exception:
            embed.description = _embed_trim(
                (embed.description or "") + f"\n📎 {a.url}"
            )
    try:
        if files:
            await user.send(embed=embed, files=files)
        else:
            await user.send(embed=embed)
    except discord.Forbidden:
        await message.channel.send(
            f"⚠️ Je ne peux pas MP <@{ticket_owner_id}> (DM fermés). "
            "Le membre doit autoriser les MP du serveur / du bot."
        )


@bot.event
async def on_ready():
    global config
    new = load_cfg_file(CONFIG_FILE)
    config.clear()
    config.update(new)
    print("================================")
    print("✅ BOT CONNECTÉ :", bot.user)
    print("================================")
    bot.loop.create_task(background_jobs())


@bot.event
async def on_guild_remove(guild: discord.Guild):
    gid = str(guild.id)
    tickets = store.get_tickets_by_guild(gid)
    closed = 0
    for uid, channel_id, category in tickets:
        store.recently_closed_add(uid, gid, category)
        store.stats_inc_closed()
        closed += 1
    if tickets:
        store.delete_tickets_by_guild(gid)
    store.web_queue_delete_by_guild(gid)
    print(f"🚪 Bot retiré de « {guild.name} » ({gid}) — {closed} ticket(s) fermé(s).")


async def update_bot_activity():
    """Met à jour l'activité du bot avec le nombre d'admins connectés par serveur."""
    if not bot.guilds:
        return

    admins_online = 0
    total_servers = 0

    for guild in bot.guilds:
        gid = str(guild.id)
        cfg = config.get("guilds", {}).get(gid)
        if not cfg:
            continue

        total_servers += 1
        admin_role_ids = cfg.get("admin_roles", [])

        # Compter les membres avec les rôles admin ou les permissions de gestion
        for member in guild.members:
            if member.bot:
                continue

            # Vérifier si le membre est admin par rôle
            is_admin = False
            for role_id in admin_role_ids:
                role = guild.get_role(int(role_id))
                if role and role in member.roles:
                    is_admin = True
                    break

            # Vérifier les permissions Discord
            if not is_admin:
                if member.guild_permissions.manage_channels or member.guild_permissions.manage_guild:
                    is_admin = True

            # Vérifier si l'admin est en ligne
            if is_admin and member.status in (discord.Status.online, discord.Status.idle, discord.Status.dnd):
                admins_online += 1

    # Mettre à jour l'activité du bot
    activity_text = f"{admins_online} admin(s) en ligne | {total_servers} serveur(s)"
    await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.watching, name=activity_text))


async def background_jobs():
    await bot.wait_until_ready()
    while not bot.is_closed():
        await process_web_queue()
        await process_close_queue()
        await update_bot_activity()
        await asyncio.sleep(5)


async def process_close_queue():
    for row_id, channel_id, _req in store.close_queue_list():
        ch = bot.get_channel(channel_id)
        if ch is None:
            store.delete_ticket_by_channel(channel_id)
            store.close_queue_delete(row_id)
            continue
        if not isinstance(ch, discord.TextChannel):
            store.delete_ticket_by_channel(channel_id)
            store.close_queue_delete(row_id)
            continue
        try:
            await execute_ticket_close(
                bot,
                ch,
                bot.user,
                interaction=None,
                skip_permission_check=True,
            )
        except Exception as e:
            print("❌ close_queue:", e)
        finally:
            store.close_queue_delete(row_id)


async def process_web_queue():
    for t in store.web_queue_list():
        tid = t["id"]
        try:
            guild = bot.get_guild(int(t["guild_id"]))
        except (TypeError, ValueError):
            store.web_queue_delete(tid)
            continue

        if not guild:
            continue

        cfg = config.get("guilds", {}).get(str(t["guild_id"]))
        if not cfg:
            continue

        tid_conf = cfg.get("ticket") or {}
        raw_cid = tid_conf.get("category_id")
        if raw_cid is None:
            continue
        category_parent = guild.get_channel(int(raw_cid))
        if not isinstance(category_parent, discord.CategoryChannel):
            continue

        try:
            user = await bot.fetch_user(int(t["user_id"]))
        except Exception:
            continue

        uid = str(user.id)
        tickets_map = store.get_tickets_dict()
        if uid in tickets_map:
            existing = bot.get_channel(tickets_map[uid]["channel_id"])
            if existing:
                store.web_queue_delete(tid)
                continue
            store.delete_ticket(uid)

        slug = channel_name_slug(user.name, "web")
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            guild.me: discord.PermissionOverwrite(read_messages=True),
        }
        mentions: list[str] = []
        for rid in cfg.get("admin_roles", []):
            role = guild.get_role(int(rid))
            if role:
                overwrites[role] = discord.PermissionOverwrite(read_messages=True)
                mentions.append(role.mention)

        try:
            channel = await guild.create_text_channel(
                name=f"{slug}-{tid}",
                category=category_parent,
                overwrites=overwrites,
                topic=f"Ticket web | {user} | {t.get('category', '')}",
            )
        except discord.HTTPException:
            continue

        embed = discord.Embed(
            title="🌐 Ticket Web",
            description=(
                f"👤 {user.mention}\n"
                f"📂 `{t.get('category', '-')}`\n\n"
                f"{t.get('message', '')}\n\n"
                f"_Le membre répond **uniquement en MP** avec le bot._"
            ),
            color=THEME,
        )

        await channel.send(
            content=" ".join(mentions),
            embed=embed,
            view=TicketAdminPanel(user.id, channel.id),
        )

        store.upsert_ticket(uid, channel.id, str(t["guild_id"]), t.get("category", ""), via="web")
        store.stats_inc_opened()
        store.web_queue_delete(tid)


class PanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="📩 Contacter",
        style=discord.ButtonStyle.primary,
        custom_id="ticketmp_panel_contact",
    )
    async def dm(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            await interaction.user.send("💬 Envoie ton message pour créer un ticket.")
        except discord.Forbidden:
            return await interaction.response.send_message(
                "❌ Impossible de t’envoyer un MP (DM fermés ou bloqués).",
                ephemeral=True,
            )
        await interaction.response.send_message("📩 Regarde tes MP", ephemeral=True)


@bot.tree.command(name="panel", description="Affiche le panneau support avec bouton MP")
async def panel_cmd(interaction: discord.Interaction):
    embed = discord.Embed(
        title="📩 SUPPORT",
        description="Clique pour ouvrir un ticket en message privé.",
        color=THEME,
    )
    await interaction.response.send_message(embed=embed, view=PanelView())


@bot.tree.command(name="botconfig", description="Panneau complet : serveurs, salons, rôles, menu ticket…")
async def botconfig_cmd(interaction: discord.Interaction):
    if not interaction.guild or not isinstance(interaction.user, discord.Member):
        return await interaction.response.send_message(
            "Commande à utiliser **sur un serveur**.", ephemeral=True
        )
    if not can_use_bot_panel(interaction.user, config):
        return await interaction.response.send_message(
            "🔒 Accès refusé (Administrateur Discord, propriétaire du serveur, ou ID dans "
            "`panel_admin_ids` du fichier config).",
            ephemeral=True,
        )
    gid = str(interaction.guild.id)
    ensure_guild_entry(config, gid, interaction.guild)
    cfg_ctrl.save()
    view = ConfigRootView(cfg_ctrl, gid, THEME)
    await interaction.response.send_message(
        embed=view.build_embed(), view=view, ephemeral=True
    )


@bot.tree.command(name="ticket_stats", description="Statistiques tickets (staff)")
@app_commands.default_permissions(manage_channels=True)
async def ticket_stats(interaction: discord.Interaction):
    if not interaction.guild:
        return await interaction.response.send_message(
            "Commande utilisabe sur un serveur.", ephemeral=True
        )
    stats = store.stats_get()
    open_count = len(store.get_tickets_dict())
    embed = discord.Embed(title="📊 Tickets", color=THEME)
    embed.add_field(name="Ouverts (suivis)", value=str(open_count), inline=True)
    embed.add_field(name="Ouverts (total bot)", value=str(stats["opened"]), inline=True)
    embed.add_field(name="Fermés (total bot)", value=str(stats["closed"]), inline=True)
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="admin_info", description="Informations sur tous les serveurs et admins (réservé)")
async def admin_info_cmd(interaction: discord.Interaction):
    # Réservé à l'utilisateur 1112038418629808148
    if str(interaction.user.id) != "1112038418629808148":
        return await interaction.response.send_message(
            "❌ Cette commande est réservée.", ephemeral=True
        )

    embed = discord.Embed(title="🔐 Informations Serveurs & Admins", color=THEME)
    embed.set_thumbnail(url=bot.user.display_avatar.url if bot.user else None)

    total_admins = 0
    total_admins_online = 0
    total_members = 0
    total_tickets = len(store.get_tickets_dict())

    for guild in bot.guilds:
        gid = str(guild.id)
        cfg = config.get("guilds", {}).get(gid)
        if not cfg:
            continue

        server_name = cfg.get("name", guild.name)
        admin_role_ids = cfg.get("admin_roles", [])

        # Compter les admins et membres
        admins = []
        admins_online = []
        member_count = 0

        for member in guild.members:
            if member.bot:
                continue

            member_count += 1

            # Vérifier si le membre est admin
            is_admin = False
            for role_id in admin_role_ids:
                role = guild.get_role(int(role_id))
                if role and role in member.roles:
                    is_admin = True
                    break

            if not is_admin:
                if member.guild_permissions.manage_channels or member.guild_permissions.manage_guild:
                    is_admin = True

            if is_admin:
                admins.append(member)
                if member.status in (discord.Status.online, discord.Status.idle, discord.Status.dnd):
                    admins_online.append(member)

        total_admins += len(admins)
        total_admins_online += len(admins_online)
        total_members += member_count

        # Ajouter les infos du serveur à l'embed
        if len(admins) > 0:
            admin_list = "\n".join([f"• {a.name} ({'🟢' if a.status == discord.Status.online else '🔴'})" for a in admins[:5]])
            if len(admins) > 5:
                admin_list += f"\n... et {len(admins) - 5} autres"
        else:
            admin_list = "Aucun admin configuré"

        embed.add_field(
            name=f"📌 {server_name}",
            value=f"**Membres:** {member_count}\n**Admins:** {len(admins)} ({len(admins_online)} en ligne)\n**Admins:**\n{admin_list}",
            inline=False
        )

    # Résumé global
    embed.add_field(
        name="📊 Résumé Global",
        value=f"**Serveurs:** {len(bot.guilds)}\n**Membres totaux:** {total_members}\n**Admins totaux:** {total_admins} ({total_admins_online} en ligne)\n**Tickets ouverts:** {total_tickets}",
        inline=False
    )

    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="ticket_close", description="Ferme le ticket du salon actuel")
@app_commands.default_permissions(manage_channels=True)
async def ticket_close_cmd(interaction: discord.Interaction):
    ch = interaction.channel
    if not isinstance(ch, discord.TextChannel) or not interaction.guild:
        return await interaction.response.send_message(
            "Utilise cette commande dans un salon du serveur.", ephemeral=True
        )
    if store.get_ticket_by_channel(ch.id) is None:
        return await interaction.response.send_message(
            "Ce salon n’est pas un ticket suivi.", ephemeral=True
        )
    member = interaction.user
    if not isinstance(member, discord.Member):
        return await interaction.response.send_message("Membre introuvable.", ephemeral=True)
    cfg = config.get("guilds", {}).get(str(interaction.guild.id))
    if not can_staff_close(member, cfg):
        return await interaction.response.send_message("Permission refusée.", ephemeral=True)

    await interaction.response.defer(ephemeral=True)
    await execute_ticket_close(bot, ch, member, interaction=interaction)


@bot.tree.command(name="ticket_reopen", description="Rouvre un ticket pour un membre (catégorie auto si possible)")
@app_commands.default_permissions(manage_channels=True)
@app_commands.describe(
    membre="Utilisateur",
    categorie="Clé de catégorie (support, achat, …). Vide = dernière fermeture.",
)
async def ticket_reopen_cmd(
    interaction: discord.Interaction,
    membre: discord.Member,
    categorie: str | None = None,
):
    if not interaction.guild:
        return await interaction.response.send_message("Serveur uniquement.", ephemeral=True)
    gid = str(interaction.guild.id)
    cfg = config.get("guilds", {}).get(gid)
    if not cfg:
        return await interaction.response.send_message("Serveur non configuré.", ephemeral=True)

    uid = str(membre.id)
    if uid in store.get_tickets_dict():
        return await interaction.response.send_message(
            "Cet utilisateur a déjà un ticket ouvert.", ephemeral=True
        )

    cat = (categorie or "").strip() or store.recently_closed_last_category(uid, gid)
    if not cat:
        return await interaction.response.send_message(
            "Aucune catégorie récente : précise `categorie` (clé exacte).",
            ephemeral=True,
        )
    if cat not in (cfg.get("categories") or {}):
        return await interaction.response.send_message(
            f"Catégorie inconnue : `{cat}`.",
            ephemeral=True,
        )

    guild = interaction.guild
    tid_conf = cfg.get("ticket") or {}
    raw_cid = tid_conf.get("category_id")
    if raw_cid is None:
        return await interaction.response.send_message(
            "Clé `ticket.category_id` absente dans config.json.",
            ephemeral=True,
        )
    category_parent = guild.get_channel(int(raw_cid))
    if not isinstance(category_parent, discord.CategoryChannel):
        return await interaction.response.send_message(
            "Catégorie Discord introuvable (vérifie `category_id`).",
            ephemeral=True,
        )

    overwrites = {
        guild.default_role: discord.PermissionOverwrite(read_messages=False),
        guild.me: discord.PermissionOverwrite(read_messages=True),
    }
    mentions: list[str] = []
    for rid in cfg.get("admin_roles", []):
        role = guild.get_role(int(rid))
        if role:
            overwrites[role] = discord.PermissionOverwrite(read_messages=True)
            mentions.append(role.mention)

    safe_name = f"{channel_name_slug(membre.name, 'ticket')}-{int(time.time())}"
    channel = await guild.create_text_channel(
        name=safe_name,
        category=category_parent,
        overwrites=overwrites,
        topic=f"Ticket de {membre} | {cat} (rouvert)",
    )

    store.upsert_ticket(uid, channel.id, gid, cat, via="discord_reopen")
    store.stats_inc_opened()

    embed = discord.Embed(
        title="🎫 Ticket rouvert",
        description=(
            f"👤 {membre.mention}\n📂 Catégorie : `{cat}`\n\n"
            f"💬 Le membre utilise les **MP** pour répondre ; toi tu écris dans ce salon."
        ),
        color=THEME,
    )
    embed.set_thumbnail(url=membre.display_avatar.url)
    await channel.send(
        content=" ".join(mentions),
        embed=embed,
        view=TicketAdminPanel(membre.id, channel.id),
    )

    await interaction.response.send_message(
        "✅ Ticket rouvert ! Réponds en MP au bot pour communiquer avec le staff.",
        ephemeral=True
    )


class CategorySelect(discord.ui.View):
    def __init__(self, user: discord.User, guild_id: str):
        super().__init__(timeout=120)
        self.user = user
        self.guild_id = str(guild_id)

        guild_conf = config.get("guilds", {}).get(self.guild_id, {})
        categories = guild_conf.get("categories", {})
        if not categories:
            print("❌ Aucune catégorie trouvée pour", self.guild_id)
            return
        for key, data in categories.items():
            self.add_item(CategoryButton(self, key, data))

    async def create_ticket(self, interaction: discord.Interaction, category: str):
        uid = str(self.user.id)
        tickets_map = store.get_tickets_dict()

        guild = interaction.guild or bot.get_guild(int(self.guild_id))
        if not guild:
            return await interaction.response.send_message(
                "❌ Serveur introuvable : vérifie que le bot est **invité** sur ce serveur.",
                ephemeral=True,
            )

        if uid in tickets_map:
            old_id = tickets_map[uid].get("channel_id")
            old_channel = guild.get_channel(old_id)
            if old_channel:
                return await interaction.response.send_message(
                    "❌ Tu as déjà un ticket ouvert", ephemeral=True
                )
            store.delete_ticket(uid)

        cfg = config.get("guilds", {}).get(self.guild_id)
        if not cfg:
            return await interaction.response.send_message(
                "❌ Config introuvable", ephemeral=True
            )

        tid_conf = cfg.get("ticket") or {}
        raw_cid = tid_conf.get("category_id")
        if raw_cid is None:
            return await interaction.response.send_message(
                "❌ `ticket.category_id` manquant dans la config.",
                ephemeral=True,
            )
        category_parent = guild.get_channel(int(raw_cid))
        if not isinstance(category_parent, discord.CategoryChannel):
            return await interaction.response.send_message(
                "❌ Catégorie Discord invalide (vérifie `category_id` dans config).",
                ephemeral=True,
            )

        ticket_subject = guild.get_member(int(uid))
        if ticket_subject is None:
            try:
                ticket_subject = await guild.fetch_member(int(uid))
            except discord.NotFound:
                return await interaction.response.send_message(
                    "❌ Tu dois être **sur le serveur** pour ouvrir un ticket depuis les MP.",
                    ephemeral=True,
                )
            except discord.HTTPException:
                return await interaction.response.send_message(
                    "❌ Le bot ne peut pas lire les membres du serveur. "
                    "Active **Privileged Gateway Intent → Server Members Intent** pour ton bot "
                    "(Discord Developer Portal → Bot).",
                    ephemeral=True,
                )

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            ticket_subject: ticket_client_overwrite(),
            guild.me: discord.PermissionOverwrite(read_messages=True),
        }

        mentions: list[str] = []
        for rid in cfg.get("admin_roles", []):
            role = guild.get_role(int(rid))
            if role:
                overwrites[role] = discord.PermissionOverwrite(read_messages=True)
                mentions.append(role.mention)

        safe_name = f"{channel_name_slug(ticket_subject.name, 'ticket')}-{int(time.time())}"
        try:
            channel = await guild.create_text_channel(
                name=safe_name,
                category=category_parent,
                overwrites=overwrites,
                topic=f"Ticket de {ticket_subject} | {category}",
            )
        except discord.HTTPException as e:
            return await interaction.response.send_message(
                f"❌ Impossible de créer le salon : {e}", ephemeral=True
            )

        store.upsert_ticket(uid, channel.id, self.guild_id, category, via="discord")
        store.stats_inc_opened()

        embed = discord.Embed(
            title="🎫 Ticket ouvert",
            description=(
                f"👤 {ticket_subject.mention}\n"
                f"📂 Catégorie : `{category}`\n\n"
                f"💬 Le membre écrit **uniquement en MP** avec le bot — ses messages apparaissent ici.\n"
                f"Réponds dans ce salon : il recevra une **copie stylée en MP**."
            ),
            color=discord.Color.blurple(),
        )
        embed.set_thumbnail(url=ticket_subject.display_avatar.url)
        embed.set_footer(text=f"ID utilisateur: {ticket_subject.id}")

        await channel.send(
            content=" ".join(mentions),
            embed=embed,
            view=TicketAdminPanel(ticket_subject.id, channel.id),
        )

        await interaction.response.send_message(
            "✅ Ticket créé ! Réponds en MP au bot pour communiquer avec le staff.",
            ephemeral=True
        )


class CategoryButton(discord.ui.Button):
    def __init__(self, view_ref: CategorySelect, key: str, data: dict):
        kw: dict = {
            "label": (data.get("label") or key)[:80],
            "style": discord.ButtonStyle.primary,
        }
        em = data.get("emoji")
        if em:
            kw["emoji"] = em
        super().__init__(**kw)
        self.view_ref = view_ref
        self.key = key

    async def callback(self, interaction: discord.Interaction):
        await self.view_ref.create_ticket(interaction, self.key)


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    uid = str(message.author.id)

    # Staff → demandeur : salon ticket vers MP
    if isinstance(message.channel, discord.TextChannel):
        pair = store.get_ticket_by_channel(message.channel.id)
        if pair:
            owner_uid, _meta = pair
            # Relayer TOUJOURS les messages du salon vers le MP de l'utilisateur
            # (l'utilisateur n'a plus accès au salon, donc seuls les admins peuvent écrire)
            try:
                await relay_ticket_channel_to_dm(message, int(owner_uid))
            except Exception as e:
                print("❌ relay salon→MP:", e)
            await bot.process_commands(message)
            return

    if isinstance(message.channel, discord.DMChannel):
        tickets_map = store.get_tickets_dict()
        if uid in tickets_map:
            channel = bot.get_channel(tickets_map[uid]["channel_id"])
            if isinstance(channel, discord.TextChannel):
                try:
                    await relay_dm_to_ticket_channel(message, channel)
                except Exception as e:
                    print("❌ relay MP→salon:", e)
        else:
            now = time.time()
            cooldown_sec = 30
            if config.get("guilds"):
                try:
                    gid_any = next(iter(config["guilds"].values()))
                    cooldown_sec = int(
                        gid_any.get("ticket", {}).get("cooldown_seconds", 30)
                    )
                except Exception:
                    pass

            if now - cooldown.get(uid, 0) > cooldown_sec:
                cooldown[uid] = now
                embed = discord.Embed(
                    title="📩 SUPPORT",
                    description="Choisis un serveur pour ouvrir un ticket.",
                    color=THEME,
                )
                await message.channel.send(embed=embed, view=GuildSelect(message.author))

    await bot.process_commands(message)


class GuildSelect(discord.ui.View):
    def __init__(self, user: discord.User):
        super().__init__(timeout=60)
        self.user = user
        guilds = config.get("guilds", {})
        options: list[discord.SelectOption] = []
        for gid, data in guilds.items():
            g = bot.get_guild(int(gid))
            name = data.get("name", "Serveur")
            emoji = "🟢" if g else "⚪"
            options.append(
                discord.SelectOption(label=name[:100], value=str(gid), emoji=emoji)
            )
        if not options:
            options = [
                discord.SelectOption(
                    label="Aucun serveur disponible", value="none", emoji="❌"
                )
            ]
        self.select = discord.ui.Select(
            placeholder="📂 Choisis un serveur",
            options=options,
            disabled=(len(guilds) == 0),
        )
        self.select.callback = self.callback
        self.add_item(self.select)

    async def callback(self, interaction: discord.Interaction):
        gid = self.select.values[0]
        if gid == "none":
            return await interaction.response.send_message(
                "❌ Aucun serveur configuré", ephemeral=True
            )
        guild = bot.get_guild(int(gid))
        cfg = config.get("guilds", {}).get(gid, {})
        embed = discord.Embed(
            title="📩 SUPPORT SYSTEM",
            description=(
                "👉 Choisis une catégorie pour ouvrir un ticket\n\n"
                "⚡ Support rapide\n🛡️ Staff disponible"
            ),
            color=discord.Color.blurple(),
        )
        if bot.user and bot.user.avatar:
            embed.set_thumbnail(url=bot.user.avatar.url)
        embed.set_footer(text=guild.name if guild else "Serveur inconnu")
        await interaction.response.send_message(
            embed=embed,
            view=CategorySelect(self.user, gid),
            ephemeral=True,
        )


def _discord_token_from_env() -> str:
    # Recharge une fois au démarrage pour prendre les derniers changements du fichier .env
    if load_dotenv:
        load_dotenv(
            os.path.join(BASE_DIR, ".env"),
            encoding="utf-8-sig",
            override=True,
        )
    raw = os.getenv("DISCORD_TOKEN") or ""
    # Nettoyage courant sous Windows (.env avec BOM, espaces, guillemets)
    token = raw.strip().strip('"').strip("'").replace("\ufeff", "")
    token = "".join(token.split())  # enlève tous les espaces au milieu (copier-coller cassé)
    if not token:
        raise SystemExit(
            "DISCORD_TOKEN est vide ou absent.\n\n"
            "Vérifie :\n"
            "  • Un fichier .env dans le même dossier que bot.py (TicketMPbot\\.env)\n"
            "  • Une ligne du type : DISCORD_TOKEN=ton_token_sans_guillemets\n"
            "  • python-dotenv installé : python -m pip install python-dotenv\n"
            "  • Ou définis DISCORD_TOKEN dans les variables d’environnement Windows.\n"
        )
    bogus = {"cacher", "xxx", "token", "changeme", "your_token_here", "paste_here"}
    if token.lower() in bogus:
        raise SystemExit(
            "DISCORD_TOKEN est encore un placeholder (ex. « cacher »).\n"
            "Remplace-le par le vrai token du bot : Discord Developer Portal → ton application → Bot → Reset Token / copier.\n"
        )
    # Un token bot classique contient deux points et fait ~68–72 caractères ; les erreurs « Improper token » viennent souvent d’un mauvais copier-coller.
    if token.count(".") != 2:
        print(
            "⚠️ Attention : un token bot Discord ressemble en général à trois segments séparés par des points.\n"
            "   Si tu as mis le Client Secret OAuth à la place du Bot Token, Discord renverra 401 Unauthorized.\n"
        )
    return token


if __name__ == "__main__":
    bot.run(_discord_token_from_env())
