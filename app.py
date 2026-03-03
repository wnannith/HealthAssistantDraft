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
from discord.ext import commands, tasks

from chat import generate_response, generate_summary, connect_db, save_extracted_profile, ProfileStructure
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
            conn = connect_db()
            conn.execute("PRAGMA foreign_keys = ON")
            cursor = conn.cursor()

            # 1. ลบข้อมูลจากตารางลูกที่เกี่ยวข้องทั้งหมด
            cursor.execute("DELETE FROM MessageMappings WHERE user_id = ?", (self.user_id,))
            cursor.execute("DELETE FROM UserSummaryRecords WHERE user_id = ?", (self.user_id,))
            cursor.execute("DELETE FROM UserActivityRecords WHERE user_id = ?", (self.user_id,))
            cursor.execute("DELETE FROM UserBMIRecords WHERE user_id = ?", (self.user_id,))
            
            # 2. เก็บเวลาปัจจุบัน (UTC) ไว้สำหรับกั้นประวัติใน Discord
            now_iso = datetime.now(timezone.utc).isoformat()

            # 3. แทนที่จะ DELETE จาก Users ให้ทำการ UPDATE ล้างค่าเก่าและบันทึกเวลา Reset
            cursor.execute("""
                UPDATE Users 
                SET name = NULL, 
                    dob = NULL, 
                    gender = NULL, 
                    occupation = NULL, 
                    description = NULL, 
                    chronic_disease = NULL,
                    last_reset_at = ?
                WHERE user_id = ?
            """, (now_iso, self.user_id))

            # กรณีถ้าไม่เคยมี User นี้ในตาราง (อาจจะล้างตอนยังไม่มีโปรไฟล์) ให้ Insert เข้าไปใหม่
            if cursor.rowcount == 0:
                cursor.execute("INSERT INTO Users (user_id, last_reset_at) VALUES (?, ?)", 
                               (self.user_id, now_iso))
            
            conn.commit()
            conn.close()

            await interaction.response.edit_message(content="🗑️ ระบบได้ล้างข้อมูลและประวัติการสนทนาของคุณเรียบร้อยแล้ว (ข้อความก่อนหน้านี้จะถูกเพิกเฉย)", view=None)
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


async def send_response_safely(target, text: str, waiting_msg: discord.Message = None, reply_to_id: int = None):
    """ส่งข้อความและบันทึก message_id ลง Database"""
    disclaimer = "-# ไม่ใช่คำวินิจฉัยทางการแพทย์ กรุณาปรึกษากับแพทย์ผู้ชำนาญการก่อนทุกครั้ง"
    full_footer = f"\n\n{disclaimer}\n"
    
    if not text:
        if waiting_msg: 
            try: await waiting_msg.delete()
            except: pass
        return

    if waiting_msg:
        try: await waiting_msg.delete()
        except: pass

    # เตรียมข้อความ
    clean_text = text.replace(full_footer, "").replace(disclaimer, "").strip()
    chunks = [clean_text[i:i+1900] for i in range(0, len(clean_text), 1900)]
    
    if chunks:
        chunks[-1] = chunks[-1] + full_footer
    else:
        chunks = [full_footer.strip()]

    # ส่งข้อความและบันทึก ID
    sent_messages = []
    for chunk in chunks:
        if isinstance(target, discord.Interaction):
            msg = await target.followup.send(chunk)
        else:
            msg = await target.send(chunk)
        sent_messages.append(msg)

    # บันทึก Mapping ลง Database
    if reply_to_id and sent_messages:
        conn = connect_db()
        cursor = conn.cursor()
        for m in sent_messages:
            cursor.execute(
                "INSERT OR REPLACE INTO MessageMappings (message_id, user_id) VALUES (?, ?)",
                (m.id, reply_to_id)
            )
        conn.commit()
        conn.close()


