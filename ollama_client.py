"""
Optional local Ollama integration for Tamil Analyzer v28.
No paid/live API is required. Defaults to localhost Ollama only.
"""
import json
import urllib.request
import urllib.error

DEFAULT_MODEL = "qwen2.5:7b-instruct"
DEFAULT_BASE_URL = "http://127.0.0.1:11434"


def _post_json(url, payload, timeout=120):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def ollama_health(base_url=DEFAULT_BASE_URL, timeout=4):
    try:
        with urllib.request.urlopen(base_url.rstrip("/") + "/api/tags", timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        models = [m.get("name", "") for m in data.get("models", [])]
        return {"available": True, "models": models, "error": None}
    except Exception as e:
        return {"available": False, "models": [], "error": str(e)}


def generate(prompt, model=DEFAULT_MODEL, base_url=DEFAULT_BASE_URL, temperature=0.2, timeout=180):
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": temperature,
            "top_p": 0.9,
            "num_ctx": 4096,
        },
    }
    result = _post_json(base_url.rstrip("/") + "/api/generate", payload, timeout=timeout)
    return (result.get("response") or "").strip()


def tamil_author_rewrite(text, target_grade=3, model=DEFAULT_MODEL, base_url=DEFAULT_BASE_URL):
    prompt = f"""நீங்கள் தமிழ் குழந்தைகள் புத்தக ஆசிரியருக்கு உதவும் ஆசிரியர்.
கீழே உள்ள உரையை {target_grade}ஆம் வகுப்பு மாணவர்களுக்கு ஏற்ற எளிய தமிழில் மாற்றுங்கள்.
விதிகள்:
- பொருள் மாறக்கூடாது
- குறுகிய வாக்கியங்கள் பயன்படுத்து
- கடினமான சொற்களுக்கு எளிய மாற்று பயன்படுத்து
- பதிலை தமிழில் மட்டும் கொடு

உரை:
{text}

எளிய வடிவம்:"""
    return generate(prompt, model=model, base_url=base_url, temperature=0.15)


def tamil_simple_explanation(text, target_grade=3, model=DEFAULT_MODEL, base_url=DEFAULT_BASE_URL):
    prompt = f"""இந்த கருத்தை {target_grade}ஆம் வகுப்பு மாணவர்களுக்கு புரியும் எளிய தமிழில் விளக்குங்கள்.
3-5 குறுகிய வரிகளில் எழுதுங்கள். தமிழில் மட்டும் பதிலளியுங்கள்.

கருத்து/உரை:
{text}

விளக்கம்:"""
    return generate(prompt, model=model, base_url=base_url, temperature=0.2)


def tamil_lesson_plan(words, concepts, target_grade=3, model=DEFAULT_MODEL, base_url=DEFAULT_BASE_URL):
    words_txt = ", ".join(words[:30]) if isinstance(words, list) else str(words)
    concepts_txt = ", ".join(concepts[:15]) if isinstance(concepts, list) else str(concepts)
    prompt = f"""நீங்கள் தமிழ் ஆசிரியர்.
{target_grade}ஆம் வகுப்பு மாணவர்களுக்கு ஒரு வாசிப்பு பாடத் திட்டம் உருவாக்குங்கள்.
வடிவம்:
1. வாசிப்புக்கு முன்
2. வாசிக்கும் போது
3. வாசித்த பின்
4. சொல்லகராதி செயல்பாடு

கடின சொற்கள்: {words_txt}
கருத்துகள்: {concepts_txt}

தமிழில் மட்டும், சுருக்கமாக பதிலளியுங்கள்."""
    return generate(prompt, model=model, base_url=base_url, temperature=0.25)


def tamil_questions(text, target_grade=3, count=5, model=DEFAULT_MODEL, base_url=DEFAULT_BASE_URL):
    prompt = f"""கீழே உள்ள தமிழ்ப் பகுதியை வைத்து {target_grade}ஆம் வகுப்பு மாணவர்களுக்கு {count} எளிய புரிதல் கேள்விகள் உருவாக்குங்கள்.
கேள்விகள் தமிழில் மட்டும் இருக்க வேண்டும்.

உரை:
{text}

கேள்விகள்:"""
    return generate(prompt, model=model, base_url=base_url, temperature=0.25)
