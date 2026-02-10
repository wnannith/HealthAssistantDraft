"""
Docstring for app
"""

import os
import sqlite3
from datetime import timedelta, timezone, datetime
from dotenv import load_dotenv

import discord
from discord import app_commands
from discord.ext import commands

from chat import get_prompt, generate_response, generate_summary, save_extracted_profile, ProfileStructure
from server import server_on

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix='!',
                   intents=intents)


class ProfileConfirmView(discord.ui.View):
    """
    Docstring for ProfileConfirmView
    """
    def __init__(self, user_id, extracted_data):
        super().__init__(timeout=60) # 1 minute to click
        self.user_id = user_id
        self.data = extracted_data

    @discord.ui.button(label="Confirm & Save", style=discord.ButtonStyle.green, emoji="✅")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            # We use the saver function we built earlier
            save_extracted_profile(self.user_id, ProfileStructure(**self.data))
            await interaction.response.edit_message(content="✅ ข้อมูลของคุณถูกบันทึกเรียบร้อยแล้ว!", view=None)
        except Exception as e:
            await interaction.response.send_message(f"Error saving: {e}", ephemeral=True)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.grey)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="ยกเลิกการบันทึกข้อมูล", view=None)


class ResetConfirmView(discord.ui.View):
    def __init__(self, user_id):
        super().__init__(timeout=30)
        self.user_id = user_id

    @discord.ui.button(label="ลบข้อมูลทั้งหมด", style=discord.ButtonStyle.danger, emoji="⚠️")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("คุณไม่มีสิทธิ์กดยืนยันแทนคนอื่น", ephemeral=True)

        try:
            # Perform the database deletion
            conn = sqlite3.connect(get_prompt("databaseName"))
            conn.execute("PRAGMA foreign_keys = ON")
            cursor = conn.cursor()
            
            # Delete from both tables (Foreign key handles records if configured, but manual is safer)
            cursor.execute("DELETE FROM UserBMIRecords WHERE userID = ?", (self.user_id,))
            cursor.execute("DELETE FROM Users WHERE userID = ?", (self.user_id,))
            
            conn.commit()
            conn.close()

            await interaction.response.edit_message(content="🗑️ ข้อมูลของคุณถูกลบออกจากระบบโดยถาวรแล้ว", view=None)
        except Exception as e:
            await interaction.response.send_message(f"เกิดข้อผิดพลาด: {e}", ephemeral=True)

    @discord.ui.button(label="ยกเลิก", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="ยกเลิกการลบข้อมูล", view=None)


async def build_query_with_history(
    channel,
    user_id=None,
    current_content=None,
    max_messages=25,
    time_threshold_seconds=600,
    same_day=False
):
    """
    Unified history builder for both on_message and slash commands.
    """
    messages = []
    now = discord.utils.utcnow()
    last_ts = now
    
    # Pre-calculate cutoff for same_day
    cutoff = now.replace(hour=0, minute=0, second=0, microsecond=0) if same_day else None

    # We fetch slightly more to account for filtering
    async for prev in channel.history(limit=100, before=now, after=cutoff, oldest_first=False):
        if not prev.content or prev.content.strip() == "":
            continue

        # Filter: only user and this bot
        if prev.author.bot and prev.author != bot.user:
            continue
        if user_id and not prev.author.bot and prev.author.id != user_id:
            continue

        # Time gap check
        if not same_day:
            gap = (last_ts - prev.created_at).total_seconds()
            if gap > time_threshold_seconds and len(messages) > 0:
                break

        role = "assistant" if prev.author == bot.user else "user"
        messages.append({"role": role, "content": prev.content.strip()})
        last_ts = prev.created_at

        if len(messages) >= max_messages:
            break

    messages.reverse()
    
    # Append the current pending input if provided
    if current_content:
        messages.append({"role": "user", "content": current_content.strip()})
        
    return messages


