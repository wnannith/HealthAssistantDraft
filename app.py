"""
Docstring for app
"""

import os
from datetime import date
import sqlite3
from datetime import timedelta, timezone, datetime
from dotenv import load_dotenv
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from chat import get_prompt, generate_response, generate_summary, connect_db, save_extracted_profile, ProfileStructure
from server import server_on


intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix='!',
                   intents=intents)


class ProfileConfirmView(discord.ui.View):
    def __init__(self, user_id, extracted_data):
        super().__init__(timeout=180)
        self.user_id = user_id
        self.data = extracted_data

    @discord.ui.button(label="Confirm & Save", style=discord.ButtonStyle.green, emoji="✅")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        # ตรวจสอบสิทธิ์ (เฉพาะเจ้าของข้อมูลเท่านั้นที่กดได้)
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("ขออภัยค่ะ ปุ่มนี้สำหรับเจ้าของข้อมูลเท่านั้น", ephemeral=True)

        try:
            # ใช้ Pydantic Model แปลงข้อมูลดิบและบันทึก
            profile_obj = ProfileStructure(**self.data)
            save_extracted_profile(self.user_id, profile_obj)
            
            # แก้ไข Embed เดิมเพื่อแจ้งสถานะสำเร็จ
            await interaction.response.edit_message(
                content=f"✅ ข้อมูลของ <@{self.user_id}> ถูกบันทึกเรียบร้อยแล้ว!", 
                embed=None, 
                view=None
            )
        except Exception as e:
            print(f"Error saving profile: {e}")
            if interaction.response.is_done():
                await interaction.followup.send(f"เกิดข้อผิดพลาดในการบันทึก: {e}", ephemeral=True)
            else:
                await interaction.response.send_message(f"เกิดข้อผิดพลาดในการบันทึก: {e}", ephemeral=True)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.grey)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("ขออภัยค่ะ ปุ่มนี้สำหรับเจ้าของข้อมูลเท่านั้น", ephemeral=True)
            
        await interaction.response.edit_message(content="ยกเลิกการบันทึกข้อมูลเรียบร้อย", embed=None, view=None)


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
            conn = connect_db()
            conn.execute("PRAGMA foreign_keys = ON")
            cursor = conn.cursor()
            
            # Delete from both tables (Foreign key handles records if configured, but manual is safer)
            cursor.execute("DELETE FROM UserSummaryRecords WHERE user_id = ?", (self.user_id,))
            cursor.execute("DELETE FROM UserActivityRecords WHERE user_id = ?", (self.user_id,))
            cursor.execute("DELETE FROM UserBMIRecords WHERE user_id = ?", (self.user_id,))
            cursor.execute("DELETE FROM Users WHERE user_id = ?", (self.user_id,))
            
            conn.commit()
            conn.close()

            await interaction.response.edit_message(content="🗑️ ข้อมูลของคุณถูกลบออกจากระบบโดยถาวรแล้ว", view=None)
        except Exception as e:
            await interaction.response.send_message(f"เกิดข้อผิดพลาด: {e}")

    @discord.ui.button(label="ยกเลิก", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="ยกเลิกการลบข้อมูล", view=None)


def save_activity_to_db(user_id, date_str, steps, sleep_hours, calories_burned, avg_heart_rate, active_minutes):
    conn = connect_db()
    cursor = conn.cursor()

    # We use COALESCE(steps, excluded.steps) to keep old data if the new input is NULL
    query = """
    INSERT INTO UserActivityRecords (user_id, date, steps, sleep_hours, calories_burned, avg_heart_rate, active_minutes)
    VALUES (?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(user_id, date) DO UPDATE SET
        steps = COALESCE(excluded.steps, steps),
        sleep_hours = COALESCE(excluded.sleep_hours, sleep_hours),
        calories_burned = COALESCE(excluded.calories_burned, calories_burned),
        avg_heart_rate = COALESCE(excluded.avg_heart_rate, avg_heart_rate),
        active_minutes = COALESCE(excluded.active_minutes, active_minutes);
    """
    cursor.execute(query, (user_id, date_str, steps, sleep_hours, calories_burned, avg_heart_rate, active_minutes))
    conn.commit()
    conn.close()


