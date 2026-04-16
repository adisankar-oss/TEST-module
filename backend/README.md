# summer-internship-project-2-AI-AVATAR-
# team members:
  jonnet shaji
  gourilekshmi
  karthik rajeev
  adi sankar 
# this project consists of 7 modules
  1. Session Orchestrator
  2. Meeting Bot
  3. Speech Engine
  4. Avatar Engine
  5. Question Intelligence Engine
  6. Answer Evaluator
  7. Interview Report Generator

# module 1 : SESSION ORCHESTRATOR

M1 is the **central controller** that:

- Receives a session creation request from the recruiter dashboard
- Loads the job profile and candidate data
- Runs a pre-flight health check on all 6 modules (M2–M7)
- Controls the sequence and timing of every action during the interview
- Maintains interview state persistently in Redis
- Publishes events to the Kafka bus for real-time monitoring
- Triggers report generation when the interview ends

# module 2: Meeting Bot


M2 acts as the **physical presence** of the AI system inside the video call. From the candidate's perspective, they see and hear a lifelike AI interviewer in the video room — that's M2 making it happen.

**Responsibilities:**
- Join the video conference room as a named bot participant ("AI Interviewer")
- Capture the candidate's microphone audio continuously
- Detect when the candidate is speaking (VAD) and buffer those chunks
- Forward audio chunks to M3 for speech-to-text transcription
- Receive TTS audio from M3 and play it into the room
- Receive avatar video frames from M4 and broadcast them to the room
- Gracefully leave and release all media tracks when the session ends

# module 3: Speech Engine


M3 is the **speech bridge** of the system — it handles all conversion between audio and text in both directions.

**STT (Speech-to-Text):**
- Receives raw PCM audio chunks from M2 (candidate's microphone)
- Applies noise reduction and normalization
- Passes through OpenAI Whisper Large-v3 for transcription
- Returns a clean transcript with confidence score and timestamps

**TTS (Text-to-Speech):**
- Receives question text or response text from M1
- Synthesizes natural-sounding speech using Coqui XTTS-v2
- Applies audio post-processing (EQ, compression)
- Uploads the audio file to MinIO/S3
- Returns a signed `audio_url` and duration in milliseconds

# module 4: Avatar Engine

M4 generates **realistic talking avatar video** from audio input. Given a TTS audio file (from M3), it produces a video of a lifelike human face speaking those words — with correct lip movements, natural head motion, and facial expressions.

The output video stream is sent in real-time to M2, which injects it into the video conference room so the candidate sees the AI interviewer as a speaking, moving person.

**Key capabilities:**
- Lip-sync video generation from any audio input
- Full-face animation with head pose and expressions
- Real-time streaming to the meeting room
- Multiple avatar personas (different faces)
- Idle/listening animation between questions


# module 5: Question Intelligence Engine


M5 is the **intelligence behind the questions**. It doesn't use a static question bank — every question is **generated on the fly** by an LLM based on:
- The specific job role and required skills
- The candidate's resume and declared experience
- The candidate's performance on previous answers
- The remaining time in the interview

**Key features:**
- Generates a full question queue at the start of the session
- Delivers questions one at a time to M1 as requested
- Generates context-aware follow-up questions when M6 flags them
- Adapts difficulty up or down in real time
- Ensures no two interview sessions have identical questions

# module 6: Answer Evaluator


M6 is the **judge** of the system. After every candidate answer is transcribed by M3, M1 sends the question and answer to M6 for evaluation.

M6 performs:
- **5 quantitative NLP scores** (parallel processing)
- **1 LLM qualitative evaluation** (deep contextual analysis)
- **Weighted score aggregation** into a composite 0–100 score
- **Score band classification** (Excellent / Good / Average / Poor)
- **Structured feedback** (strengths, weaknesses, recommendation)
- **Follow-up decision** (should M5 generate a follow-up question?)
- **Live session score tracking** in Redis
- **Persistent result storage** in MongoDB


# module 7: Interview Report Generator



M7 is the **final output of the entire system**. When the session ends, M1 triggers M7 to compile everything into a polished, structured report that the recruiter can use to make a hiring decision.

**The report contains:**
- Complete session metadata (date, duration, job role, candidate name)
- Full score breakdown (overall + per-question + per-skill + per-dimension)
- Score trend analysis (did the candidate improve or decline?)
- Full timestamped transcript (every question and answer)
- AI-written executive summary
- Key strengths (3–5 specific bullets)
- Development areas (3–5 specific bullets)
- Skill gap analysis
- Hiring recommendation with confidence level

**Output formats:**
- `JSON` — for ATS/dashboard integration
- `PDF` — formatted, recruiter-ready report
- `Dashboard Charts` — radar chart, bar chart, score timeline
