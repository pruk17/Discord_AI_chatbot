# Discord AI Chatbot (OpenRouter)

บอท Discord ที่เชื่อมกับ AI ผ่าน **OpenRouter** เขียนด้วย **Python + discord.py**
เมื่อมีคน **@mention** บอทในเซิร์ฟเวอร์ บอทจะส่งข้อความไปถาม AI แล้วตอบกลับในห้องแชท
มีความจำถาวร (SQLite) + ระบบ facts + รองรับหลายโมเดลแบบสำรองอัตโนมัติ

---

## ไฟล์ในโปรเจกต์

| ไฟล์ | หน้าที่ |
|------|--------|
| `bot.py` | **ไฟล์หลัก (บาง)** — สร้าง client, ผูก event (@mention chat), sync คำสั่ง, รัน |
| `botcore/` | **แพ็กเกจโมดูล** (แยกตามหน้าที่ ดูตารางด้านล่าง) |
| `requirements.txt` | รายชื่อไลบรารีที่ต้องติดตั้ง |
| `.env.example` | ตัวอย่างการตั้งค่า — ก๊อปเป็น `.env` แล้วใส่ค่าจริง |
| `.gitignore` | กัน `.env`, `venv/`, `*.db` ไม่ให้ขึ้น git |
| `chat_history.db` | ไฟล์ความจำ (SQLite) — สร้างอัตโนมัติเมื่อรันครั้งแรก |
| `sounds/` | โฟลเดอร์ไฟล์เสียงสำหรับ `/join`, `/kuru` (สร้างเอง) |
| `discord-ai-bot.service` | เทมเพลต systemd สำหรับรัน 24 ชม. บน Raspberry Pi |

### โมดูลใน `botcore/`

โค้ดแยกตามหน้าที่ เพื่อขยาย/แก้ไขง่าย — `bot.py` เป็นตัวเรียกใช้ทั้งหมด

| โมดูล | หน้าที่ |
|-------|--------|
| `config.py` | โหลด `.env` + ค่าตั้งทั้งหมด (ที่เดียวที่อ่าน env) |
| `memory.py` | SQLite: chat history + facts + roster สมาชิก |
| `ai.py` | OpenRouter client + `ask_model()` (มีระบบสำรองหลายโมเดล) |
| `sounds.py` | เล่นไฟล์เสียงในช่องเสียง |
| `dcutils.py` | ตัวช่วย Discord (แบ่งข้อความยาว ฯลฯ) |
| `slash.py` | คำสั่ง `/` ทั้งหมด + ตรรกะ roll/pick/calc |

> **เพิ่มคำสั่งใหม่:** เพิ่มฟังก์ชันใน `slash.py` (ใต้ `register()`) — **อยากเพิ่มความสามารถใหม่:** สร้างโมดูลใหม่ใน `botcore/` แล้ว import ใน `bot.py`

---

## ขั้นที่ 1 — สร้างบอทใน Discord Developer Portal

1. เข้า https://discord.com/developers/applications → **New Application**
2. เมนู **Bot** → **Reset Token** → **Copy** เก็บ **Bot Token** ไว้ (ใส่ลง `.env`)
3. ในหน้า **Bot** เลื่อนลงไปที่ **Privileged Gateway Intents** แล้วกด Save Changes:
   - เปิด **MESSAGE CONTENT INTENT** (จำเป็น — ไม่งั้นบอทอ่านข้อความไม่ได้)
   - เปิด **SERVER MEMBERS INTENT** *(เฉพาะถ้าจะใช้ `/roles` `/members` หรือให้ AI รู้จักสมาชิก — ต้องตั้ง `ENABLE_MEMBERS=true` คู่กัน)*
4. เมนู **OAuth2 → URL Generator**:
   - **Scopes**: ติ๊ก `bot` **และ `applications.commands`** *(อันหลังจำเป็นสำหรับคำสั่ง `/`)*
   - **Bot Permissions**: `Send Messages`, `Read Message History` *(เพิ่ม `Connect`, `Speak` ถ้าจะใช้ช่องเสียง)*
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