def save_bmi_to_db(user_id, date_str, weight, height):
    conn = connect_db()
    cursor = conn.cursor()
    
    query = """
    INSERT INTO UserBMIRecords (user_id, date, weight, height)
    VALUES (?, ?, ?, ?)
    ON CONFLICT(user_id, date) DO UPDATE SET
        weight = COALESCE(excluded.weight, weight),
        height = COALESCE(excluded.height, height);
    """
    cursor.execute(query, (user_id, date_str, weight, height))
    conn.commit()
    conn.close()


async def build_query_with_history(
    channel,
    user_id=None,
    current_content=None,
    max_messages=4,
    time_threshold_seconds=600,
    same_day=False
):
    messages = []
    now = discord.utils.utcnow()
    last_ts = now
    
    # หัวข้อ Embed ที่เป็นเรื่องระบบและควรข้าม
    ignored_titles = ["⚠️ ยืนยันการลบข้อมูล", "ยืนยันการลบข้อมูล", "Error", "🗑️ ลบข้อมูลเรียบร้อย"]
    disclaimer = "\n\n-# ไม่ใช่คำวินิจฉัยทางการแพทย์ กรุณาปรึกษากับแพทย์ผู้ชำนาญการก่อนทุกครั้ง\n"
    
    cutoff = now.replace(hour=0, minute=0, second=0, microsecond=0) if same_day else None

    async for prev in channel.history(limit=100, before=now, after=cutoff, oldest_first=False):
        # 1. กรอง Author ทั่วไป
        if prev.author.bot and prev.author != bot.user:
            continue
        if user_id and not prev.author.bot and prev.author.id != user_id:
            continue

        # 2. ตรวจสอบ Embed และเงื่อนไขการลบข้อมูล
        should_ignore = False
        if prev.embeds:
            for embed in prev.embeds:
                # เช็ค Title ว่าเป็นเรื่องระบบหรือไม่
                is_system_title = any(title in (embed.title or "") for title in ignored_titles)
                
                if is_system_title:
                    # ถ้าเป็น Embed ของบอท ให้เช็คว่า "เกี่ยวกับ User คนนี้หรือไม่"
                    # โดยตรวจสอบจาก Footer หรือ Author ใน Embed ที่เรามักระบุชื่อ user ไว้
                    # หรือตรวจสอบว่านี่เป็น Interaction ของ user_id นี้
                    should_ignore = True
                    break
        
        if should_ignore:
            continue

        # 3. ดึงเนื้อหา (ข้ามหากเป็นข้อความว่างหลังจากกรอง)
        full_content = prev.content if prev.content else ""
        
        if prev.author == bot.user and prev.embeds:
            embed_texts = []
            for embed in prev.embeds:
                # กรองเนื้อหาใน Embed
                if embed.title: embed_texts.append(f"Title: {embed.title}")
                if embed.description: embed_texts.append(embed.description)
                for field in embed.fields:
                    embed_texts.append(f"{field.name}: {field.value}")
            
            full_content = f"{full_content}\n" + "\n".join(embed_texts).strip()

        # ลบ Disclaimer
        full_content = full_content.replace(disclaimer, "").strip()

        if not full_content:
            continue

        # 4. Check Time Gap & Role Mapping
        if not same_day:
            gap = (last_ts - prev.created_at).total_seconds()
            if gap > time_threshold_seconds and len(messages) > 0:
                break

        role = "assistant" if prev.author == bot.user else "user"
        messages.append({"role": role, "content": full_content})
        last_ts = prev.created_at

        if len(messages) >= max_messages:
            break

    messages.reverse()
    
    if current_content:
        messages.append({"role": "user", "content": current_content.replace(disclaimer, "").strip()})
        
    return messages


