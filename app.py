import os
import sys
import json
import time
import subprocess
import streamlit as st
import whisper
import static_ffmpeg
import yt_dlp
from google import genai
from google.genai.errors import ServerError
from gtts import gTTS

static_ffmpeg.add_paths()

API_KEY = st.secrets.get("GEMINI_API_KEY", os.environ.get("GEMINI_API_KEY", ""))
OUTPUT_DIR = "dubbed_viral_clips"
TEMP_AUDIO = "temp_full_audio.mp3"

os.makedirs(OUTPUT_DIR, exist_ok=True)

st.set_page_config(page_title="Clipper AI - Fast Stream", page_icon="⚡", layout="wide")

st.markdown("""
<style>
    .stApp { background-color: #0d0e15; color: #e2e8f0; font-family: 'Inter', sans-serif; }
    .hero-container { text-align: center; padding: 2rem 1rem 1rem 1rem; }
    .hero-title { font-size: 3rem; font-weight: 800; background: linear-gradient(135deg, #a855f7 0%, #6366f1 50%, #3b82f6 100%); -webkit-background-clip: text; -webkit-text-fill-color: transparent; }
    .hero-subtitle { color: #94a3b8; font-size: 1.1rem; margin-bottom: 2rem; }
    .clip-card { background: #181924; border: 1px solid #2e3146; border-radius: 16px; padding: 20px; margin-bottom: 25px; }
    .score-badge { display: inline-block; background: linear-gradient(135deg, #22c55e 0%, #16a34a 100%); color: #ffffff; font-weight: 700; padding: 6px 14px; border-radius: 20px; font-size: 0.9rem; margin-bottom: 12px; }
    .stButton>button { width: 100%; background: linear-gradient(135deg, #6366f1 0%, #a855f7 100%) !important; color: white !important; font-weight: 700 !important; border: none !important; padding: 12px 24px !important; border-radius: 12px !important; }
    .stTextInput input { background-color: #13141f !important; border: 1px solid #2e3146 !important; color: #ffffff !important; border-radius: 12px !important; padding: 14px !important; }
    #MainMenu {visibility: hidden;} footer {visibility: hidden;}
</style>
""", unsafe_allow_html=True)

st.markdown("""
<div class="hero-container">
    <div class="hero-title">Cloud Video Clipper AI</div>
    <div class="hero-subtitle">Fast partial streaming: Analyzes audio only, then streams exact clips.</div>
</div>
""", unsafe_allow_html=True)

col_left, col_mid, col_right = st.columns([1, 3, 1])

with col_mid:
    video_url = st.text_input("", placeholder="Paste YouTube link here...")
    generate_btn = st.button("✨ Extract Clips (Stream Mode)", type="primary")

