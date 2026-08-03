# Discord AI Chatbot (OpenRouter)

บอท Discord ที่เชื่อมกับ AI ผ่าน **OpenRouter** เขียนด้วย **Python + discord.py**
เมื่อมีคน **@mention** บอทในเซิร์ฟเวอร์ บอทจะส่งข้อความไปถาม AI แล้วตอบกลับในห้องแชท
มีความจำถาวร (SQLite) + ระบบ facts + รองรับหลายโมเดลแบบสำรองอัตโนมัติ

---

## ไฟล์ในโปรเจกต์

| ไฟล์ | หน้าที่ |
|------|--------|
| `bot.py` | โค้ดหลักของบอท (ดักอีเวนต์ → เรียก AI ผ่าน OpenRouter → ตอบกลับ) |
| `requirements.txt` | รายชื่อไลบรารีที่ต้องติดตั้ง |
| `.env.example` | ตัวอย่างการตั้งค่า — ก๊อปเป็น `.env` แล้วใส่ค่าจริง |
| `.gitignore` | กัน `.env`, `venv/`, `*.db` ไม่ให้ขึ้น git |
| `chat_history.db` | ไฟล์ความจำ (SQLite) — สร้างอัตโนมัติเมื่อรันครั้งแรก |
| `discord-ai-bot.service` | เทมเพลต systemd สำหรับรัน 24 ชม. บน Raspberry Pi |

---

## ขั้นที่ 1 — สร้างบอทใน Discord Developer Portal

1. เข้า https://discord.com/developers/applications → **New Application**
2. เมนู **Bot** → **Reset Token** → **Copy** เก็บ **Bot Token** ไว้ (ใส่ลง `.env`)
3. ในหน้า **Bot** เลื่อนลงไปที่ **Privileged Gateway Intents** →
   เปิด **MESSAGE CONTENT INTENT** ให้เป็นสีเขียว
   *(ไม่เปิด = บอทอ่านข้อความไม่ได้ จะตอบไม่ได้เลย)*
4. เมนู **OAuth2 → URL Generator**:
   - **Scopes**: ติ๊ก `bot`
   - **Bot Permissions**: ติ๊ก `Send Messages`, `Read Message History`
   - ก๊อป URL ด้านล่างไปเปิดในเบราว์เซอร์ → เลือกเซิร์ฟเวอร์ → เชิญบอทเข้า

## ขั้นที่ 2 — เอา API Key ของ OpenRouter

1. เข้า https://openrouter.ai/ → ล็อกอิน (Google/GitHub ได้)
2. ไปที่ **Keys → Create Key** → ตั้งชื่อ → **Create**
3. ก๊อปคีย์ (ขึ้นต้นด้วย `sk-or-v1-...`) ไปใส่ใน `.env` ช่อง `OPENROUTER_API_KEY`
4. เลือกโมเดลที่ https://openrouter.ai/models — กรอง **"Free"** เพื่อหาโมเดลฟรี

**ใส่ได้หลายโมเดล คั่นด้วยจุลภาค** ในช่อง `OPENROUTER_MODEL` — บอทจะลองทีละตัวจากซ้ายไปขวา
ถ้าตัวไหนคิวเต็ม (429) จะ **เด้งไปตัวถัดไปอัตโนมัติ** ค่าเริ่มต้น:

```
OPENROUTER_MODEL=google/gemma-4-31b-it:free,google/gemma-4-26b-a4b-it:free,openai/gpt-oss-20b:free,openrouter/free
```

ลำดับ: Gemma 31B (ไทยดีสุด) → Gemma 26B → gpt-oss-20b → `openrouter/free` (สุ่มโมเดลฟรีอื่นเป็นตัวสำรองสุดท้าย)

---

## ขั้นที่ 3 — รันทดสอบบน Windows

เปิด **PowerShell** ในโฟลเดอร์โปรเจกต์ (`F:\VS_code_homeplay\AI_chatbot`)

