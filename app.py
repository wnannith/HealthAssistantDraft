"""
Docstring for app
"""

import os
from datetime import timedelta
from dotenv import load_dotenv

import discord
from discord import app_commands
from discord.ext import commands

from chat import generate_response, generate_summary
from server import server_on

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix='!',
                   intents=intents)


async def build_query_with_history(
        channel, message, content=None,
        max_messages=25, time_threshold_seconds=600,
        lookback_minutes=None, author_only=None, same_day=False
        ):

    """
    Collect prior messages from the same channel and build a conversation-style list of messages.
    - Gathers up to `max_messages` messages before `message` (not including itself).
    - Stops collecting when a time gap between consecutive messages exceeds `time_threshold_seconds`.
        If `same_day=True`, the function will instead collect messages from the same calendar day
        (since UTC midnight) and will not stop early based on `time_threshold_seconds`.
    - Skips empty messages and other bots (except this bot).
    - Returns a list of tuples (role, content) in chronological order, where role is "user" or "assistant".
    """
    messages = []

    last_ts = message.created_at
    # Compute cutoff. `same_day` takes precedence over `lookback_minutes` when set.
    cutoff = None
    if same_day:
        cutoff = message.created_at.replace(hour=0, minute=0, second=0, microsecond=0)
    elif lookback_minutes is not None:
        cutoff = message.created_at - timedelta(minutes=lookback_minutes)

    async for prev in channel.history(limit=200, before=message.created_at if cutoff is None else None, after=cutoff, oldest_first=False):
        # skip empty messages
        if not getattr(prev, "content", None) or prev.content.strip() == "":
            continue

        # skip other bots' messages to reduce noise
        if prev.author.bot and prev.author != bot.user:
            continue

        # if author_only is set, skip other authors
        if author_only is not None and prev.author.id != author_only:
            continue

        gap = (last_ts - prev.created_at).total_seconds()
        # if there's a large gap and we've already collected some context, stop
        # When `same_day=True` we do not cut off based on gap size.
        if not same_day and gap > time_threshold_seconds and len(messages) > 0:
            break

        prev_role = "assistant" if prev.author == bot.user else "user"
        messages.append({"role": prev_role, "content": prev.content.strip()})

        last_ts = prev.created_at

        if len(messages) >= max_messages:
            break

    # reverse to chronological order
    messages.reverse()
    message_role = "assistant" if message.author == bot.user else "user"
    if content is None:
        content = message.content
    messages.append({"role": message_role, "content": content.strip()})

    return messages


@bot.event
async def on_message(message):
    """
    Docstring for on_message
    
    :param message: Description
    """
    try:
        if message.author == bot.user:
            return

        if not message.content.startswith('!health'):
            if not isinstance(message.channel, discord.DMChannel):
                return

        content = message.content.replace("!health", "", 1).strip()
        messages = await build_query_with_history(message.channel, message, content)
        response, warning = generate_response(messages)

        if response is not None and warning:
            severe_embed = discord.Embed(
                title="ข้อควรระวัง",
                description="อาการของคุณน่าเป็นห่วงอย่างมาก กรุณาติดต่อสายด่วน และเข้ารับการกำกับดูแลโดยเร็วที่สุด",
                color=discord.Color.red()
            )
            await message.channel.send(embed=severe_embed)
            return

        if warning:
            warning_embed = discord.Embed(
                title="ข้อควรระวัง",
                description="จากการประเมินอาการเบื้องต้นจากข้อความของคุณ อาการเหล่านี้ไม่ควรปล่อยปะละเลย กรุณาเข้ารับการวินิจฉัยกับสถานพยาบาลเพื่อรับคำแนะนำ",
                color=discord.Color.yellow()
            )
            await message.channel.send(embed=warning_embed)
            return

    except Exception as e:
        error_embed = discord.Embed(
            title="Error",
            description=f"An error occurred while processing your request:\n{str(e)}",
            color=discord.Color.red()
        )
        await message.channel.send(embed=error_embed)