## บอทตอบยังไง (แชท AI vs คำสั่ง)

บอทแยกเป็น 2 ทาง — คนละกลไกกัน:

| ทาง | ทริกเกอร์ | จัดการที่ |
|-----|----------|-----------|
| **แชท AI** | ถูก **@mention** เท่านั้น (กันไม่ให้ตอบทุกข้อความจนรก) | ฟังก์ชัน `on_message` |
| **คำสั่งยูทิลิตี้** | **slash command `/`** ที่ลงทะเบียนกับ Discord | `@client.tree.command(...)` |

> **เดิมคำสั่งเป็นแบบ `!`** (บอทอ่านข้อความเองแล้วเช็ก `startswith("!...")`) — เปลี่ยนมาเป็น **`/`** เพราะ Discord มีเมนู/autocomplete ให้ และไม่ต้องพึ่ง Message Content Intent สำหรับคำสั่ง ตอนนี้ไม่มีคำสั่ง `!` เหลือแล้ว

**อยากปรับเงื่อนไขแชท AI** → แก้ใน `on_message`:
- ตอบทุกข้อความในห้อง → ลบเงื่อนไข `if client.user not in message.mentions: return`
- ตอบเฉพาะบางห้อง → เช็ก `message.channel.id` ให้ตรงกับห้องที่ต้องการ

**อยากเพิ่มคำสั่ง `/` ใหม่** → เพิ่มฟังก์ชันใต้ `@client.tree.command(name=..., description=...)` แล้วบอทจะ sync ขึ้น Discord ให้เองตอนเริ่ม (ถ้าตั้ง `GUILD_ID` จะโผล่ทันที)

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

**คุยกับ AI:** @mention บอท แล้วพิมพ์ข้อความ เช่น `@Officer สวัสดี`
**คำสั่งยูทิลิตี้:** พิมพ์ `/` แล้ว Discord จะโชว์เมนูให้เลือก (slash command)

| วิธีใช้ | ทำอะไร | การใช้ AI |
|-------|--------|-----------|
| `@บอท <ข้อความ>` | คุยตามปกติ (จำบทสนทนา + facts) | ✅ ใช้ |
| `/help` | แสดงเมนูคำสั่งทั้งหมด | ❌ ไม่ใช้ |
| `/remember key value` | สั่งให้จำ fact (ทับของเก่า) | ❌ ไม่ใช้ |
| `/forget key` | ลบ fact | ❌ ไม่ใช้ |
| `/facts` | ดูทุกอย่างที่จำไว้ | ❌ ไม่ใช้ |
| `/roll` | ทอยลูกเต๋า (เช่น `20`, `2d6`) | ❌ ไม่ใช้ |
| `/pick` | สุ่มเลือกจากรายการ | ❌ ไม่ใช้ |
| `/calc` | เครื่องคิดเลข | ❌ ไม่ใช้ |
| `/roles` / `/members` | ดูบทบาท/สมาชิก (ต้อง `ENABLE_MEMBERS=true`) | ❌ ไม่ใช้ |
| `/whoami` | เช็กบทบาทของตัวเอง | ❌ ไม่ใช้ |
| `/join` / `/leave` | เข้า/ออกช่องเสียง + เล่นเสียงทักทาย (ต้องลง PyNaCl/FFmpeg + สิทธิ์ Connect/Speak) 🔊 | ❌ ไม่ใช้ |
| `/kuru` | เล่นเสียง `sounds/kuru.mp3` (เข้าห้องให้เองถ้ายังไม่อยู่) | ❌ ไม่ใช้ |