**1. สร้าง virtual environment (venv)** — แยกไลบรารีของโปรเจกต์นี้ออกจากตัวเครื่อง:

```powershell
python -m venv venv
```

**2. เปิดใช้งาน venv:**

```powershell
.\venv\Scripts\Activate.ps1
```

> ถ้าติด error เรื่อง execution policy ให้รันครั้งเดียว:
> `Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned`
> แล้ว activate ใหม่ (เห็น `(venv)` นำหน้าบรรทัด = สำเร็จ)

**3. ติดตั้งไลบรารี:**

```powershell
pip install -r requirements.txt
```

**4. สร้างไฟล์ตั้งค่า** — ก๊อป `.env.example` เป็น `.env` แล้วเปิดใส่ค่าจริง:

```powershell
Copy-Item .env.example .env
```

**5. รันบอท:**

```powershell
python bot.py
```

ถ้าขึ้น `Logged in as ...` และ `Bot is online` แปลว่าใช้ได้แล้ว
ไปที่ห้องแชทในเซิร์ฟเวอร์ พิมพ์ `@ชื่อบอท สวัสดี` แล้วรอบอทตอบ

> ปิดบอท: กด **Ctrl + C** ใน PowerShell (บอทจะออฟไลน์ทันที)

---

## เงื่อนไขการตอบ

ตอนนี้บอทตอบเฉพาะเมื่อถูก **@mention** เท่านั้น (กันไม่ให้ตอบทุกข้อความจนรก)
ถ้าอยากปรับ ให้แก้ในฟังก์ชัน `on_message` ของ `bot.py`:

- **อยากให้ตอบทุกข้อความในห้อง** → ลบเงื่อนไข `if client.user not in message.mentions: return`
- **อยากให้ตอบเฉพาะบางห้อง** → เช็ก `message.channel.id` ให้ตรงกับ ID ห้องที่ต้องการ
- **อยากให้ตอบเมื่อขึ้นต้นด้วยคำสั่ง** เช่น `!ai` → เปลี่ยนไปเช็ก `message.content.startswith("!ai")`

---

## ความจำของบอท

บอทเก็บความจำลงไฟล์ **SQLite** (`chat_history.db`) → **จำข้ามการรีสตาร์ท** และ **รวมทุกห้อง/ทุกเซิร์ฟเวอร์เป็นชุดเดียว** มีความจำ 2 ชั้น:

| ชั้น | เก็บอะไร | ขอบเขต |
|------|---------|--------|
| Chat history | บทสนทนาล่าสุด (พร้อมชื่อคนพูด) | แค่ `HISTORY_LIMIT` ข้อความล่าสุด |
| Facts | ข้อเท็จจริง `key = value` | ส่งให้โมเดล **ทุกครั้ง ไม่มีหลุด** |

- เพิ่ม `HISTORY_LIMIT` → จำบทสนทนาได้ยาวขึ้น **แต่ใช้ token มากขึ้น**
- Facts จะ **ทับของเก่า** เมื่อ key ซ้ำ (เช่น เปลี่ยนชื่อ A → B)
- **อยากล้างความจำ:** ปิดบอท → ลบไฟล์ `chat_history.db` → เปิดใหม่

---

## คำสั่งของบอท

พิมพ์โดย **@mention บอทก่อนเสมอ** เช่น `@Officer !help`

| คำสั่ง | ทำอะไร | ใช้ AI ไหม |
|-------|--------|-----------|
| `<ข้อความ>` | คุยตามปกติ (จำบทสนทนา + facts) | ✅ ใช้ |
| `!help` | แสดงเมนูคำสั่งทั้งหมด | ❌ ไม่ใช้ |
| `!remember หัวข้อ = ค่า` | สั่งให้จำ fact (ทับของเก่า) | ❌ ไม่ใช้ |
| `!forget หัวข้อ` | ลบ fact | ❌ ไม่ใช้ |
| `!facts` | ดูทุกอย่างที่จำไว้ | ❌ ไม่ใช้ |

