# Status update

บันทึกความคืบหน้ารายสัปดาห์ รายการใหม่อยู่บนสุด

---

## สัปดาห์ที่ 2 — tools, CLI, traceability

**สถานะ:** เสร็จ · 77 offline tests · CI

ทำครบโจทย์ 6 ข้อ แต่ละข้อชี้ไฟล์ได้

1. **เครื่องมือ** — 8 ตัวชุดเดียวกับ opencode (`bash read write edit glob grep webfetch websearch`)
   แทนของเดิม 5 ตัว ค้นเว็บผ่าน Tavily เพราะ Brave เลิก free tier ไปแล้ว
2. **ทะเบียนเครื่องมือเป็น JSON + permission** — `config/tools.json` เป็น source of truth ของสิ่งที่โมเดลเห็น
   argument ถูกตรวจก่อนเรียกทุกครั้ง permission แบบ allow / ask / deny (rule สุดท้ายที่ match ชนะ)
   ปฏิเสธพร้อมเหตุผลที่ส่งกลับให้โมเดลได้
3. **CLI** — `run` / `tools` / `trace` คำตอบไป stdout ความคืบหน้าไป stderr, `--format json`, exit code
4. **trace database** — `sandbox/trace.db` หนึ่งแถวต่อ action พร้อมผลตัดสินของ permission และโมเดลที่ตอบ
   ตอบได้ว่า agent ขออะไรแล้ว*ถูกปฏิเสธ* ไม่ใช่แค่ทำอะไร
5. **yaml ยังเป็น config** — `workflow.yaml` + `runtime.yaml` ในรูปแบบ router ของวิชา (role → model → vendor)
6. **`dev_mem/`** — สองไฟล์นี้

ผลจริง: งานปกติ 7 แบบผ่านทั้งหมดใน 1–5 actions (1,510–10,329 tokens) รวมงานที่ต้องใช้ `edit` และ `webfetch`
การทดลองขอบเขต: `deny` หยุด `rm` ได้แม้ใส่ `--yes`, `webfetch` ไป localhost ถูกปฏิเสธ, และเมื่อสั่งให้พิมพ์ environment
agent ตอบว่าไม่มี `GROQ_API_KEY` เพราะ process ลูกไม่เห็น key — ตรวจแล้ว key ไม่อยู่ทั้งใน trace และ log

รันกับโมเดลจริงหลังทุกขั้นก่อน commit แล้วเจอปัญหาที่ test แบบ offline ไม่มีทางเจอ 3 อย่าง: reviewer
(gpt-oss) สร้าง tool call เองทั้งที่ไม่มี tool, reviewer ตัดสินผิดเพราะไม่รู้ว่า tool ถูกปฏิเสธ (แก้โดยให้เห็น
action log ของ engine: 6 → 4 actions), และ JSON แบบผสมที่ parser ไม่รับ (4 → 3 actions)

---

## สัปดาห์ที่ 1 — mini agent

**สถานะ:** เสร็จ · [github.com/NithichoteC/mini-agent](https://github.com/NithichoteC/mini-agent)

ทำ agent ที่รับงานเป็นภาษาธรรมดา เลือก action เอง ทำงานในโฟลเดอร์ของตัวเอง แล้วมี reviewer ตัดสิน

- เครื่องมือ 5 ตัว (จัดการไฟล์, รัน Python, ดึงเว็บ) ทำงานในโฟลเดอร์ของ session เท่านั้น
- ตัวหยุด 3 แบบ: งบ action, การทำ action ซ้ำ, คำตัดสิน BLOCKED — ทุกกรณีส่งต่อให้คนช่วยแล้วทำต่อได้
- agent ใช้ `qwen/qwen3.8-27b`, reviewer ใช้ `openai/gpt-oss-120b` (Groq)
- 31 offline tests รันได้โดยไม่ต้องมี API key, CI รันทุก push

ผลจริง: งานปกติ 4 แบบผ่านทั้งหมด ใช้ 1–4 actions (702–3,046 tokens)
เมื่อถอดเครื่องมือเขียนไฟล์ออก agent รายงานว่าทำไม่ได้ใน 3 actions แทนที่จะวนจนหมดงบ