async def send_response_safely(target, text: str, waiting_msg: discord.Message = None):
    """
    ส่งข้อความแบบแบ่ง Chunk และลบข้อความรอ พร้อมควบคุม Disclaimer ไม่ให้ซ้ำซ้อน
    """
    disclaimer = "-# ไม่ใช่คำวินิจฉัยทางการแพทย์ กรุณาปรึกษากับแพทย์ผู้ชำนาญการก่อนทุกครั้ง"
    full_disclaimer = f"\n\n{disclaimer}\n"

    if not text:
        if waiting_msg:
            await waiting_msg.delete()
        return

    if waiting_msg:
        try:
            await waiting_msg.delete()
        except:
            pass

    clean_text = text.replace(full_disclaimer, "").replace(disclaimer, "").strip()
    chunks = [clean_text[i:i+1900] for i in range(0, len(clean_text), 1900)]

    if chunks:
        chunks[-1] = chunks[-1] + full_disclaimer
    else:
        # กรณี clean_text ว่างเปล่า (เช่น มีแต่ disclaimer อย่างเดียว)
        chunks = [full_disclaimer.strip()]

    # 4. เริ่มส่งข้อความ
    for chunk in chunks:
        try:
            if isinstance(target, discord.Interaction):
                if target.response.is_done():
                    await target.followup.send(chunk)
                else:
                    await target.response.send_message(chunk)
            else:
                await target.send(chunk)
        except Exception as e:
            print(f"Error sending chunk: {e}")


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
            await message.channel.send("สวัสดีครับ! มีคำถาม หรือต้องการให้ช่วยเรื่องสุขภาพด้านไหนครับ? (พิมพ์ข้อความหลัง !health ได้เลย)")
            return

        waiting_msg = await message.channel.send("⏳ *กำลังประมวลผลข้อมูลของคุณ กรุณารอสักครู่...*")
        history = await build_query_with_history(message.channel, user_id=message.author.id, current_content=content)
        response_text, state = generate_response(history, user_id=message.author.id)

        if response_text:
            await send_response_safely(message.channel, response_text, waiting_msg)

        if state.get("pending_extraction"):
            pending = state["pending_extraction"]
            # Filter out nulls for the display
            display_info = "\n".join([f"**{k}**: {v}" for k, v in pending.items() if v])
            embed = discord.Embed(
                title="ยืนยันข้อมูลสุขภาพ",
                description=f"ตรวจพบข้อมูลใหม่ของคุณ ต้องการให้บันทึกไว้ไหมครับ (ข้อมูลนี้จะถูกใช้ในการตอบคำถามครั้งต่อ ๆ ไป)\n\n{display_info}",
                color=discord.Color.blue()
            )
            embed.set_author(name=message.author.name, icon_url=str(message.author.avatar))
            view = ProfileConfirmView(message.author.id, pending)
            await message.channel.send(embed=embed, view=view)

        if state.get("interrupted"):
            severe_embed = discord.Embed(
                title="คำเตือน",
                description="อาการของคุณอยู่ในขั้นรุนแรงและน่าเป็นห่วงอย่างมาก กรุณาขอความช่วยเหลือ หรือติดต่อสายด่วน และเข้ารับการกำกับดูแลโดยเร็วที่สุด",
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
        summary_response, user_info = generate_summary(history, user_id=interaction.user.id, use_rag=True)
        overview = summary_response.get("overview", '--')
        office_risk = summary_response.get("office_risk", '--')
        office_summary = summary_response.get("office_summary", '--')

        name = '-- --'
        height = '--'
        weight = '--'
        if user_info:
            name = user_info.get("name", '-- --')
            height = user_info.get("height", '--')
            weight = user_info.get("weight", '--')

        embed.set_author(name=interaction.user.name, icon_url=str(interaction.user.avatar))
        embed.add_field(name='Name', value=name, inline=False)
        embed.add_field(name='Height', value=f"{height} CM", inline=True)
        embed.add_field(name='Weight', value=f"{weight} KG", inline=True)
        embed.add_field(name='Overview', value=overview, inline=True)
        embed.add_field(name='Office Syndrome', value='', inline=True)
        embed.add_field(name='Risk', value=office_risk, inline=True)
        embed.add_field(name='', value=office_summary, inline=True)
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
@app_commands.describe(info="Your info...")
async def update_user(interaction, info: str):
    await interaction.response.defer(thinking=True)
    history = await build_query_with_history(interaction.channel, user_id=interaction.user.id, current_content=info)
    _, state = generate_response(history, user_id=interaction.user.id, topic='update')

    if state.get("pending_extraction"):
        pending = state["pending_extraction"]
        # Filter out nulls for the display
        display_info = "\n".join([f"**{k}**: {v}" for k, v in pending.items() if v])
        embed = discord.Embed(
            title="ยืนยันข้อมูลสุขภาพ",
            description=f"ตรวจพบข้อมูลใหม่ของคุณ ต้องการให้บันทึกไว้ไหมครับ (ข้อมูลนี้จะถูกใช้ในการตอบคำถามครั้งต่อ ๆ ไป)\n\n{display_info}",
            color=discord.Color.blue()
        )
        embed.set_author(name=interaction.user.name, icon_url=str(interaction.user.avatar))
        view = ProfileConfirmView(interaction.user.id, pending)
        await interaction.followup.send(embed=embed, view=view)


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
    await send_response_safely(interaction.channel, response_text)


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
    await send_response_safely(interaction.channel, response_text)


@bot.tree.command(name="log", description="Manually log your daily health stats.")
@app_commands.describe(
    steps="The steps taken (step)",
    sleep_hours="Your sleep hours from last night (hr)",
    calories_burned="Calories burned (kcal)",
    avg_heart_rate="Your recorded average heart rate",
    active_minutes="Your detected active minutes (min)", 

    weight="Your recorded weight (kg)",
    height="Your recorded height (cm)"
    )
async def log(
    interaction: discord.Interaction,
    steps: Optional[int] = None,
    sleep_hours: Optional[float] = None,
    calories_burned: Optional[float] = None,
    avg_heart_rate: Optional[float] = None,
    active_minutes: Optional[float] = None,

    weight: Optional[int] = None,
    height: Optional[int] = None
):
    user_id = interaction.user.id
    today = date.today().isoformat()
    
    # Logic to save to multiple tables based on what was provided
    try:
        activity_info = [steps, sleep_hours, calories_burned, avg_heart_rate, active_minutes]
        if any(activity_info):
            save_activity_to_db(user_id, today, steps, sleep_hours, calories_burned, avg_heart_rate, active_minutes)
            
        bmi_info = [weight, height]
        if any(bmi_info):
            save_bmi_to_db(user_id, today, weight, height)
            
        await interaction.response.send_message(
            f"✅ Data updated for {today}!", ephemeral=True
        )
    except Exception as e:
        await interaction.response.send_message(f"❌ Error: {e}", ephemeral=True)


@bot.tree.command(name="reset-user", description="[DANGER] ลบข้อมูลส่วนตัวทั้งหมดออกจากระบบ")
async def reset_user(interaction: discord.Interaction):
    # We use ephemeral=True so the warning is private to the user
    embed = discord.Embed(
        title="⚠️ ยืนยันการลบข้อมูล",
        description=(
            "การดำเนินการนี้จะลบ:\n"
            "- ประวัติส่วนตัว (ชื่อ, อาชีพ, โรคประจำตัว)\n"
            "- ข้อมูลน้ำหนัก ส่วนสูง และบันทึกกิจกรรมทั้งหมด\n\n"
            "**ข้อมูลนี้ไม่สามารถกู้คืนได้ คุณแน่ใจหรือไม่?**"
        ),
        color=discord.Color.red()
    )
    embed.set_author(name=interaction.user.name, icon_url=str(interaction.user.avatar))
    view = ResetConfirmView(interaction.user.id)
    await interaction.response.send_message(embed=embed, view=view)


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