- คำสั่งที่ขึ้นต้นด้วย `!` ทำงานทันที **ไม่เปลือง token และไม่ติด 429**
- บอทยัง **แอบจำ fact เอง** ตอนคุยปกติถ้าเจอข้อมูลสำคัญ (ผ่านแท็ก `[[REMEMBER]]` ที่ซ่อนไว้)
  แต่โมเดลฟรีเล็กอาจพลาด → ใช้ `!remember` สั่งเองจะชัวร์กว่า

---

## ⚠️ เรื่องค่าใช้จ่าย (OpenRouter)

- **โมเดลฟรี** (ลงท้าย `:free`) — ใช้ฟรี แต่มีลิมิตจำนวนครั้งต่อวัน พอสำหรับเล่น/ทดสอบ
- **โมเดลเสียเงิน** — จ่ายตาม token ที่ใช้จริง (เติมเครดิตล่วงหน้า ไม่ใช่รายเดือน)

กลไกกันเงิน/กันสแปมที่ใส่ไว้ในโค้ดแล้ว:

| ตัวช่วย | ตั้งค่าใน `.env` | ผล |
|--------|------------------|-----|
| ตอบเฉพาะตอนถูก mention | (โค้ด) | ไม่ยิง API ทุกข้อความ |
| คูลดาวน์ต่อผู้ใช้ | `USER_COOLDOWN_SECONDS` | กันสแปมรัวๆ |
| จำกัดความยาวคำตอบ | `MAX_TOKENS` | คำตอบสั้นลง |
| จำกัดประวัติ | `HISTORY_LIMIT` | input token น้อยลง |
| เลือกโมเดล | `OPENROUTER_MODEL` | ใช้โมเดล `:free` = ไม่เสียเงิน |

**เคล็ดลับ:** ช่วงทดสอบใช้โมเดล `:free` ไปก่อน บอทจะ log จำนวน token ทุกครั้งที่ตอบ
ดูยอดใช้งาน/ตั้งเพดานงบได้ที่ OpenRouter → **Settings / Credits**

---

## ขั้นที่ 4 (อนาคต) — ย้ายขึ้น Raspberry Pi 5 ให้ออนไลน์ 24 ชม.

โค้ด `.py` ก๊อปไปวางได้เลย ไม่ต้องแก้ (Python ข้ามแพลตฟอร์ม, pip จัดการ ARM ให้เอง)

**บน Pi (Ubuntu 24):**

```bash
sudo apt update && sudo apt install -y python3 python3-venv git
cd ~/AI_chatbot                       # โฟลเดอร์โปรเจกต์บน Pi
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env                  # แล้วแก้ .env ใส่ token/key ใหม่
nano .env
```

**ทดสอบรันมือก่อน:** `python bot.py` — ถ้าออนไลน์โอเค กด Ctrl+C แล้วตั้ง systemd:

```bash
# แก้ path/User ในไฟล์ให้ตรงกับ Pi ก่อน (ค่าเริ่มต้นใช้ user "pi" และ /home/pi/AI_chatbot)
sudo cp discord-ai-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now discord-ai-bot     # เปิด + ให้รันอัตโนมัติเมื่อบูตเครื่อง
```

คำสั่งที่ใช้บ่อย:

```bash
systemctl status discord-ai-bot        # เช็กสถานะ
journalctl -u discord-ai-bot -f        # ดู log สดๆ
sudo systemctl restart discord-ai-bot  # รีสตาร์ทหลังแก้โค้ด/.env
sudo systemctl stop discord-ai-bot     # หยุด
```

ตั้ง `enable` ไว้แล้ว = เปิดเครื่อง Pi เมื่อไหร่บอทก็ขึ้นเองอัตโนมัติ = **ออนไลน์ตลอด** ✅