> **สำคัญ:** ตั้ง `GUILD_ID` ใน `.env` เพื่อให้คำสั่ง `/` โผล่ในเซิร์ฟเวอร์ **ทันที**
> ถ้าไม่ตั้ง จะเป็นการลงทะเบียนแบบ global ที่ **รอได้ถึง ~1 ชั่วโมง** กว่าจะเห็น

- คำสั่ง `/` ทำงานทันที **ไม่เปลือง token และไม่ติด 429**
- บอทยัง **แอบจำ fact เอง** ตอนคุยปกติถ้าเจอข้อมูลสำคัญ (ผ่านแท็ก `[[REMEMBER]]` ที่ซ่อนไว้)
  แต่โมเดลฟรีเล็กอาจพลาด → ใช้ `/remember` สั่งเองจะชัวร์กว่า

---

## การตั้งค่าใน `.env`

ใส่เฉพาะตัวที่อยากเปลี่ยนจากค่าเริ่มต้น (นอกจาก 2 ตัวแรกที่จำเป็นเสมอ) — ที่เหลือมี default ในโค้ดอยู่แล้ว

| ตัวแปร | หน้าที่ | ค่าเริ่มต้น |
|--------|--------|-------------|
| `DISCORD_BOT_TOKEN` | โทเคนบอท (**จำเป็น**) | — |
| `OPENROUTER_API_KEY` | คีย์ OpenRouter (**จำเป็น**) | — |
| `OPENROUTER_MODEL` | โมเดล (คั่นด้วย `,` = ลำดับสำรอง) | gemma-31b,…,openrouter/free |
| `SYSTEM_PROMPT` | บุคลิก/กฎของบอท | (ค่าในโค้ด) |
| `TEMPERATURE` | ความสุ่ม 0.5–0.7 = นิสัยนิ่ง | 0.6 |
| `MAX_TOKENS` | ความยาวคำตอบสูงสุด | 1024 |
| `HISTORY_LIMIT` | จำนวนข้อความที่จำ | 10 |
| `USER_COOLDOWN_SECONDS` | คูลดาวน์ต่อผู้ใช้ | 5 |
| `TZ_OFFSET_HOURS` | เขตเวลา (ไทย = 7) | 7 |
| `HISTORY_DB` | ไฟล์ความจำ | chat_history.db |
| `ENABLE_MEMBERS` | อ่านสมาชิก/role (ต้องเปิด intent) | false |
| `GUILD_ID` | ให้คำสั่ง `/` โผล่ทันที | (ว่าง) |
| `JOIN_SOUND` / `KURU_SOUND` | ไฟล์เสียง `/join` / `/kuru` | sounds/join.mp3, sounds/kuru.mp3 |
| `FFMPEG_PATH` | ที่อยู่ ffmpeg | ffmpeg |

---

## เสียง (voice) — ถ้าจะใช้ `/join` `/kuru`

1. ลงไลบรารีเสียง: `pip install -U "discord.py[voice]"` (ได้ davey + PyNaCl)
2. ลง **FFmpeg**: `winget install Gyan.FFmpeg` แล้ว **เปิด PowerShell ใหม่** (เช็ก `ffmpeg -version`)
3. สร้างโฟลเดอร์ `sounds/` แล้ววางไฟล์ `join.mp3` / `kuru.mp3`
4. สิทธิ์ใน Portal: เชิญบอทใหม่พร้อม `Connect` + `Speak` (ดูขั้นที่ 1)

---

## เรื่องค่าใช้จ่าย (OpenRouter)

- **โมเดลฟรี** (ลงท้าย `:free`) — ฟรี แต่จำกัด **~50 ครั้ง/วันต่อบัญชี** (รีเซ็ต 07:00 น. เวลาไทย = เที่ยงคืน UTC) พอสำหรับเล่น/ทดสอบ
- **โมเดลเสียเงิน** — จ่ายตาม token ที่ใช้จริง (เติมเครดิตล่วงหน้า ไม่ใช่รายเดือน) ไม่ติดลิมิตฟรีรายวัน

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
