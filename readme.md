# บอทดิสคอร์ด ที่โหดที่สุดใน ๒๐๒๖ ใหญ่

**แก้ชื่อ `env.txt` เป็น `.env` และใส่ token ก่อน**
**ดูชื่อ model ใน `chat.py` ด้วย**

- รองรับการใช้ RAG ด้วย LangChain
- เพิ่ม slash command `/summary` (มีโครง แต่ใช้ยังไม่ได้)
- ลองใช้ code ตามคลิปข้างล่าง (ใน `tools_chanking/nih_chunking.py`) ได้ model RAG อยู่ใน `chroma/nih-chroma.db/` (ในคลิปใช้จาก https://ods.od.nih.gov/api/)

## แหล่งอ้างอิง

[RAG Agent](https://github.com/aliceheiman/YouTube/blob/main/nih-rag/) นำโมเดล RAG ในรูป chroma db ใช้ประกอบการ generate  
~~[SQL Agent](https://github.com/Mayurji/Explore-Libraries/blob/main/SQL-Agent/) เชื่อมต่อ Database เพื่อเก็บข้อมูลผู้ใช้~~
