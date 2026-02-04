import os
import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

from chat import generate_response, generate_summary
from datetime import timedelta
from server import server_on

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix='!',
                   intents=intents)


async def build_query_with_history(message, current_content, max_messages=25, time_threshold_seconds=600, lookback_minutes=None, author_only=None):
    """
    Collect prior messages from the same channel and build a conversation-style list of messages.
    - Gathers up to `max_messages` messages before `message` (not including itself).
    - Stops collecting when a time gap between consecutive messages exceeds `time_threshold_seconds`.
    - Skips empty messages and other bots (except this bot).
    - Returns a list of tuples (role, content) in chronological order, where role is "user" or "assistant".
    """
    channel = message.channel
    history_msgs = []

    last_ts = message.created_at
    # If lookback_minutes provided, compute cutoff and iterate after that time
    cutoff = None
    if lookback_minutes is not None:
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
        if gap > time_threshold_seconds and len(history_msgs) > 0:
            break

        history_msgs.append(prev)
        last_ts = prev.created_at
        if len(history_msgs) >= max_messages:
            break

    # reverse to chronological order
    history_msgs.reverse()

    message_list = []
    for m in history_msgs:
        role = "assistant" if m.author == bot.user else "user"
        message_list.append((role, m.content.strip()))

    # append current content
    current_role = "assistant" if message.author == bot.user else "user"
    message_list.append((current_role, current_content.strip()))

    return message_list


@bot.event
async def on_message(message):
    try:
        if not message:
            message = "สวัสดี"

        if message.author == bot.user:
            return

        # Handle DMs without prefix requirement
        if isinstance(message.channel, discord.DMChannel):
            current_content = message.content.strip()
            message_list = await build_query_with_history(message, current_content)
            await message.channel.send(generate_response(message_list))

        # Handle server messages with !health prefix
        elif message.content.startswith('!health'):
            # await message.channel.send("Received a trigger")
            current_content = message.content.replace("!health", "", 1).strip()
            message_list = await build_query_with_history(message, current_content)
            await message.channel.send(generate_response(message_list))

    except Exception as e:
        error_embed = discord.Embed(
            title="Error",
            description=f"An error occurred while processing your request: {str(e)}",
            color=discord.Color.red()
        )
        await message.channel.send(embed=error_embed)


@bot.tree.command(name="summary", description="Get your personalized health summary.")
async def summary(interaction):
    # Collect user's messages for the past 24 hours (customize lookback_minutes as needed)
    lookback_minutes = 24 * 60
    channel = interaction.channel
    cutoff = discord.utils.utcnow() - timedelta(minutes=lookback_minutes)

    msgs = []
    async for m in channel.history(limit=1000, after=cutoff, oldest_first=True):
        if m.author.id == interaction.user.id or (m.author == bot.user):
            role = "assistant" if m.author == bot.user else "user"
            if getattr(m, "content", None) and m.content.strip() != "":
                msgs.append((role, m.content.strip()))

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

    embed.set_footer(text="ไม่ใช่คำวินิจฉัยทางการแพทย์ กรุณาปรึกษากับแพทย์ผู้ชำนาญการก่อนทุกครั้ง")

    await interaction.response.send_message(embed=embed)


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