if generate_btn:
    if not video_url:
        st.error("Please enter a YouTube video URL first.")
    else:
        status = st.status("🚀 Processing stream...", expanded=True)
        
        # 1. Download Low-Bandwidth Audio Stream Only
        status.write("⚡ **1/4 Extracting light audio stream...**")
        if os.path.exists(TEMP_AUDIO):
            os.remove(TEMP_AUDIO)
            
        ydl_opts_audio = {
            'format': 'bestaudio/best',
            'outtmpl': 'temp_full_audio',
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }],
            'quiet': True
        }
        with yt_dlp.YoutubeDL(ydl_opts_audio) as ydl:
            ydl.download([video_url])

        # 2. Transcribe Audio Only
        status.write("🎙️ **2/4 Transcribing audio with Whisper...**")
        whisper_model = whisper.load_model("base")
        result = whisper_model.transcribe(TEMP_AUDIO, fp16=False)
        segments = result.get("segments", [])

        if not segments:
            status.update(label="Failed!", state="error")
            st.error("No clear audio or speech detected.")
            st.stop()

        formatted_transcript = [
            {"start": round(s["start"], 2), "end": round(s["end"], 2), "text": s["text"].strip()}
            for s in segments
        ]

        # 3. Analyze Viral Moments with Gemini
        status.write("🧠 **3/4 Finding viral hooks with Gemini...**")
        prompt = f"""
        You are an elite video editor. Analyze this transcript and select 2-3 viral hooks.
        Clips MUST be between 15 and 60 seconds long.
        Assign a 'score' (between 85 and 99).

        Return ONLY a raw JSON array:
        [
          {{
            "title": "Short Punchy Title",
            "score": 95,
            "start": 12.5,
            "end": 45.0,
            "reason": "Why this works."
          }}
        ]

        Transcript:
        {json.dumps(formatted_transcript)}
        """

        client = genai.Client(api_key=API_KEY)
        for attempt in range(3):
            try:
                response = client.models.generate_content(
                    model="gemini-3.6-flash",
                    contents=prompt,
                    config={"automatic_function_calling": {"disable": True}}
                )
                break
            except ServerError:
                time.sleep(3)

        raw_json = response.text.strip().replace("```json", "").replace("```", "").strip()
        selected_clips = json.loads(raw_json)

        # 4. Download ONLY the Selected Time Ranges
        status.write("✂️ **4/4 Streaming exact clip segments & dubbing...**")
        rendered_files = []

        for idx, clip in enumerate(selected_clips, 1):
            start, end = clip["start"], clip["end"]
            title = clip.get("title", f"Clip_{idx}")
            score = clip.get("score", 90)

            clip_text = " ".join([s["text"] for s in formatted_transcript if s["start"] >= start and s["end"] <= end]).strip()
            if not clip_text:
                clip_text = " ".join([s["text"] for s in formatted_transcript])

            # Translate
            trans_res = client.models.generate_content(
                model="gemini-3.6-flash",
                contents=f"Translate to Spanish. Output ONLY raw translation: {clip_text}",
                config={"automatic_function_calling": {"disable": True}}
            )
            spanish_text = trans_res.text.strip() if trans_res.text else ""

            if not spanish_text:
                continue

            clean_title = title.replace(" ", "_").replace("/", "_")
            raw_section_video = os.path.join(OUTPUT_DIR, f"section_{idx}.mp4")
            spanish_audio_path = os.path.join(OUTPUT_DIR, f"spanish_{idx}.mp3")
            final_video = os.path.join(OUTPUT_DIR, f"dubbed_{idx}_{clean_title}.mp4")

            # Stream ONLY the clip section from YouTube
            time_range = f"*{start}-{end}"
            section_opts = {
                'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
                'download_sections': [time_range],
                'outtmpl': raw_section_video,
                'quiet': True,
                'force_keyframes_at_cuts': True
            }
            with yt_dlp.YoutubeDL(section_opts) as ydl:
                ydl.download([video_url])

            # Generate Spanish TTS
            tts = gTTS(text=spanish_text, lang='es')
            tts.save(spanish_audio_path)

            # Combine Section Video + Spanish Audio
            subprocess.run([
                "ffmpeg", "-y", "-i", raw_section_video, "-i", spanish_audio_path,
                "-c:v", "copy", "-c:a", "aac", "-map", "0:v:0", "-map", "1:a:0",
                "-shortest", final_video
            ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

            # Clean temporary files
            if os.path.exists(raw_section_video): os.remove(raw_section_video)
            if os.path.exists(spanish_audio_path): os.remove(spanish_audio_path)

            rendered_files.append({
                "title": title,
                "score": score,
                "reason": clip.get("reason", ""),
                "spanish_text": spanish_text,
                "file_path": final_video
            })

        if os.path.exists(TEMP_AUDIO): os.remove(TEMP_AUDIO)
        status.update(label="Complete!", state="complete")
        st.balloons()

        # Render Clips
        st.write("---")
        st.subheader("🔥 Generated Clips")

        for item in rendered_files:
            st.markdown(f"""
            <div class="clip-card">
                <span class="score-badge">Viral Score: {item['score']}/100</span>
                <h2 style="margin-top: 5px; color: #ffffff;">{item['title']}</h2>
                <p style="color: #94a3b8;"><b>Why it works:</b> {item['reason']}</p>
            </div>
            """, unsafe_allow_html=True)
            
            v_col, t_col = st.columns([1, 1])
            with v_col:
                st.video(item["file_path"])
                with open(item["file_path"], "rb") as f:
                    st.download_button(
                        label=f"⬇️ Download {item['title']}.mp4",
                        data=f,
                        file_name=os.path.basename(item["file_path"]),
                        mime="video/mp4",
                        key=item["file_path"]
                    )
            with t_col:
                st.markdown("**Spanish Voiceover Script:**")
                st.info(item["spanish_text"])