async def build_query_with_history(channel, user_id=None, current_content=None, max_messages=25, same_day=False):
    messages = []
    now = discord.utils.utcnow()
    channel_prefix = "!health"
    is_dm = isinstance(channel, discord.DMChannel)
    ignored_titles = ["⚠️ ยืนยันการลบข้อมูล", "ยืนยันการลบข้อมูล", "Error", "🗑️ ลบข้อมูลเรียบร้อย"]
    
    last_reset = None
    bot_msg_ids = set()
    if user_id:
        conn = connect_db()
        cursor = conn.cursor()
        cursor.execute("SELECT message_id FROM MessageMappings WHERE user_id = ?", (user_id,))
        bot_msg_ids = {row[0] for row in cursor.fetchall()}
        
        row = conn.execute("SELECT last_reset_at FROM Users WHERE user_id = ?", (user_id,)).fetchone()
        if row and row[0]:
            last_reset = datetime.fromisoformat(row[0])

        conn.close()

    async for prev in channel.history(limit=100, before=now):
        # --- กรองข้อความที่เกิดก่อนการ Reset ---
        if last_reset and prev.created_at <= last_reset:
            # เนื่องจาก history เรียงจากใหม่ไปเก่า 
            # ถ้าเจอข้อความที่เก่ากว่าเวลา reset แล้ว ข้อความหลังจากนี้ก็เก่าหมดแน่นอน
            break

        # 1. เช็ควัน (Same Day)
        if same_day and prev.created_at.date() != now.date():
            break

        # 2. แยกจัดการระหว่าง "ข้อความจากคน" กับ "ข้อความจากบอท"
        if prev.author.bot:
            # --- กรณีเป็น BOT ---
            # ต้องเป็นบอทตัวนี้เท่านั้น และต้องมี ID อยู่ใน Mapping ของ user_id นี้
            if prev.author.id != bot.user.id or prev.id not in bot_msg_ids:
                continue
            role = "assistant"
        else:
            # --- กรณีเป็น USER ---
            # ต้องเป็นเจ้าของ user_id ที่เรียกมาเท่านั้น
            if user_id and prev.author.id != user_id:
                continue
            
            # ถ้าอยู่ใน Server (ไม่ใช่ DM) ต้องมี Prefix !health
            if not is_dm and not prev.content.startswith(channel_prefix):
                continue
            role = "user"

        # 3. จัดการเนื้อหาข้อความ (Clean content)
        raw_content = prev.content if prev.content else ""
        
        # ลบ Prefix
        if not is_dm and raw_content.startswith(channel_prefix):
            clean_content = raw_content.replace(channel_prefix, "", 1).strip()
        else:
            clean_content = raw_content.strip()
        
        # ดึง Text จาก Embed (เฉพาะของบอท)
        if prev.author.id == bot.user.id and prev.embeds:
            embed_texts = [f"{e.title}\n{e.description}" for e in prev.embeds 
                           if e.title not in ignored_titles]
            if embed_texts:
                clean_content += "\n" + "\n".join(embed_texts)

        # ลบ Disclaimer
        disclaimer = "-# ไม่ใช่คำวินิจฉัยทางการแพทย์ กรุณาปรึกษากับแพทย์ผู้ชำนาญการก่อนทุกครั้ง"
        clean_content = clean_content.replace(disclaimer, "").strip()

        if not clean_content:
            continue

        messages.append({"role": role, "content": clean_content})

        if len(messages) >= max_messages:
            break

    messages.reverse()

    if current_content:
        messages.append({"role": "user", "content": current_content.strip()})

    return messages


def cleanup_message_mappings(days_to_keep=7):
    """ลบประวัติ Mapping ที่เก่าเกินกำหนดเพื่อประหยัดพื้นที่"""
    conn = connect_db()
    cursor = conn.cursor()
    try:
        # ลบข้อมูลที่ timestamp เก่ากว่า X วัน
        cursor.execute(
            "DELETE FROM MessageMappings WHERE timestamp < datetime('now', ?)",
            (f'-{days_to_keep} days',)
        )
        deleted_count = cursor.rowcount
        conn.commit()
        if deleted_count > 0:
            print(f"🧹 Cleanup: ลบ Mapping เก่าออก {deleted_count} รายการเรียบร้อย")
    except Exception as e:
        print(f"Cleanup error: {e}")
    finally:
        conn.close()


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
        history = await build_query_with_history(message.channel, user_id=message.author.id)
        response_text, state = generate_response(history, user_id=message.author.id)

        if response_text:
            await send_response_safely(message.channel, response_text, waiting_msg=waiting_msg, reply_to_id=message.author.id)

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


@bot.tree.command(name="summary", description="สร้างสรุปประจำวัน")
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


@bot.tree.command(name="update-user", description="เขียนอัพเดต และบันทึกข้อมูลของคุณ (ไม่ตอบคำถาม)")
@app_commands.describe(info="ข้อความอธิบายอธิบาย มีอะไรใหม่อยากให้บอทบันทึกบ้าง")
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


@bot.tree.command(name="ask", description="ถามคำถามบอท")
@app_commands.describe(question="คำถามที่อยากถาม")
async def ask(interaction, question: str):
    """
    Docstring for ask
    
    :param interaction: Description
    :param question: Description
    :type question: str
    """
    history = await build_query_with_history(interaction.channel, user_id=interaction.user.id, current_content=question)
    response_text, _ = generate_response(history, user_id=interaction.user.id, topic='ask')
    await send_response_safely(interaction.channel, response_text, reply_to_id=interaction.user.id)


@bot.tree.command(name="askraw", description="[For Testing Only] ถามคำถามบอท (ไม่ใช้ RAG)")
@app_commands.describe(question="คำถามที่อยากถาม")
async def askraw(interaction, question: str):
    """
    Docstring for askraw
    
    :param interaction: Description
    :param question: Description
    :type question: str
    """
    history = await build_query_with_history(interaction.channel, user_id=interaction.user.id, current_content=question)
    response_text, _ = generate_response(history, user_id=None, use_info=False, use_rag=False, topic='ask')
    await send_response_safely(interaction.channel, response_text, reply_to_id=interaction.user.id)


@bot.tree.command(name="log", description="จดบันทึกข้อมูลสุขภาพจากอุปกรณ์ของคุณ")
@app_commands.describe(
    steps="The steps taken (step)",
    sleep_hours="Your sleep hours from last night (hr)",
    calories_burned="Calories burned (kcal)",
    avg_heart_rate="Your recorded average heart rate (bpm)",
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


# เพิ่มเข้าไปใน class บอทของคุณ หรือในไฟล์หลัก
@tasks.loop(hours=24)
async def daily_cleanup():
    cleanup_message_mappings(days_to_keep=7)
    print("Daily cleanup task completed.")


@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f'We have logged in as {bot.user}')
    if not daily_cleanup.is_running():
        daily_cleanup.start()


def main():
    load_dotenv()
    server_on()
    token = os.getenv("DISCORD_TOKEN")
    bot.run(token)


if __name__ == "__main__":
    main()