@bot.event
async def on_message(message):
    """
    Docstring for on_message
    
    :param message: Description
    """

    channel_prefix = "!health"
    try:
        if message.author == bot.user:
            return

        if not message.content.startswith(channel_prefix):
            if not isinstance(message.channel, discord.DMChannel):
                return

        content = message.content.replace(channel_prefix, "", 1).strip()

        if not content and not isinstance(message.channel, discord.DMChannel):
            await message.channel.send("สวัสดีค่ะ! ต้องการให้ช่วยเรื่องสุขภาพด้านไหนคะ? (พิมพ์ข้อความหลัง !health ได้เลย)")
            return

        history = await build_query_with_history(message.channel, user_id=message.author.id, current_content=content)
        response_text, state = generate_response(history, user_id=message.author.id)

        if response_text:
            await message.channel.send(response_text)

        if state.get("pending_extraction"):
            pending = state["pending_extraction"]
            # Filter out nulls for the display
            display_info = "\n".join([f"**{k}**: {v}" for k, v in pending.items() if v])
            embed = discord.Embed(
                title="ยืนยันข้อมูลสุขภาพ",
                description=f"ฉันตรวจพบข้อมูลใหม่ของคุณ ต้องการให้บันทึกไว้ไหม?\n\n{display_info}",
                color=discord.Color.blue()
            )
            view = ProfileConfirmView(message.author.id, pending)
            await message.channel.send(embed=embed, view=view)

        if state.get("interrupted"):
            severe_embed = discord.Embed(
                title="คำเตือน",
                description="อาการของคุณอยู่ในขั้นรุนแรงและน่าเป็นห่วงอย่างมาก กรุณาติดต่อสายด่วน และเข้ารับการกำกับดูแลโดยเร็วที่สุด",
                color=discord.Color.red()
            )
            await message.channel.send(embed=severe_embed)
            return

        if state.get("severity_rate") >= 2:
            warning_embed = discord.Embed(
                title="ข้อควรระวัง",
                description="อาการเหล่านี้ไม่ควรปล่อยปะละเลย กรุณาเข้ารับการวินิจฉัยกับสถานพยาบาลเพื่อรับคำแนะนำ",
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
    """
    Docstring for summary
    
    :param interaction: Description
    """

    await interaction.response.defer(thinking=True)
    history = await build_query_with_history(interaction.channel, user_id=interaction.user.id, same_day=True)

    utc_now = discord.utils.utcnow()
    # Convert to UTC+7 (Bangkok time)
    tz_utc7 = timezone(timedelta(hours=7))
    now = utc_now.astimezone(tz_utc7)
    embed = discord.Embed(
            title="Summary",
            description="สรุป ณ วันที่ " + now.strftime("%d/%m/%Y %H:%M:%S"),
            timestamp=utc_now
    )

    summary_response = None
    overview, office_risk, office_summary = '----', '--', '----'

    # Generate summary (Overview, Risk)
    if history:
        summary_response = generate_summary(history, use_rag=True)
        overview = summary_response.get("overview")
        office_risk = summary_response.get("office_risk")
        office_summary = summary_response.get("office_summary")

        embed.set_author(name=interaction.user.name, icon_url=str(interaction.user.avatar))
        embed.add_field(name='Name', value='-- --', inline=False)
        embed.add_field(name='Height', value='--', inline=True)
        embed.add_field(name='Weight', value='--', inline=True)
        embed.add_field(name='Overview', value=overview, inline=False)
        embed.add_field(name='Office Syndrome', value='', inline=False)
        embed.add_field(name='Risk', value=office_risk, inline=False)
        embed.add_field(name='', value=office_summary, inline=False)
        embed.set_footer(text="ไม่ใช่คำวินิจฉัยทางการแพทย์ กรุณาปรึกษากับแพทย์ผู้ชำนาญการก่อนทุกครั้ง")

    nomsg_embed = discord.Embed(
        title="Message not found",
        description="ไม่มีข้อมูลสำหรับการสรุปผล กรุณาลองใหม่ภายหลัง"
    )

    # Send as a followup because we deferred earlier
    try:
        if not history:
            await interaction.followup.send(embed=nomsg_embed)
            return
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


@bot.tree.command(name="update-user", description="Update your personal profile with your words.")
@app_commands.describe(question="The question you want to ask...")
async def update_user(interaction, question: str):
    await interaction.response.defer(thinking=True)
    history = await build_query_with_history(interaction.channel, user_id=interaction.user.id, current_content=question)
    response_text, _ = generate_response(history, user_id=interaction.user.id)

    await interaction.followup.send(response_text)


@bot.tree.command(name="ask", description="Ask the bot a question. (Alternative to `!health` prefix in server channels)")
@app_commands.describe(question="The question you want to ask...")
async def ask(interaction, question: str):
    """
    Docstring for ask
    
    :param interaction: Description
    :param question: Description
    :type question: str
    """
    await interaction.response.defer(thinking=True)
    history = await build_query_with_history(interaction.channel, user_id=interaction.user.id, current_content=question)
    response_text, _ = generate_response(history, user_id=interaction.user.id, topic='ask')

    await interaction.followup.send(response_text)


@bot.tree.command(name="askraw", description="[For Testing Only] Ask the bot a question without RAG.")
@app_commands.describe(question="The question you want to ask...")
async def askraw(interaction, question: str):
    """
    Docstring for askraw
    
    :param interaction: Description
    :param question: Description
    :type question: str
    """
    await interaction.response.defer(thinking=True)
    history = await build_query_with_history(interaction.channel, user_id=interaction.user.id, current_content=question)
    response_text, _ = generate_response(history, user_id=None, use_info=False, use_rag=False, topic='ask')

    await interaction.followup.send(response_text)


@bot.tree.command(name="reset-user", description="[DANGER] ลบข้อมูลส่วนตัวทั้งหมดออกจากระบบ")
async def reset_user(interaction: discord.Interaction):
    # We use ephemeral=True so the warning is private to the user
    embed = discord.Embed(
        title="⚠️ ยืนยันการลบข้อมูล",
        description=(
            "การดำเนินการนี้จะลบ:\n"
            "- ประวัติส่วนตัว (ชื่อ, อาชีพ, โรคประจำตัว)\n"
            "- บันทึกน้ำหนักและส่วนสูงทั้งหมด\n\n"
            "**ข้อมูลนี้ไม่สามารถกู้คืนได้ คุณแน่ใจหรือไม่?**"
        ),
        color=discord.Color.red()
    )

    view = ResetConfirmView(interaction.user.id)
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


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