@bot.tree.command(name="summary", description="Get your personalized health summary.")
async def summary(interaction):
    # Defer immediately in case summary generation takes >3s
    try:
        await interaction.response.defer(thinking=True)
    except Exception:
        # If defer fails (very rare), continue and attempt to follow up later
        pass

    # Collect user's messages for the same calendar day (UTC)
    channel = interaction.channel
    now = discord.utils.utcnow()
    cutoff = now.replace(hour=0, minute=0, second=0, microsecond=0)

    msgs = []
    async for m in channel.history(limit=1000, after=cutoff, oldest_first=True):
        if m.author.id == interaction.user.id or (m.author == bot.user):
            role = "assistant" if m.author == bot.user else "user"
            if getattr(m, "content", None) and m.content.strip() != "":
                msgs.append({"role": role, "content": m.content.strip()})

    embed = discord.Embed(
            title="Summary",
            description="สรุป ณ วันที่ " + str(discord.utils.utcnow()),
            timestamp=discord.utils.utcnow()
    )

    overview, office_risk, office_summary = '----', '--', '----'

    # Generate summary (Overview, Risk)
    if msgs:
        overview, office_risk, office_summary = generate_summary(msgs, use_rag=True)

    embed.set_author(name=interaction.user.name, icon_url=str(interaction.user.avatar))
    embed.add_field(name='Name', value='-- --', inline=False)
    embed.add_field(name='Height', value='--', inline=True)
    embed.add_field(name='Weight', value='--', inline=True)
    embed.add_field(name='Overview', value=overview, inline=False)
    embed.add_field(name='Office Syndrome', value='', inline=False)
    embed.add_field(name='Risk', value=office_risk, inline=False)
    embed.add_field(name='', value=office_summary, inline=False)
    embed.set_footer(text="ไม่ใช่คำวินิจฉัยทางการแพทย์ กรุณาปรึกษากับแพทย์ผู้ชำนาญการก่อนทุกครั้ง")

    # Send as a followup because we deferred earlier
    try:
        await interaction.followup.send(embed=embed)
    except discord.NotFound:
        # Unknown interaction / original response deleted - log and skip
        print("Warning: Unknown interaction when sending summary (NotFound).")
    except Exception as e:
        error_embed = discord.Embed(
            title="Error",
            description=f"An error occurred while processing your request: {str(e)}",
            color=discord.Color.red()
        )
        await interaction.channel.send(embed=error_embed)


@bot.tree.command(name="ask", description="Ask the bot a question. (Alternative to `!health` prefix in server channels)")
@app_commands.describe(question="The question you want to ask...")
async def ask(interaction, question: str):
    try:
        await interaction.response.defer(thinking=True)
    except Exception:
        # If defer fails (very rare), continue and attempt to follow up later
        pass
    channel = interaction.channel
    msgs = []
    async for m in channel.history(limit=200, oldest_first=False):
        if m.author.id == interaction.user.id or (m.author == bot.user):
            role = "assistant" if m.author == bot.user else "user"
            if getattr(m, "content", None) and m.content.strip() != "":
                msgs.append({"role": role, "content": m.content.strip()})
    msgs.reverse()
    msgs.append({"role": "user", "content": question.strip()})
    await interaction.followup.send(generate_response(msgs))


@bot.tree.command(name="askraw", description="[Testing purposes only] Ask the bot a question without RAG.")
@app_commands.describe(question="The question you want to ask...")
async def askraw(interaction, question: str):
    try:
        await interaction.response.defer(thinking=True)
    except Exception:
        # If defer fails (very rare), continue and attempt to follow up later
        pass

    channel = interaction.channel
    msgs = []
    async for m in channel.history(limit=200, oldest_first=False):
        if m.author.id == interaction.user.id or (m.author == bot.user):
            role = "assistant" if m.author == bot.user else "user"
            if getattr(m, "content", None) and m.content.strip() != "":
                msgs.append({"role": role, "content": m.content.strip()})
    msgs.reverse()
    msgs.append({"role": "user", "content": question.strip()})
    await interaction.followup.send(generate_response(msgs, use_rag=False))


@bot.tree.command(name="reset-user", description="[DANGER] Remove your stored data.")
async def reset_user(interaction):
    pass


@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f'We have logged in as {bot.user}')


def main():
    load_dotenv()
    server_on()
    token = os.getenv("DISCORD_TOKEN")
    bot.run(token)


if __name__ == "__main__":
    main()
